"""
ALPHA HUNTER — Backtesting Engine
Scans every week 2018-2023 across all tickers for buy signals,
then measures forward 12-month returns to calculate precision.
"""

import sqlite3
import sys
import time
import logging
import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

import yfinance as yf
import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "db.sqlite"
LOG_PATH = DATA_DIR / "backtest.log"
REPORT_PATH = DATA_DIR / "backtest_report.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("backtest")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BACKTEST_START = "2018-01-01"
BACKTEST_END = "2023-12-31"
FORWARD_DAYS = 252          # 12 months of trading days
GAIN_THRESHOLD = 100        # % gain to count as a "hit"
VOL_SPIKE_MULT = 3.0
MA_PERIOD = 200
HIGH_52W_PCT = 0.50         # Must be within 50% of 52w high
MIN_MCAP = 500_000_000
SAMPLE_WEEKS = None         # None = all weeks; set int to sample for speed
RANDOM_BASELINE_N = 2000    # Number of random stock/date pairs for baseline
REQUEST_DELAY = 0.25

# ---------------------------------------------------------------------------
# Step 1: Load all ticker history up front (batch)
# ---------------------------------------------------------------------------

def load_all_histories(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Download full 2016-2025 history for every ticker. 2016 start gives
    enough lookback for 200-day MA when backtesting from 2018."""
    log.info(f"Downloading history for {len(symbols)} tickers (2016-2025)...")
    histories = {}
    failed = 0

    for i, sym in enumerate(symbols, 1):
        try:
            t = yf.Ticker(sym)
            h = t.history(start="2016-01-01", end="2025-12-31", auto_adjust=True)
            if h.empty or len(h) < MA_PERIOD + 50:
                failed += 1
                continue
            h.index = h.index.tz_localize(None)
            histories[sym] = h
        except Exception:
            failed += 1

        if i % 100 == 0:
            log.info(f"  Downloaded {i}/{len(symbols)} ({len(histories)} valid, {failed} failed)")
        time.sleep(REQUEST_DELAY)

    log.info(f"Loaded {len(histories)} tickers ({failed} failed)")
    return histories


def get_market_cap_estimate(sym: str) -> float:
    """Quick market cap estimate from yfinance .info (cached once per ticker)."""
    try:
        info = yf.Ticker(sym).info or {}
        return info.get("marketCap", 0) or 0
    except:
        return 0


# ---------------------------------------------------------------------------
# Step 2: Scan for signals
# ---------------------------------------------------------------------------

def scan_signals(histories: dict[str, pd.DataFrame], mcaps: dict[str, float]) -> list[dict]:
    """For each Monday in backtest range, check every ticker for a buy signal
    that fired in that week (Mon-Fri)."""

    bt_start = pd.Timestamp(BACKTEST_START)
    bt_end = pd.Timestamp(BACKTEST_END)

    # Generate all Mondays in range
    mondays = pd.date_range(bt_start, bt_end, freq="W-MON")
    if SAMPLE_WEEKS:
        mondays = mondays[::max(1, len(mondays) // SAMPLE_WEEKS)]

    log.info(f"Scanning {len(mondays)} weeks x {len(histories)} tickers...")

    signals = []
    week_count = 0

    for monday in mondays:
        friday = monday + timedelta(days=4)
        week_count += 1

        for sym, hist in histories.items():
            # Quick market cap filter
            mcap = mcaps.get(sym, 0)
            if mcap < MIN_MCAP:
                continue

            try:
                # Need at least MA_PERIOD days before this week
                week_mask = (hist.index >= monday) & (hist.index <= friday)
                week_data = hist[week_mask]
                if week_data.empty:
                    continue

                # Pre-week data for MA and 52w high
                pre_week = hist[hist.index < monday]
                if len(pre_week) < MA_PERIOD:
                    continue

                ma200 = pre_week["Close"].iloc[-MA_PERIOD:].mean()
                high_52w = pre_week["Close"].iloc[-252:].max() if len(pre_week) >= 252 else pre_week["Close"].max()
                vol_ma20 = pre_week["Volume"].iloc[-20:].mean()

                if vol_ma20 <= 0 or ma200 <= 0 or high_52w <= 0:
                    continue

                # Check each day in the week
                for idx, row in week_data.iterrows():
                    close = row["Close"]
                    opn = row["Open"]
                    vol = row["Volume"]

                    # Signal conditions
                    is_green = close > opn
                    vol_spike = vol >= vol_ma20 * VOL_SPIKE_MULT
                    above_ma200 = close > ma200
                    near_high = close >= high_52w * (1 - HIGH_52W_PCT)

                    if is_green and vol_spike and above_ma200 and near_high:
                        vol_ratio = round(vol / vol_ma20, 2)

                        # Forward return (12 months)
                        signal_idx = hist.index.get_loc(idx)
                        if signal_idx + FORWARD_DAYS < len(hist):
                            future_slice = hist["Close"].iloc[signal_idx:signal_idx + FORWARD_DAYS + 1]
                            future_max = future_slice.max()
                            future_end = future_slice.iloc[-1]
                            fwd_return = round((future_end - close) / close * 100, 2)
                            fwd_max_return = round((future_max - close) / close * 100, 2)
                        else:
                            fwd_return = None
                            fwd_max_return = None

                        signals.append({
                            "symbol": sym,
                            "date": idx.strftime("%Y-%m-%d"),
                            "price": round(close, 4),
                            "vol_ratio": vol_ratio,
                            "ma200": round(ma200, 4),
                            "pct_of_52w_high": round(close / high_52w * 100, 1),
                            "market_cap": mcap,
                            "fwd_12m_return": fwd_return,
                            "fwd_12m_max_return": fwd_max_return,
                        })
                        break  # One signal per ticker per week max

            except Exception:
                continue

        if week_count % 20 == 0:
            log.info(f"  Week {week_count}/{len(mondays)} | Signals so far: {len(signals)}")

    log.info(f"Total raw signals: {len(signals)}")

    # Deduplicate: keep at most one signal per ticker per 60 days
    signals.sort(key=lambda x: (x["symbol"], x["date"]))
    deduped = []
    last_signal = {}
    for s in signals:
        sym = s["symbol"]
        d = datetime.strptime(s["date"], "%Y-%m-%d")
        if sym in last_signal:
            if (d - last_signal[sym]).days < 60:
                continue
        last_signal[sym] = d
        deduped.append(s)

    log.info(f"After dedup (60-day cooldown): {len(deduped)} signals")
    return deduped


# ---------------------------------------------------------------------------
# Step 3: Random baseline
# ---------------------------------------------------------------------------

def compute_baseline(histories: dict[str, pd.DataFrame], n: int = RANDOM_BASELINE_N) -> dict:
    """Pick N random (ticker, date) pairs and measure 12-month forward return."""
    bt_start = pd.Timestamp(BACKTEST_START)
    bt_end = pd.Timestamp(BACKTEST_END)

    eligible = [(sym, hist) for sym, hist in histories.items() if len(hist) > FORWARD_DAYS + 252]
    if not eligible:
        return {"samples": 0, "hit_rate": 0}

    hits = 0
    total = 0
    returns = []

    for _ in range(n):
        sym, hist = random.choice(eligible)
        # Pick a random date in backtest range
        valid_dates = hist[(hist.index >= bt_start) & (hist.index <= bt_end)]
        if len(valid_dates) < FORWARD_DAYS + 1:
            continue
        max_idx = len(valid_dates) - FORWARD_DAYS - 1
        if max_idx <= 0:
            continue
        rand_pos = random.randint(0, max_idx)
        rand_date = valid_dates.index[rand_pos]

        idx_in_full = hist.index.get_loc(rand_date)
        if idx_in_full + FORWARD_DAYS >= len(hist):
            continue

        price = hist["Close"].iloc[idx_in_full]
        future = hist["Close"].iloc[idx_in_full + FORWARD_DAYS]
        if price <= 0:
            continue

        ret = (future - price) / price * 100
        returns.append(ret)
        total += 1
        if ret >= GAIN_THRESHOLD:
            hits += 1

    avg_ret = round(np.mean(returns), 2) if returns else 0
    median_ret = round(np.median(returns), 2) if returns else 0
    hit_rate = round(hits / total * 100, 2) if total > 0 else 0

    return {
        "samples": total,
        "hits_100pct": hits,
        "hit_rate_pct": hit_rate,
        "avg_return": avg_ret,
        "median_return": median_ret,
    }


# ---------------------------------------------------------------------------
# Step 4: Compute stats
# ---------------------------------------------------------------------------

def compute_stats(signals: list[dict], baseline: dict) -> dict:
    """Compute precision, recall-proxy, and edge vs baseline."""
    with_fwd = [s for s in signals if s["fwd_12m_return"] is not None]
    total = len(with_fwd)
    if total == 0:
        return {"error": "No signals with forward data"}

    hits = [s for s in with_fwd if s["fwd_12m_return"] >= GAIN_THRESHOLD]
    hits_max = [s for s in with_fwd if s["fwd_12m_max_return"] is not None and s["fwd_12m_max_return"] >= GAIN_THRESHOLD]
    losses = [s for s in with_fwd if s["fwd_12m_return"] < 0]
    big_losses = [s for s in with_fwd if s["fwd_12m_return"] < -50]

    returns = [s["fwd_12m_return"] for s in with_fwd]
    avg_ret = round(np.mean(returns), 2)
    median_ret = round(np.median(returns), 2)
    max_ret = round(max(returns), 2)
    min_ret = round(min(returns), 2)

    precision = round(len(hits) / total * 100, 2)
    precision_max = round(len(hits_max) / total * 100, 2)
    loss_rate = round(len(losses) / total * 100, 2)
    big_loss_rate = round(len(big_losses) / total * 100, 2)

    baseline_hit = baseline.get("hit_rate_pct", 0)
    edge = round(precision - baseline_hit, 2)
    edge_ratio = round(precision / baseline_hit, 2) if baseline_hit > 0 else 0

    # By year
    by_year = defaultdict(lambda: {"total": 0, "hits": 0, "returns": []})
    for s in with_fwd:
        yr = s["date"][:4]
        by_year[yr]["total"] += 1
        by_year[yr]["returns"].append(s["fwd_12m_return"])
        if s["fwd_12m_return"] >= GAIN_THRESHOLD:
            by_year[yr]["hits"] += 1

    by_year_summary = {}
    for yr, data in sorted(by_year.items()):
        by_year_summary[yr] = {
            "signals": data["total"],
            "hits": data["hits"],
            "hit_rate_pct": round(data["hits"] / data["total"] * 100, 1) if data["total"] else 0,
            "avg_return": round(np.mean(data["returns"]), 1),
        }

    # Top signals (best forward returns)
    top_signals = sorted(with_fwd, key=lambda x: x["fwd_12m_return"], reverse=True)[:15]

    return {
        "total_signals": total,
        "hits_100pct_eoy": len(hits),
        "hits_100pct_peak": len(hits_max),
        "precision_eoy_pct": precision,
        "precision_peak_pct": precision_max,
        "loss_rate_pct": loss_rate,
        "big_loss_rate_pct": big_loss_rate,
        "avg_forward_return_pct": avg_ret,
        "median_forward_return_pct": median_ret,
        "max_return_pct": max_ret,
        "min_return_pct": min_ret,
        "baseline_hit_rate_pct": baseline_hit,
        "baseline_avg_return_pct": baseline.get("avg_return", 0),
        "edge_vs_baseline_pp": edge,
        "edge_ratio": edge_ratio,
        "by_year": by_year_summary,
        "top_signals": top_signals,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_backtest():
    conn = sqlite3.connect(str(DB_PATH))
    symbols = [r[0] for r in conn.execute("SELECT symbol FROM tickers").fetchall()]
    conn.close()

    log.info(f"ALPHA HUNTER BACKTEST")
    log.info(f"Period: {BACKTEST_START} to {BACKTEST_END}")
    log.info(f"Tickers: {len(symbols)}")
    log.info(f"Signal: Vol >= {VOL_SPIKE_MULT}X + Green + Above MA{MA_PERIOD} + Within {int(HIGH_52W_PCT*100)}% of 52w high")
    log.info(f"Forward window: {FORWARD_DAYS} trading days")
    log.info(f"Hit threshold: +{GAIN_THRESHOLD}%")
    log.info("=" * 60)

    # 1. Load histories
    histories = load_all_histories(symbols)

    # 2. Get market caps (quick pass)
    log.info("Fetching market caps...")
    mcaps = {}
    for i, sym in enumerate(histories.keys(), 1):
        mcaps[sym] = get_market_cap_estimate(sym)
        if i % 100 == 0:
            log.info(f"  MCaps: {i}/{len(histories)}")
        time.sleep(0.1)

    mcap_filtered = {s: m for s, m in mcaps.items() if m >= MIN_MCAP}
    log.info(f"Tickers with MCap >= ${MIN_MCAP:,.0f}: {len(mcap_filtered)}")

    # 3. Scan signals
    signals = scan_signals(histories, mcaps)

    # 4. Random baseline
    log.info(f"Computing random baseline ({RANDOM_BASELINE_N} samples)...")
    baseline = compute_baseline(histories)
    log.info(f"Baseline: {baseline['hit_rate_pct']}% hit rate | avg return {baseline['avg_return']}%")

    # 5. Stats
    stats = compute_stats(signals, baseline)

    # 6. Report
    report = {
        "config": {
            "period": f"{BACKTEST_START} to {BACKTEST_END}",
            "tickers_scanned": len(histories),
            "tickers_mcap_eligible": len(mcap_filtered),
            "vol_spike_mult": VOL_SPIKE_MULT,
            "ma_period": MA_PERIOD,
            "forward_days": FORWARD_DAYS,
            "hit_threshold_pct": GAIN_THRESHOLD,
        },
        "baseline": baseline,
        "results": stats,
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)

    # Print results
    log.info("=" * 60)
    log.info("BACKTEST RESULTS")
    log.info("=" * 60)
    log.info(f"Total signals fired:         {stats.get('total_signals', 0)}")
    log.info(f"Hits (100%+ by EOY):         {stats.get('hits_100pct_eoy', 0)}")
    log.info(f"Hits (100%+ at peak):        {stats.get('hits_100pct_peak', 0)}")
    log.info(f"")
    log.info(f"PRECISION (EOY):             {stats.get('precision_eoy_pct', 0)}%")
    log.info(f"PRECISION (Peak):            {stats.get('precision_peak_pct', 0)}%")
    log.info(f"Loss rate:                   {stats.get('loss_rate_pct', 0)}%")
    log.info(f"Big loss rate (>50%):        {stats.get('big_loss_rate_pct', 0)}%")
    log.info(f"")
    log.info(f"Avg forward return:          {stats.get('avg_forward_return_pct', 0)}%")
    log.info(f"Median forward return:       {stats.get('median_forward_return_pct', 0)}%")
    log.info(f"Best signal return:          {stats.get('max_return_pct', 0)}%")
    log.info(f"Worst signal return:         {stats.get('min_return_pct', 0)}%")
    log.info(f"")
    log.info(f"BASELINE hit rate:           {baseline.get('hit_rate_pct', 0)}%")
    log.info(f"BASELINE avg return:         {baseline.get('avg_return', 0)}%")
    log.info(f"")
    log.info(f"EDGE vs baseline:            +{stats.get('edge_vs_baseline_pp', 0)} pp")
    log.info(f"EDGE ratio:                  {stats.get('edge_ratio', 0)}x")
    log.info(f"")

    by_year = stats.get("by_year", {})
    if by_year:
        log.info("BY YEAR:")
        for yr, data in by_year.items():
            log.info(f"  {yr}: {data['signals']} signals, {data['hits']} hits ({data['hit_rate_pct']}%), avg return {data['avg_return']}%")

    log.info(f"")
    log.info(f"TOP SIGNALS:")
    for s in stats.get("top_signals", [])[:10]:
        log.info(f"  {s['symbol']} {s['date']}: Vol {s['vol_ratio']}X, fwd +{s['fwd_12m_return']}%")

    verdict = "HAS EDGE" if stats.get("precision_eoy_pct", 0) > baseline.get("hit_rate_pct", 5) * 1.5 else "NO CLEAR EDGE"
    log.info(f"")
    log.info(f"VERDICT: {verdict}")
    log.info(f"Report saved to {REPORT_PATH}")


if __name__ == "__main__":
    run_backtest()
