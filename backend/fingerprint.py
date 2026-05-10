"""
ALPHA HUNTER — Pre-Run Fingerprint (Task 3)
For each runner, analyze the 90 days BEFORE run_start_date.
All data from cache only — no network calls.
"""

import sqlite3
import sys
import logging
import json
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cache.price_cache import load_all

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "db.sqlite"
CSV_PATH = DATA_DIR / "fingerprints.csv"
CACHE_DIR = BASE_DIR / "cache" / "prices"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("fingerprint")


def load_runners() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM runners ORDER BY peak_return_pct DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_spy_vix() -> tuple[pd.Series, pd.Series]:
    """Load SPY and VIX from cache."""
    spy_path = DATA_DIR / "cache" / "market" / "spy.parquet"
    vix_path = DATA_DIR / "cache" / "market" / "vix.parquet"

    # Try new cache location first, fall back to price cache
    if not spy_path.exists():
        spy_path = CACHE_DIR / "SPY.parquet"
    if not vix_path.exists():
        vix_path = CACHE_DIR / "^VIX.parquet"

    spy = pd.read_parquet(spy_path)["Close"] if spy_path.exists() else pd.Series(dtype=float)
    vix = pd.read_parquet(vix_path)["Close"] if vix_path.exists() else pd.Series(dtype=float)
    return spy, vix


def compute_fingerprint(hist: pd.DataFrame, run_start_date: str,
                        spy: pd.Series, vix: pd.Series,
                        fund_data: dict) -> dict | None:
    """Compute the 90-day pre-run fingerprint for one runner."""
    sd = pd.Timestamp(run_start_date)

    # Get pre-run data (90 days before)
    pre = hist[hist.index < sd].tail(90)
    if len(pre) < 30:
        return None

    closes = pre["Close"]
    volumes = pre["Volume"]
    highs = pre["High"]
    lows = pre["Low"]

    # Also need 252 days for 52w high/low
    pre_252 = hist[hist.index < sd].tail(252)
    high_52w = pre_252["High"].max() if len(pre_252) > 50 else closes.max()
    low_52w = pre_252["Low"].min() if len(pre_252) > 50 else closes.min()

    last_price = closes.iloc[-1]
    if last_price <= 0 or high_52w <= 0:
        return None

    # === PRICE ACTION ===
    pct_below_52w_high = round((high_52w - last_price) / high_52w * 100, 2)
    pct_above_52w_low = round((last_price - low_52w) / low_52w * 100, 2) if low_52w > 0 else 0

    ret_10d = round((closes.iloc[-1] - closes.iloc[-10]) / closes.iloc[-10] * 100, 2) if len(closes) >= 10 else 0
    ret_30d = round((closes.iloc[-1] - closes.iloc[-30]) / closes.iloc[-30] * 100, 2) if len(closes) >= 30 else 0
    ret_60d = round((closes.iloc[-1] - closes.iloc[-60]) / closes.iloc[-60] * 100, 2) if len(closes) >= 60 else 0

    # Base detection: 30+ days within 15% range
    if len(closes) >= 30:
        last_30 = closes.iloc[-30:]
        range_pct = (last_30.max() - last_30.min()) / last_30.min() * 100 if last_30.min() > 0 else 100
        has_base = range_pct <= 15
    else:
        has_base = False

    # MA distances
    ma50 = closes.rolling(50).mean().iloc[-1] if len(closes) >= 50 else closes.mean()
    ma200_data = hist[hist.index < sd].tail(200)["Close"]
    ma200 = ma200_data.mean() if len(ma200_data) >= 200 else ma200_data.mean()

    dist_ma50 = round((last_price - ma50) / ma50 * 100, 2) if ma50 > 0 else 0
    dist_ma200 = round((last_price - ma200) / ma200 * 100, 2) if ma200 > 0 else 0
    above_ma200 = last_price > ma200

    # === VOLUME ===
    vol_30d = volumes.iloc[-30:] if len(volumes) >= 30 else volumes
    vol_90d = volumes

    avg_vol_30d = vol_30d.mean()
    avg_vol_90d = vol_90d.mean()
    avg_vol_60d = volumes.iloc[-60:].mean() if len(volumes) >= 60 else avg_vol_90d

    # Spike days (30d before run)
    threshold_2x = avg_vol_90d * 2
    threshold_3x = avg_vol_90d * 3
    spike_days_2x = int((vol_30d > threshold_2x).sum())
    spike_days_3x = int((vol_30d > threshold_3x).sum())

    # Volume trend: is 30d avg > 60d avg?
    vol_trend_increasing = bool(avg_vol_30d > avg_vol_60d)

    # Highest volume day in last 30d
    max_vol_30d = vol_30d.max()
    max_vol_ratio = round(max_vol_30d / avg_vol_90d, 2) if avg_vol_90d > 0 else 1

    # Volume on run_start_date itself
    run_day = hist[hist.index == sd]
    if not run_day.empty:
        vol_on_start = run_day["Volume"].iloc[0]
        vol_ratio_start = round(vol_on_start / avg_vol_90d, 2) if avg_vol_90d > 0 else 1
        green_on_start = bool(run_day["Close"].iloc[0] > run_day["Open"].iloc[0])
    else:
        # Use first day after
        after = hist[hist.index >= sd].head(1)
        if not after.empty:
            vol_on_start = after["Volume"].iloc[0]
            vol_ratio_start = round(vol_on_start / avg_vol_90d, 2) if avg_vol_90d > 0 else 1
            green_on_start = bool(after["Close"].iloc[0] > after["Open"].iloc[0])
        else:
            vol_ratio_start = 1
            green_on_start = False

    # === BREAKOUT SIGNALS (convergence score) ===
    signals = []
    if vol_ratio_start >= 3:
        signals.append("vol_3x")
    if green_on_start:
        signals.append("green_day")
    if above_ma200:
        signals.append("above_ma200")
    if pct_below_52w_high <= 20:
        signals.append("near_52w_high")
    if vol_trend_increasing:
        signals.append("vol_increasing")
    if has_base:
        signals.append("base_present")

    signals_present = len(signals)

    # === MACRO CONTEXT ===
    vix_val = None
    spy_regime = None
    days_since_correction = None

    if not vix.empty:
        vix_on_date = vix[vix.index <= sd]
        if len(vix_on_date) > 0:
            vix_val = round(float(vix_on_date.iloc[-1]), 2)

    if not spy.empty:
        spy_on_date = spy[spy.index <= sd]
        if len(spy_on_date) >= 50:
            spy_price = spy_on_date.iloc[-1]
            spy_ma50 = spy_on_date.iloc[-50:].mean()
            spy_regime = "above_ma50" if spy_price > spy_ma50 else "below_ma50"

            # Days since last 10% correction
            spy_high = spy_on_date.expanding().max()
            drawdown = (spy_on_date - spy_high) / spy_high * 100
            corrections = drawdown[drawdown <= -10]
            if len(corrections) > 0:
                last_correction = corrections.index[-1]
                days_since_correction = (sd - last_correction).days
            else:
                days_since_correction = 999

    # === FUNDAMENTALS (from existing analysis_v2 or fund cache) ===
    rev_growth = fund_data.get("rev_growth")
    eps_positive = fund_data.get("profitable", None)
    gross_margin = None  # Would need FMP historical — leave null for now

    return {
        # Price action
        "pct_below_52w_high": pct_below_52w_high,
        "pct_above_52w_low": pct_above_52w_low,
        "ret_10d": ret_10d,
        "ret_30d": ret_30d,
        "ret_60d": ret_60d,
        "has_base": has_base,
        "dist_ma50_pct": dist_ma50,
        "dist_ma200_pct": dist_ma200,
        "above_ma200": above_ma200,
        # Volume
        "avg_vol_30d": round(avg_vol_30d),
        "avg_vol_90d": round(avg_vol_90d),
        "spike_days_2x": spike_days_2x,
        "spike_days_3x": spike_days_3x,
        "vol_trend_increasing": vol_trend_increasing,
        "max_vol_ratio_30d": max_vol_ratio,
        "vol_ratio_on_start": vol_ratio_start,
        # Breakout signals
        "signals_present": signals_present,
        "signals_list": ",".join(signals),
        "green_on_start": green_on_start,
        # Macro
        "vix": vix_val,
        "spy_regime": spy_regime,
        "days_since_correction": days_since_correction,
        # Fundamentals
        "revenue_growth_yoy": round(rev_growth * 100, 2) if rev_growth else None,
        "eps_positive": eps_positive,
        "gross_margin": gross_margin,
    }


def run():
    log.info("Loading runners from DB...")
    runners = load_runners()
    log.info(f"Runners to fingerprint: {len(runners)}")

    log.info("Loading price cache...")
    histories = load_all(min_rows=100)
    log.info(f"Histories available: {len(histories)}")

    log.info("Loading SPY + VIX...")
    spy, vix = load_spy_vix()
    log.info(f"SPY: {len(spy)} days | VIX: {len(vix)} days")

    # Load fundamentals
    fund_path = DATA_DIR / "cache" / "fundamentals.json"
    if fund_path.exists():
        with open(fund_path) as f:
            all_fund = json.load(f)
    else:
        all_fund = {}

    fingerprints = []
    skipped = 0

    for i, r in enumerate(runners, 1):
        sym = r["symbol"]
        if sym not in histories:
            skipped += 1
            continue

        hist = histories[sym]
        fund = all_fund.get(sym, {})

        fp = compute_fingerprint(hist, r["run_start_date"], spy, vix, fund)
        if fp is None:
            skipped += 1
            continue

        # Merge runner info + fingerprint
        row = {
            "symbol": sym,
            "run_start_date": r["run_start_date"],
            "peak_return_pct": r["peak_return_pct"],
            "peak_date": r["peak_date"],
            "sector": r["sector"],
            "market_cap_at_start": r["market_cap_at_start"],
        }
        row.update(fp)
        fingerprints.append(row)

        if i % 20 == 0:
            log.info(f"  [{i}/{len(runners)}] Fingerprinted: {len(fingerprints)} | Skipped: {skipped}")

    log.info(f"\nTotal fingerprints: {len(fingerprints)} | Skipped: {skipped}")

    # Save CSV
    df = pd.DataFrame(fingerprints)
    df.to_csv(CSV_PATH, index=False)
    log.info(f"Saved to {CSV_PATH}")

    # Save to SQLite
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DROP TABLE IF EXISTS fingerprints")
    df.to_sql("fingerprints", conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()
    log.info(f"Saved to db.sqlite table 'fingerprints'")


if __name__ == "__main__":
    run()
