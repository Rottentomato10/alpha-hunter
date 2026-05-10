"""
ALPHA HUNTER v2 — Scanner with Rolling 252-day Windows
Instead of calendar year, scans every possible 252-trading-day window
for 500%+ returns. Catches moves that span across years.
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

FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FMP_STABLE = "https://financialmodelingprep.com/stable"

MIN_RETURN_PCT = 500
MIN_MARKET_CAP = 500_000_000
WINDOW_DAYS = 252  # Trading days in a year
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
log = logging.getLogger("scanner_v2")

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
        CREATE TABLE IF NOT EXISTS winners_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            start_price REAL,
            end_price REAL,
            return_pct REAL,
            market_cap_at_start REAL,
            sector TEXT,
            industry TEXT,
            peak_date TEXT,
            peak_price REAL,
            peak_return_pct REAL,
            UNIQUE(symbol, start_date)
        );
        CREATE TABLE IF NOT EXISTS analysis_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            start_date TEXT NOT NULL,
            param TEXT NOT NULL,
            value TEXT,
            UNIQUE(symbol, start_date, param)
        );
        CREATE TABLE IF NOT EXISTS breakouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            start_date TEXT NOT NULL,
            breakout_date TEXT,
            breakout_type TEXT,
            breakout_price REAL,
            volume_ratio REAL,
            description TEXT,
            UNIQUE(symbol, start_date, breakout_date, breakout_type)
        );
        CREATE TABLE IF NOT EXISTS scan_done_v2 (
            symbol TEXT PRIMARY KEY
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Ticker list (reuse from existing DB or fetch)
# ---------------------------------------------------------------------------

def get_tickers(conn: sqlite3.Connection) -> list[str]:
    """Get tickers from existing DB or fetch fresh."""
    rows = conn.execute("SELECT symbol FROM tickers").fetchall()
    if rows:
        log.info(f"Using {len(rows)} tickers from existing DB.")
        return [r[0] for r in rows]

    # Fetch from Wikipedia + extras
    log.info("Fetching ticker lists...")
    headers = {"User-Agent": "Mozilla/5.0 AlphaHunter/2.0"}
    symbols = set()

    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                              storage_options={"User-Agent": headers["User-Agent"]})
        for _, row in tables[0].iterrows():
            sym = str(row["Symbol"]).replace(".", "-").strip()
            symbols.add(sym)
            conn.execute("INSERT OR IGNORE INTO tickers VALUES (?,?,?,?,?)",
                         (sym, str(row.get("Security", "")), "NYSE/NASDAQ",
                          str(row.get("GICS Sector", "")), str(row.get("GICS Sub-Industry", ""))))
    except Exception as e:
        log.error(f"S&P 500 fetch failed: {e}")

    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/Russell_1000_Index",
                              storage_options={"User-Agent": headers["User-Agent"]})
        for tbl in tables:
            col = "Ticker" if "Ticker" in tbl.columns else "Symbol" if "Symbol" in tbl.columns else None
            if col:
                for _, row in tbl.iterrows():
                    sym = str(row[col]).strip()
                    if sym and len(sym) <= 5 and "." not in sym:
                        symbols.add(sym)
                        conn.execute("INSERT OR IGNORE INTO tickers VALUES (?,?,?,?,?)",
                                     (sym, "", "NYSE/NASDAQ", "", ""))
                break
    except Exception as e:
        log.error(f"Russell 1000 fetch failed: {e}")

    # Extras
    extras = [
        "NVDA", "TSLA", "AMD", "SMCI", "PLTR", "MSTR", "COIN", "HOOD", "SOFI",
        "PLUG", "FCEL", "BLNK", "GME", "AMC", "SPCE", "LCID", "RIVN", "QS",
        "ENPH", "SEDG", "FSLR", "MARA", "RIOT", "CLSK", "BITF",
        "MRNA", "BNTX", "NVAX", "IONQ", "RKLB", "ASTS", "SOUN", "APLD",
        "CVNA", "APP", "UPST", "AFRM", "DKNG", "NIO", "XPEV", "LI",
        "BBBY", "WKHS", "CLOV", "HIMS", "DUOL", "CELH",
        "MP", "ALB", "AXON", "VST", "CEG", "GEV", "SNDK", "SMMT",
        "QXO", "RGTI", "QBTS", "LUNR", "BBAI",
    ]
    for sym in extras:
        symbols.add(sym)
        conn.execute("INSERT OR IGNORE INTO tickers VALUES (?,?,?,?,?)",
                     (sym, "", "NYSE/NASDAQ", "", ""))

    conn.commit()
    log.info(f"Total: {len(symbols)} tickers")
    return list(symbols)


# ---------------------------------------------------------------------------
# Rolling window scan
# ---------------------------------------------------------------------------

def scan_ticker_rolling(symbol: str, conn: sqlite3.Connection) -> list[dict]:
    """Scan ticker for any 252-trading-day window with 500%+ return."""
    winners = []

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(start="2014-01-01", end="2025-12-31", auto_adjust=True)
        if hist.empty or len(hist) < WINDOW_DAYS + 20:
            return []

        closes = hist["Close"]
        dates = hist.index

        # Slide a 252-day window across the data
        best_windows = []  # Track non-overlapping best windows

        for i in range(len(closes) - WINDOW_DAYS):
            start_price = closes.iloc[i]
            end_price = closes.iloc[i + WINDOW_DAYS]

            if start_price <= 0:
                continue

            ret = (end_price - start_price) / start_price * 100

            if ret >= MIN_RETURN_PCT:
                start_date = dates[i].strftime("%Y-%m-%d")
                end_date = dates[i + WINDOW_DAYS].strftime("%Y-%m-%d")

                # Find peak within this window
                window_slice = closes.iloc[i:i + WINDOW_DAYS + 1]
                peak_idx = window_slice.idxmax()
                peak_price = window_slice.max()
                peak_return = (peak_price - start_price) / start_price * 100

                best_windows.append({
                    "start_idx": i,
                    "start_date": start_date,
                    "end_date": end_date,
                    "start_price": round(start_price, 4),
                    "end_price": round(end_price, 4),
                    "return_pct": round(ret, 2),
                    "peak_date": peak_idx.strftime("%Y-%m-%d"),
                    "peak_price": round(peak_price, 4),
                    "peak_return_pct": round(peak_return, 2),
                })

        if not best_windows:
            return []

        # Keep only non-overlapping windows (best return per ~6-month block)
        best_windows.sort(key=lambda x: x["return_pct"], reverse=True)
        selected = []
        used_ranges = []

        for w in best_windows:
            overlaps = False
            for ur in used_ranges:
                if not (w["start_idx"] + WINDOW_DAYS < ur[0] or w["start_idx"] > ur[1]):
                    overlaps = True
                    break
            if not overlaps:
                selected.append(w)
                used_ranges.append((w["start_idx"], w["start_idx"] + WINDOW_DAYS))

        if not selected:
            return []

        # Get market cap info (only once per ticker)
        info = ticker.info or {}
        market_cap = info.get("marketCap")
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        shares = info.get("sharesOutstanding")
        sector = info.get("sector", "")
        industry = info.get("industry", "")

        for w in selected:
            # Estimate market cap at start
            if shares:
                mcap = shares * w["start_price"]
            elif market_cap and current_price and current_price > 0:
                mcap = (market_cap / current_price) * w["start_price"]
            else:
                mcap = 0

            if mcap < MIN_MARKET_CAP:
                continue

            w["market_cap"] = round(mcap)
            w["sector"] = sector
            w["industry"] = industry
            w["symbol"] = symbol
            winners.append(w)

    except Exception as e:
        log.error(f"Error scanning {symbol}: {e}")

    return winners


def save_winner_v2(conn: sqlite3.Connection, w: dict):
    conn.execute(
        """INSERT OR REPLACE INTO winners_v2
           (symbol, start_date, end_date, start_price, end_price, return_pct,
            market_cap_at_start, sector, industry, peak_date, peak_price, peak_return_pct)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (w["symbol"], w["start_date"], w["end_date"], w["start_price"], w["end_price"],
         w["return_pct"], w["market_cap"], w["sector"], w["industry"],
         w["peak_date"], w["peak_price"], w["peak_return_pct"]),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_scan(resume: bool = True):
    conn = sqlite3.connect(str(DB_PATH))
    init_db(conn)

    symbols = get_tickers(conn)
    total = len(symbols)

    done = set()
    if resume:
        done = set(r[0] for r in conn.execute("SELECT symbol FROM scan_done_v2").fetchall())
        log.info(f"Resuming: {len(done)} already scanned, {total - len(done)} remaining")

    total_winners = conn.execute("SELECT COUNT(*) FROM winners_v2").fetchone()[0]
    scanned = 0

    for i, symbol in enumerate(symbols, 1):
        if symbol in done:
            continue

        winners = scan_ticker_rolling(symbol, conn)
        for w in winners:
            save_winner_v2(conn, w)
            total_winners += 1
            log.info(
                f"*** WINNER: {w['symbol']} | {w['start_date']} -> {w['end_date']} | "
                f"+{w['return_pct']}% (peak +{w['peak_return_pct']}%) | MCap ${w['market_cap']:,.0f} ***"
            )

        conn.execute("INSERT OR IGNORE INTO scan_done_v2 VALUES (?)", (symbol,))
        scanned += 1

        if scanned % 50 == 0:
            conn.commit()
            log.info(f"Progress: {i}/{total} | Scanned: {scanned} | Winners: {total_winners}")

        time.sleep(REQUEST_DELAY)

    conn.commit()

    # Summary
    log.info("=" * 60)
    log.info(f"V2 SCAN COMPLETE. Winners: {total_winners}")
    log.info("=" * 60)

    rows = conn.execute(
        "SELECT symbol, start_date, end_date, return_pct, peak_return_pct, market_cap_at_start "
        "FROM winners_v2 ORDER BY return_pct DESC"
    ).fetchall()
    for r in rows:
        log.info(f"  {r[0]}: {r[1]} -> {r[2]} | +{r[3]}% (peak +{r[4]}%) | MCap ${r[5]:,.0f}")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ALPHA HUNTER v2 Scanner (Rolling Window)")
    parser.add_argument("--fresh", action="store_true", help="Start fresh")
    args = parser.parse_args()
    run_scan(resume=not args.fresh)
