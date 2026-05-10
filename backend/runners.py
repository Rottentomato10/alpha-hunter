"""
ALPHA HUNTER — Runner Universe (Task 2)
Single source of truth for all 500%+ runners (2019-2024).
Uses price cache — no network calls.
"""

import sqlite3
import sys
import logging
import json
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cache.price_cache import load_all, CACHE_DIR

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "db.sqlite"
CSV_PATH = DATA_DIR / "runners.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("runners")

WINDOW = 252          # Trading days (~1 year)
MIN_RETURN = 500      # Percent
MIN_MCAP = 500_000_000
START_YEAR = 2019
END_YEAR = 2024


def get_fundamentals() -> dict:
    """Load cached fundamentals if available."""
    fund_path = DATA_DIR / "cache" / "fundamentals.json"
    if fund_path.exists():
        with open(fund_path) as f:
            return json.load(f)
    return {}


def find_runners(histories: dict[str, pd.DataFrame], fundamentals: dict) -> list[dict]:
    """Slide 252-day window across each ticker. Find any window where
    the peak return from window open reaches 500%+."""

    runners = []
    total = len(histories)

    for idx, (sym, hist) in enumerate(histories.items(), 1):
        if len(hist) < WINDOW + 100:
            continue

        closes = hist["Close"]
        dates = hist.index

        # Filter to our date range
        start_mask = dates >= pd.Timestamp(f"{START_YEAR}-01-01")
        if not start_mask.any():
            continue

        # Market cap filter (best effort)
        fund = fundamentals.get(sym, {})
        mcap = fund.get("mcap", 0)
        if mcap and mcap < MIN_MCAP:
            continue

        # Find runs
        ticker_runs = []
        i = 0
        while i < len(closes) - WINDOW:
            if dates[i] < pd.Timestamp(f"{START_YEAR}-01-01"):
                i += 1
                continue
            if dates[i] > pd.Timestamp(f"{END_YEAR}-12-31"):
                break

            start_price = closes.iloc[i]
            if start_price <= 0:
                i += 1
                continue

            # Look at the next 252 days for peak
            window_slice = closes.iloc[i:i + WINDOW + 1]
            peak_price = window_slice.max()
            peak_return = (peak_price - start_price) / start_price * 100

            if peak_return >= MIN_RETURN:
                peak_idx_in_window = window_slice.idxmax()
                peak_date = peak_idx_in_window

                # Price after peak
                peak_loc = hist.index.get_loc(peak_date)
                price_30d = closes.iloc[peak_loc + 30] if peak_loc + 30 < len(closes) else None
                price_90d = closes.iloc[peak_loc + 90] if peak_loc + 90 < len(closes) else None

                run = {
                    "symbol": sym,
                    "run_start_date": dates[i].strftime("%Y-%m-%d"),
                    "run_start_price": round(start_price, 4),
                    "peak_price": round(peak_price, 4),
                    "peak_date": peak_date.strftime("%Y-%m-%d"),
                    "peak_return_pct": round(peak_return, 2),
                    "price_30d_after_peak": round(price_30d, 4) if price_30d else None,
                    "price_90d_after_peak": round(price_90d, 4) if price_90d else None,
                    "sector": fund.get("sector", ""),
                    "industry": fund.get("industry", ""),
                    "market_cap_at_start": mcap if mcap else None,
                }
                ticker_runs.append(run)

                # Skip ahead past this run (to avoid overlapping runs)
                i += WINDOW // 2  # Move forward 6 months
            else:
                i += 5  # Step 1 week

        runners.extend(ticker_runs)

        if idx % 100 == 0:
            log.info(f"  [{idx}/{total}] Scanned | Runners found: {len(runners)}")

    return runners


def save_runners(runners: list[dict]):
    """Save to CSV and SQLite."""
    df = pd.DataFrame(runners)
    df.to_csv(CSV_PATH, index=False)
    log.info(f"Saved {len(runners)} runners to {CSV_PATH}")

    # SQLite
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DROP TABLE IF EXISTS runners")
    conn.execute("""
        CREATE TABLE runners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            run_start_date TEXT,
            run_start_price REAL,
            peak_price REAL,
            peak_date TEXT,
            peak_return_pct REAL,
            price_30d_after_peak REAL,
            price_90d_after_peak REAL,
            sector TEXT,
            industry TEXT,
            market_cap_at_start REAL
        )
    """)
    for r in runners:
        conn.execute(
            """INSERT INTO runners (symbol, run_start_date, run_start_price, peak_price,
               peak_date, peak_return_pct, price_30d_after_peak, price_90d_after_peak,
               sector, industry, market_cap_at_start)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (r["symbol"], r["run_start_date"], r["run_start_price"], r["peak_price"],
             r["peak_date"], r["peak_return_pct"], r["price_30d_after_peak"],
             r["price_90d_after_peak"], r["sector"], r["industry"], r["market_cap_at_start"])
        )
    conn.commit()
    conn.close()
    log.info(f"Saved to db.sqlite table 'runners'")


def print_summary(runners: list[dict]):
    df = pd.DataFrame(runners)
    log.info("")
    log.info("=" * 60)
    log.info(f"RUNNER UNIVERSE: {len(runners)} total runs found")
    log.info("=" * 60)

    log.info(f"\nBy Year (run start):")
    df["year"] = pd.to_datetime(df["run_start_date"]).dt.year
    for yr, group in df.groupby("year"):
        log.info(f"  {yr}: {len(group)} runners")

    log.info(f"\nBy Sector:")
    for sector, group in df.groupby("sector"):
        if sector:
            log.info(f"  {sector}: {len(group)}")

    log.info(f"\nTop 10 by Peak Return:")
    top = df.nlargest(10, "peak_return_pct")
    for _, r in top.iterrows():
        log.info(f"  {r['symbol']:>6s}  {r['run_start_date']}  +{r['peak_return_pct']:>8.1f}%  {r['sector']}")

    log.info(f"\nReturn stats:")
    log.info(f"  Median peak return: +{df['peak_return_pct'].median():.1f}%")
    log.info(f"  Mean peak return:   +{df['peak_return_pct'].mean():.1f}%")
    log.info(f"  Max peak return:    +{df['peak_return_pct'].max():.1f}%")

    # Did they hold after peak?
    hold_30 = df.dropna(subset=["price_30d_after_peak"])
    if len(hold_30) > 0:
        hold_30["drop_30d"] = (hold_30["price_30d_after_peak"] - hold_30["peak_price"]) / hold_30["peak_price"] * 100
        log.info(f"\n  Avg drop 30d after peak: {hold_30['drop_30d'].mean():.1f}%")
        log.info(f"  Median drop 30d after peak: {hold_30['drop_30d'].median():.1f}%")


def run():
    log.info("Loading price cache...")
    histories = load_all(min_rows=WINDOW + 100)
    log.info(f"Loaded {len(histories)} tickers from cache")

    fundamentals = get_fundamentals()
    log.info(f"Fundamentals: {len(fundamentals)} tickers")

    log.info(f"\nScanning for 500%+ runners ({START_YEAR}-{END_YEAR})...")
    runners = find_runners(histories, fundamentals)

    if not runners:
        log.error("No runners found!")
        return

    save_runners(runners)
    print_summary(runners)


if __name__ == "__main__":
    run()
