"""
ALPHA HUNTER — Phase 2: Scanner
Finds every NYSE/NASDAQ stock that returned 500%+ in a single calendar year (2015-2025)
with market cap >= $500M at start of year.

Optimized: downloads full 2015-2025 history per ticker in ONE call,
and fetches ticker info only when a potential winner is found.
"""

import sqlite3
import os
import sys
import time
import logging
import argparse
from datetime import datetime
from pathlib import Path

import yfinance as yf
import pandas as pd
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "db.sqlite"
LOG_PATH = DATA_DIR / "errors.log"
CHECKPOINT_PATH = DATA_DIR / "scanner_checkpoint.txt"

FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FMP_BASE = "https://financialmodelingprep.com/api/v3"

MIN_RETURN_PCT = 500
MIN_MARKET_CAP = 500_000_000
YEARS = list(range(2015, 2026))
BATCH_SIZE = 50
REQUEST_DELAY = 0.3

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("scanner")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tickers (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            exchange TEXT,
            sector TEXT,
            industry TEXT
        );
        CREATE TABLE IF NOT EXISTS winners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            year INTEGER NOT NULL,
            ytd_return REAL,
            jan_price REAL,
            dec_price REAL,
            market_cap REAL,
            sector TEXT,
            industry TEXT,
            UNIQUE(symbol, year)
        );
        CREATE TABLE IF NOT EXISTS analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            year INTEGER NOT NULL,
            param TEXT NOT NULL,
            value TEXT,
            UNIQUE(symbol, year, param)
        );
        CREATE TABLE IF NOT EXISTS scan_done (
            symbol TEXT PRIMARY KEY
        );
    """)
    conn.commit()

# ---------------------------------------------------------------------------
# Step 1: Fetch ticker list from FMP (1 API call)
# ---------------------------------------------------------------------------

def fetch_tickers_fmp(conn: sqlite3.Connection) -> list[str]:
    """Download full NYSE + NASDAQ ticker list from FMP."""
    existing = conn.execute("SELECT COUNT(*) FROM tickers").fetchone()[0]
    if existing > 0:
        log.info(f"Tickers table already has {existing} rows, skipping FMP download.")
        rows = conn.execute("SELECT symbol FROM tickers").fetchall()
        return [r[0] for r in rows]

    if not FMP_API_KEY or FMP_API_KEY == "your_key_here":
        log.warning("No FMP API key set. Falling back to yfinance-based ticker list.")
        return fetch_tickers_fallback(conn)

    url = f"{FMP_BASE}/stock/list?apikey={FMP_API_KEY}"
    log.info("Fetching ticker list from FMP (1 API call)...")
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.error(f"FMP ticker fetch failed: {e}. Falling back.")
        return fetch_tickers_fallback(conn)

    symbols = []
    for item in data:
        exch = (item.get("exchangeShortName") or "").upper()
        if exch not in ("NYSE", "NASDAQ"):
            continue
        sym = item.get("symbol", "")
        # Skip preferred shares, warrants, units, etc.
        if not sym or "." in sym or "-" in sym or len(sym) > 5:
            continue
        # Skip if type indicates non-stock
        item_type = (item.get("type") or "").lower()
        if item_type and item_type not in ("stock", "common stock", ""):
            continue

        name = item.get("name", "")
        conn.execute(
            "INSERT OR IGNORE INTO tickers VALUES (?,?,?,?,?)",
            (sym, name, exch, "", ""),
        )
        symbols.append(sym)

    conn.commit()
    log.info(f"Saved {len(symbols)} tickers (NYSE + NASDAQ) to DB.")
    return symbols


def fetch_tickers_fallback(conn: sqlite3.Connection) -> list[str]:
    """Fallback: S&P 500 + NASDAQ-100 + Russell 1000 + known explosive movers."""
    log.info("Fetching ticker lists from Wikipedia...")
    symbols = set()
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AlphaHunter/1.0"}

    # S&P 500
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                              storage_options={"User-Agent": headers["User-Agent"]})
        sp500 = tables[0]
        for _, row in sp500.iterrows():
            sym = str(row["Symbol"]).replace(".", "-").strip()
            symbols.add(sym)
            conn.execute(
                "INSERT OR IGNORE INTO tickers VALUES (?,?,?,?,?)",
                (sym, str(row.get("Security", "")), "NYSE/NASDAQ",
                 str(row.get("GICS Sector", "")), str(row.get("GICS Sub-Industry", ""))),
            )
        log.info(f"  S&P 500: added {len(sp500)} tickers")
    except Exception as e:
        log.error(f"Failed to fetch S&P 500 list: {e}")

    # NASDAQ-100
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100",
                              storage_options={"User-Agent": headers["User-Agent"]})
        for tbl in tables:
            if "Ticker" in tbl.columns:
                for _, row in tbl.iterrows():
                    sym = str(row["Ticker"]).strip()
                    if sym and len(sym) <= 5:
                        symbols.add(sym)
                        conn.execute(
                            "INSERT OR IGNORE INTO tickers VALUES (?,?,?,?,?)",
                            (sym, str(row.get("Company", "")), "NASDAQ", "", ""),
                        )
                log.info(f"  NASDAQ-100: found {len(tbl)} tickers")
                break
    except Exception as e:
        log.error(f"Failed to fetch NASDAQ-100 list: {e}")

    # Russell 1000 — more mid-cap coverage
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/Russell_1000_Index",
                              storage_options={"User-Agent": headers["User-Agent"]})
        for tbl in tables:
            if "Ticker" in tbl.columns or "Symbol" in tbl.columns:
                col = "Ticker" if "Ticker" in tbl.columns else "Symbol"
                for _, row in tbl.iterrows():
                    sym = str(row[col]).strip()
                    if sym and len(sym) <= 5 and "." not in sym:
                        symbols.add(sym)
                        conn.execute(
                            "INSERT OR IGNORE INTO tickers VALUES (?,?,?,?,?)",
                            (sym, str(row.get("Company", "")), "NYSE/NASDAQ", "", ""),
                        )
                log.info(f"  Russell 1000: found {len(tbl)} tickers")
                break
    except Exception as e:
        log.error(f"Failed to fetch Russell 1000 list: {e}")

    # Known explosive movers + mid/small caps that had big runs
    extras = [
        # Mega-cap tech
        "NVDA", "TSLA", "AMD", "AAPL", "MSFT", "GOOG", "GOOGL", "AMZN", "META",
        # High-growth tech
        "SMCI", "PLTR", "MSTR", "COIN", "HOOD", "SOFI", "TTD", "ROKU", "ZM",
        "PTON", "SQ", "SHOP", "MELI", "SE", "NET", "CRWD", "DDOG", "ZS",
        "MDB", "SNOW", "AI", "PATH", "UPST", "AFRM", "DKNG", "DASH", "ABNB",
        "RBLX", "U", "PINS", "SNAP",
        # Meme / short squeeze
        "GME", "AMC", "SPCE", "BBBY", "CLOV", "WISH", "IRNT", "DWAC",
        # EV / clean energy
        "LCID", "RIVN", "QS", "CHPT", "NKLA", "LAZR", "GOEV", "FSR",
        "PLUG", "FCEL", "BLNK", "WKHS", "HYLN", "RIDE",
        "ENPH", "SEDG", "FSLR", "RUN", "NOVA", "MAXN", "ARRY", "STEM",
        "GEVO", "CLNE", "BE",
        # Crypto / blockchain
        "MARA", "RIOT", "MSTR", "COIN", "HUT", "BTBT", "BITF", "CLSK",
        # Biotech / pharma
        "MRNA", "BNTX", "NVAX", "VXRT", "INO", "CODX", "APT", "LAKE",
        "SAVA", "CORT", "AXSM", "ITCI", "HIMS", "VRNA",
        # AI / quantum / space
        "IONQ", "RKLB", "ASTS", "SOUN", "APLD", "AEHR", "BBAI",
        "RGTI", "QBTS", "LUNR", "RDW",
        # Retail / consumer
        "CVNA", "DDS", "EXPE", "ETSY", "W", "PENN", "BROS",
        "CELH", "DUOL", "TOST",
        # China ADRs that had big moves
        "NIO", "XPEV", "LI", "BABA", "JD", "PDD", "FUTU", "BILI",
        # Misc big movers
        "MP", "ALB", "ANET", "DECK", "AXON", "TRGP", "VST", "CEG",
        "FICO", "GEV", "TLN", "GDDY", "APP", "FOUR", "PAYO",
        "RELY", "DLO", "BIGC", "OPEN", "LMND",
        # Industrials / defense
        "GE", "BA", "LMT", "NOC", "RTX", "HWM", "TDG", "KTOS",
        # Financials
        "AFRM", "UPST", "LC", "SOFI", "NU",
        # Healthcare
        "HIMS", "GDRX", "DOCS", "OSCR",
    ]
    for sym in extras:
        symbols.add(sym)
        conn.execute("INSERT OR IGNORE INTO tickers VALUES (?,?,?,?,?)",
                     (sym, "", "NYSE/NASDAQ", "", ""))

    conn.commit()
    log.info(f"Saved {len(symbols)} tickers (fallback mode).")
    return list(symbols)

# ---------------------------------------------------------------------------
# Step 2: Scan — download full history per ticker, check all years at once
# ---------------------------------------------------------------------------

def scan_ticker(symbol: str, conn: sqlite3.Connection) -> list[dict]:
    """Download 2014-2025 history in ONE yfinance call.
    Check each year for 500%+ return. Only fetch .info if potential winner found."""
    winners = []

    try:
        ticker = yf.Ticker(symbol)
        # Get full history 2014-01-01 to 2025-12-31 (2014 for prior year context)
        hist = ticker.history(start="2014-01-01", end="2025-12-31", auto_adjust=True)
        if hist.empty or len(hist) < 50:
            return []

        # Check each year
        potential_winners = []
        for year in YEARS:
            year_data = hist[hist.index.year == year]
            if len(year_data) < 20:
                continue

            jan_price = year_data["Close"].iloc[0]
            dec_price = year_data["Close"].iloc[-1]

            if jan_price <= 0:
                continue

            ytd_return = (dec_price - jan_price) / jan_price * 100

            if ytd_return >= MIN_RETURN_PCT:
                potential_winners.append({
                    "year": year,
                    "ytd_return": round(ytd_return, 2),
                    "jan_price": round(jan_price, 4),
                    "dec_price": round(dec_price, 4),
                })

        if not potential_winners:
            return []

        # Only now fetch .info (expensive call) — we have a potential winner
        info = ticker.info or {}
        market_cap = info.get("marketCap")
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        shares = info.get("sharesOutstanding")
        sector = info.get("sector", "")
        industry = info.get("industry", "")

        for pw in potential_winners:
            # Estimate market cap at start of that year
            if shares:
                mcap_at_jan = shares * pw["jan_price"]
            elif market_cap and current_price and current_price > 0:
                est_shares = market_cap / current_price
                mcap_at_jan = est_shares * pw["jan_price"]
            else:
                mcap_at_jan = 0

            if mcap_at_jan < MIN_MARKET_CAP:
                log.debug(f"  {symbol}/{pw['year']}: +{pw['ytd_return']}% but MCap ${mcap_at_jan:,.0f} < $500M, skipping")
                continue

            winner = {
                "symbol": symbol,
                "year": pw["year"],
                "ytd_return": pw["ytd_return"],
                "jan_price": pw["jan_price"],
                "dec_price": pw["dec_price"],
                "market_cap": round(mcap_at_jan),
                "sector": sector,
                "industry": industry,
            }
            winners.append(winner)

    except Exception as e:
        log.error(f"Error scanning {symbol}: {e}")

    return winners


def save_winner(conn: sqlite3.Connection, w: dict):
    conn.execute(
        """INSERT OR REPLACE INTO winners
           (symbol, year, ytd_return, jan_price, dec_price, market_cap, sector, industry)
           VALUES (?,?,?,?,?,?,?,?)""",
        (w["symbol"], w["year"], w["ytd_return"], w["jan_price"],
         w["dec_price"], w["market_cap"], w["sector"], w["industry"]),
    )


def update_ticker_info(conn: sqlite3.Connection, symbol: str, sector: str, industry: str):
    if sector or industry:
        conn.execute(
            "UPDATE tickers SET sector=?, industry=? WHERE symbol=? AND (sector='' OR sector IS NULL)",
            (sector, industry, symbol),
        )

# ---------------------------------------------------------------------------
# Main scan loop
# ---------------------------------------------------------------------------

def run_scan(resume: bool = True):
    conn = sqlite3.connect(str(DB_PATH))
    init_db(conn)

    # Step 1: get tickers
    symbols = fetch_tickers_fmp(conn)
    total = len(symbols)
    log.info(f"Total tickers to scan: {total}")

    # Get already-scanned tickers for resume
    if resume:
        done = set(r[0] for r in conn.execute("SELECT symbol FROM scan_done").fetchall())
        log.info(f"Already scanned: {len(done)} tickers. Remaining: {total - len(done)}")
    else:
        done = set()

    total_winners = conn.execute("SELECT COUNT(*) FROM winners").fetchone()[0]
    log.info(f"Winners found so far: {total_winners}")

    scanned = 0
    for i, symbol in enumerate(symbols, 1):
        if symbol in done:
            continue

        winners = scan_ticker(symbol, conn)
        for w in winners:
            save_winner(conn, w)
            update_ticker_info(conn, symbol, w.get("sector", ""), w.get("industry", ""))
            total_winners += 1
            log.info(
                f"*** WINNER: {w['symbol']} in {w['year']} — "
                f"+{w['ytd_return']}% | "
                f"MCap ${w['market_cap']:,.0f} ***"
            )

        conn.execute("INSERT OR IGNORE INTO scan_done VALUES (?)", (symbol,))
        scanned += 1

        # Commit + progress every 50 tickers
        if scanned % BATCH_SIZE == 0:
            conn.commit()
            log.info(f"Progress: {i}/{total} tickers | Scanned this run: {scanned} | Total winners: {total_winners}")

        # Checkpoint every 500
        if scanned % 500 == 0:
            conn.commit()
            with open(CHECKPOINT_PATH, "w") as f:
                f.write(f"{scanned},{datetime.now().isoformat()}\n")
            log.info(f"--- Checkpoint saved at {scanned} tickers ---")

        time.sleep(REQUEST_DELAY)

    conn.commit()

    # Final summary
    log.info("=" * 60)
    log.info(f"SCAN COMPLETE. Scanned {scanned} new tickers. Total winners: {total_winners}")
    log.info("=" * 60)

    # Print winners by year
    for y in YEARS:
        count = conn.execute("SELECT COUNT(*) FROM winners WHERE year=?", (y,)).fetchone()[0]
        if count > 0:
            log.info(f"  {y}: {count} winners")
            rows = conn.execute(
                "SELECT symbol, ytd_return, market_cap FROM winners WHERE year=? ORDER BY ytd_return DESC",
                (y,)
            ).fetchall()
            for sym, ret, mc in rows:
                log.info(f"    {sym}: +{ret}% (MCap ${mc:,.0f})")

    conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ALPHA HUNTER Scanner")
    parser.add_argument("--resume", action="store_true", default=True,
                        help="Resume from last checkpoint (default: True)")
    parser.add_argument("--fresh", action="store_true",
                        help="Start fresh scan (ignore previous progress)")
    args = parser.parse_args()

    resume = not args.fresh
    log.info(f"Starting ALPHA HUNTER scanner (resume={resume})")
    run_scan(resume=resume)
