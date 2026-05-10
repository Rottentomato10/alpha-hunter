"""
ALPHA HUNTER — Expand Universe + Sensitivity Analysis
Task 1: Get full NYSE + NASDAQ ticker list (4,000-6,000 tickers)
Task 2: Test filter relaxations on precision vs signal frequency
"""

import sqlite3
import sys
import json
import time
import logging
from pathlib import Path

import yfinance as yf
import pandas as pd
import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cache.price_cache import load_all

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "db.sqlite"
FUND_PATH = DATA_DIR / "cache" / "fundamentals.json"
TICKERS_CSV = DATA_DIR / "tickers_full.csv"
CACHE_DIR = BASE_DIR / "cache" / "prices"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("expand")


# =========================================================================
# TASK 1 — Expand Universe
# =========================================================================

def fetch_full_universe():
    """Fetch all NYSE + NASDAQ tickers with market cap > $300M.
    Uses multiple sources for coverage."""
    log.info("TASK 1 — Expanding ticker universe")
    log.info("=" * 60)

    all_tickers = {}  # symbol -> {name, exchange, mcap}
    headers = {"User-Agent": "Mozilla/5.0 AlphaHunter/3.0"}

    # Source 1: Wikipedia S&P 500
    log.info("\nSource 1: S&P 500...")
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                              storage_options={"User-Agent": headers["User-Agent"]})
        for _, row in tables[0].iterrows():
            sym = str(row["Symbol"]).replace(".", "-").strip()
            if len(sym) <= 5 and sym.isalpha():
                all_tickers[sym] = {"name": str(row.get("Security", "")),
                                    "exchange": "NYSE/NASDAQ",
                                    "sector": str(row.get("GICS Sector", ""))}
        log.info(f"  Added {len(all_tickers)} from S&P 500")
    except Exception as e:
        log.error(f"  S&P 500 failed: {e}")

    # Source 2: Russell 1000
    log.info("Source 2: Russell 1000...")
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/Russell_1000_Index",
                              storage_options={"User-Agent": headers["User-Agent"]})
        added = 0
        for tbl in tables:
            col = "Ticker" if "Ticker" in tbl.columns else "Symbol" if "Symbol" in tbl.columns else None
            if col:
                for _, row in tbl.iterrows():
                    sym = str(row[col]).strip()
                    if sym and len(sym) <= 5 and sym.isalpha() and sym not in all_tickers:
                        all_tickers[sym] = {"name": str(row.get("Company", "")),
                                            "exchange": "NYSE/NASDAQ", "sector": ""}
                        added += 1
                break
        log.info(f"  Added {added} new tickers")
    except Exception as e:
        log.error(f"  Russell 1000 failed: {e}")

    # Source 3: Russell 2000 (small caps — where runners hide)
    log.info("Source 3: Russell 2000...")
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/Russell_2000_Index",
                              storage_options={"User-Agent": headers["User-Agent"]})
        added = 0
        for tbl in tables:
            col = "Ticker" if "Ticker" in tbl.columns else "Symbol" if "Symbol" in tbl.columns else None
            if col:
                for _, row in tbl.iterrows():
                    sym = str(row[col]).strip().upper()
                    if sym and len(sym) <= 5 and sym.isalpha() and sym not in all_tickers:
                        all_tickers[sym] = {"name": str(row.get("Company", "")),
                                            "exchange": "NYSE/NASDAQ", "sector": ""}
                        added += 1
                break
        log.info(f"  Added {added} new tickers")
    except Exception as e:
        log.error(f"  Russell 2000 failed: {e}")

    # Source 4: NASDAQ-100
    log.info("Source 4: NASDAQ-100...")
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100",
                              storage_options={"User-Agent": headers["User-Agent"]})
        added = 0
        for tbl in tables:
            if "Ticker" in tbl.columns:
                for _, row in tbl.iterrows():
                    sym = str(row["Ticker"]).strip()
                    if sym and len(sym) <= 5 and sym not in all_tickers:
                        all_tickers[sym] = {"name": str(row.get("Company", "")),
                                            "exchange": "NASDAQ", "sector": ""}
                        added += 1
                break
        log.info(f"  Added {added} new tickers")
    except Exception as e:
        log.error(f"  NASDAQ-100 failed: {e}")

    # Source 5: S&P 600 Small Cap
    log.info("Source 5: S&P 600 Small Cap...")
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
                              storage_options={"User-Agent": headers["User-Agent"]})
        added = 0
        for _, row in tables[0].iterrows():
            sym = str(row["Symbol"]).replace(".", "-").strip()
            if sym and len(sym) <= 5 and sym.isalpha() and sym not in all_tickers:
                all_tickers[sym] = {"name": str(row.get("Security", "")),
                                    "exchange": "NYSE/NASDAQ",
                                    "sector": str(row.get("GICS Sector", ""))}
                added += 1
        log.info(f"  Added {added} new tickers")
    except Exception as e:
        log.error(f"  S&P 600 failed: {e}")

    # Source 6: S&P 400 Mid Cap
    log.info("Source 6: S&P 400 Mid Cap...")
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
                              storage_options={"User-Agent": headers["User-Agent"]})
        added = 0
        for _, row in tables[0].iterrows():
            sym = str(row["Symbol"]).replace(".", "-").strip()
            if sym and len(sym) <= 5 and sym.isalpha() and sym not in all_tickers:
                all_tickers[sym] = {"name": str(row.get("Security", "")),
                                    "exchange": "NYSE/NASDAQ",
                                    "sector": str(row.get("GICS Sector", ""))}
                added += 1
        log.info(f"  Added {added} new tickers")
    except Exception as e:
        log.error(f"  S&P 400 failed: {e}")

    # Source 7: Additional known runners and high-growth midcaps
    log.info("Source 7: Curated high-growth + known runner list...")
    extras = [
        # Recent high-growth / runner candidates
        "DUOL", "TOST", "RKLB", "LUNR", "ASTS", "SOUN", "APLD", "IONQ",
        "RGTI", "QBTS", "BBAI", "BIGC", "DLO", "PAYO", "FOUR", "RELY",
        "CORT", "HIMS", "GDRX", "OSCR", "DOCS", "CERT", "RXRX", "DNLI",
        "BEAM", "CRSP", "NTLA", "EDIT", "VERV", "VKTX", "MDGL", "PCVX",
        "SMMT", "AEHR", "WOLF", "ACLS", "ALGM", "RMBS", "PLAB", "CEVA",
        "CLSK", "BITF", "HUT", "BTBT", "CIFR", "IREN", "CORZ", "WULF",
        "AFRM", "UPST", "LC", "SOFI", "NU", "HOOD", "COIN", "MSTR",
        "VRT", "SMCI", "DELL", "ANET", "CRDO", "CIEN", "CALX",
        "APP", "TTD", "MGNI", "PUBM", "ZETA", "BRZE", "SEMR",
        "GTLB", "CFLT", "MDB", "ESTC", "DDOG", "NET", "CRWD", "ZS",
        "CVNA", "LYFT", "UBER", "DASH", "ABNB", "RBLX", "U",
        "RDW", "ATKR", "CXT", "POWL", "GVA", "ROAD", "PRIM",
        "CHRD", "MTDR", "PR", "CRC", "CIVI", "TALO", "SM",
        "MP", "LAC", "ALB", "LTHM", "PLL", "SGML",
    ]
    added = 0
    for sym in extras:
        if sym not in all_tickers:
            all_tickers[sym] = {"name": "", "exchange": "NYSE/NASDAQ", "sector": ""}
            added += 1
    log.info(f"  Added {added} curated tickers")

    log.info(f"\nTotal unique tickers: {len(all_tickers)}")

    # Save to CSV
    rows = [{"symbol": sym, **info} for sym, info in all_tickers.items()]
    df = pd.DataFrame(rows)
    df.to_csv(TICKERS_CSV, index=False)
    log.info(f"Saved to {TICKERS_CSV}")

    # Update SQLite tickers table
    conn = sqlite3.connect(str(DB_PATH))
    for sym, info in all_tickers.items():
        conn.execute(
            "INSERT OR IGNORE INTO tickers (symbol, name, exchange, sector, industry) VALUES (?,?,?,?,?)",
            (sym, info.get("name", ""), info.get("exchange", ""), info.get("sector", ""), "")
        )
    conn.commit()
    new_count = conn.execute("SELECT COUNT(*) FROM tickers").fetchone()[0]
    conn.close()
    log.info(f"DB tickers table now has: {new_count} tickers")

    # Report by market cap range (from fundamentals cache if available)
    if FUND_PATH.exists():
        with open(FUND_PATH) as f:
            fund = json.load(f)
        mcap_ranges = {"<$300M": 0, "$300M-$1B": 0, "$1B-$5B": 0, "$5B-$15B": 0, "$15B-$50B": 0, "$50B+": 0, "Unknown": 0}
        for sym in all_tickers:
            mcap = fund.get(sym, {}).get("mcap", 0) or 0
            if mcap == 0:
                mcap_ranges["Unknown"] += 1
            elif mcap < 300e6:
                mcap_ranges["<$300M"] += 1
            elif mcap < 1e9:
                mcap_ranges["$300M-$1B"] += 1
            elif mcap < 5e9:
                mcap_ranges["$1B-$5B"] += 1
            elif mcap < 15e9:
                mcap_ranges["$5B-$15B"] += 1
            elif mcap < 50e9:
                mcap_ranges["$15B-$50B"] += 1
            else:
                mcap_ranges["$50B+"] += 1

        log.info(f"\nMarket Cap Distribution (known fundamentals):")
        for label, count in mcap_ranges.items():
            log.info(f"  {label:<15s}: {count:>5d}")

    return all_tickers


# =========================================================================
# TASK 2 — Sensitivity Analysis
# =========================================================================

def sensitivity_analysis():
    """Test filter relaxations on precision and signal frequency."""
    log.info("\n")
    log.info("=" * 70)
    log.info("TASK 2 — SENSITIVITY ANALYSIS")
    log.info("=" * 70)

    histories = load_all(min_rows=300)
    log.info(f"Histories loaded: {len(histories)}")

    if FUND_PATH.exists():
        with open(FUND_PATH) as f:
            fundamentals = json.load(f)
    else:
        fundamentals = {}

    conn = sqlite3.connect(str(DB_PATH))
    runner_symbols = set(r[0] for r in conn.execute("SELECT symbol FROM runners").fetchall())
    conn.close()

    start_check = pd.Timestamp("2019-01-01")
    end_check = pd.Timestamp("2024-06-30")

    # Test configs
    configs = [
        {"name": "Current (short>10%, rev>15%)", "short_min": 0.10, "rev_min": 0.15},
        {"name": "Relax short (7%)", "short_min": 0.07, "rev_min": 0.15},
        {"name": "Relax revenue (10%)", "short_min": 0.10, "rev_min": 0.10},
        {"name": "Relax both (short 7% + rev 10%)", "short_min": 0.07, "rev_min": 0.10},
        {"name": "Relax both more (short 5% + rev 8%)", "short_min": 0.05, "rev_min": 0.08},
    ]

    results = []

    for config in configs:
        short_min = config["short_min"]
        rev_min = config["rev_min"]
        true_signals = 0
        false_signals = 0

        for sym, hist in histories.items():
            if len(hist) < 300:
                continue

            fund = fundamentals.get(sym, {})
            mcap = fund.get("mcap", 0)
            rev_growth = fund.get("rev_growth")
            short_pct = fund.get("short_pct")
            profitable = fund.get("profitable", True)

            # Hard filters (common across all configs)
            if not (500_000_000 <= mcap <= 15_000_000_000):
                continue
            if profitable:
                continue

            # Variable filters
            if rev_growth is None or rev_growth <= rev_min:
                continue
            if short_pct is None or short_pct <= short_min:
                continue

            closes = hist["Close"]
            volumes = hist["Volume"]
            ma200 = closes.rolling(200).mean()
            vol_90d = volumes.rolling(90).mean()
            dates = hist.index

            last_sig = -60
            for i in range(201, len(hist) - 252):
                if dates[i] < start_check or dates[i] > end_check:
                    continue
                if (i - last_sig) < 60:
                    continue

                price = closes.iloc[i]
                prev_price = closes.iloc[i-1]
                ma_val = ma200.iloc[i]
                prev_ma = ma200.iloc[i-1]
                vol_val = volumes.iloc[i]
                vol_avg = vol_90d.iloc[i]

                if pd.isna(ma_val) or pd.isna(vol_avg) or ma_val <= 0 or vol_avg <= 0:
                    continue

                crossed = prev_price <= prev_ma and price > ma_val
                if not crossed:
                    continue
                if vol_val / vol_avg < 2.0:
                    continue
                if price > ma_val * 1.20:
                    continue

                last_sig = i

                future_end = min(i + 252, len(hist))
                if future_end <= i + 50:
                    continue

                future = closes.iloc[i:future_end]
                peak_return = (future.max() - price) / price * 100

                if peak_return >= 500:
                    true_signals += 1
                else:
                    false_signals += 1

        total = true_signals + false_signals
        precision = true_signals / total * 100 if total > 0 else 0
        per_year = total / 5.5  # 2019-mid 2024 = 5.5 years

        results.append({
            "config": config["name"],
            "short_min": short_min,
            "rev_min": rev_min,
            "true_signals": true_signals,
            "false_signals": false_signals,
            "total": total,
            "precision_pct": round(precision, 2),
            "signals_per_year": round(per_year, 1),
        })

    # Print results
    log.info(f"\n  {'Config':<45s} {'True':>5s} {'False':>6s} {'Total':>6s} {'Prec%':>7s} {'Sig/Yr':>7s}")
    log.info(f"  {'-'*45} {'-'*5} {'-'*6} {'-'*6} {'-'*7} {'-'*7}")

    for r in results:
        marker = " <<<" if r["precision_pct"] >= 10 and r["signals_per_year"] >= 10 else \
                 " <<" if r["precision_pct"] >= 10 else ""
        log.info(f"  {r['config']:<45s} {r['true_signals']:>5d} {r['false_signals']:>6d} "
                 f"{r['total']:>6d} {r['precision_pct']:>6.1f}% {r['signals_per_year']:>6.1f}{marker}")

    # Recommendation
    log.info(f"\n  RECOMMENDATION:")
    best = None
    for r in results:
        if r["precision_pct"] >= 10 and r["signals_per_year"] >= 10:
            if best is None or r["signals_per_year"] > best["signals_per_year"]:
                best = r

    if best:
        log.info(f"    USE: {best['config']}")
        log.info(f"    Precision: {best['precision_pct']}% | Signals/year: {best['signals_per_year']}")
        log.info(f"    This gives ~{best['signals_per_year']:.0f} actionable signals per year while keeping precision above 10%.")
    else:
        # Find best tradeoff
        above_10 = [r for r in results if r["precision_pct"] >= 10]
        if above_10:
            best = max(above_10, key=lambda x: x["signals_per_year"])
            log.info(f"    BEST AVAILABLE: {best['config']}")
            log.info(f"    Precision: {best['precision_pct']}% | Signals/year: {best['signals_per_year']}")
        else:
            log.info(f"    No config achieved both >10% precision and >10 signals/year.")
            log.info(f"    Consider expanding ticker universe (Task 1) to increase signal count.")

    return results


# =========================================================================
# Main
# =========================================================================

def run():
    # Task 1
    tickers = fetch_full_universe()

    # Task 2
    sensitivity = sensitivity_analysis()

    # Save combined report
    report = {
        "task1_tickers": len(tickers),
        "task2_sensitivity": sensitivity,
    }
    report_path = DATA_DIR / "expansion_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    log.info(f"\nReport saved to {report_path}")


if __name__ == "__main__":
    run()
