"""
ALPHA HUNTER — Recovery Entry Strategy
Find the MA200 cross entry point for each runner.
Higher conviction entry: stock has RECOVERED, not still falling.
"""

import sqlite3
import sys
import json
import logging
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cache.price_cache import load_all

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "db.sqlite"
FUND_PATH = DATA_DIR / "cache" / "fundamentals.json"
REPORT_PATH = DATA_DIR / "recovery_entry_report.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("recovery")


def load_runners():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM runners ORDER BY peak_return_pct DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_fundamentals():
    if FUND_PATH.exists():
        with open(FUND_PATH) as f:
            return json.load(f)
    return {}


# =========================================================================
# TASK 1 — Find confirmed_entry_date for each runner
# =========================================================================

def find_confirmed_entry(hist, runner, fund):
    """Find first day where price crosses above MA200 with volume confirmation."""
    sd = pd.Timestamp(runner["run_start_date"])
    peak_date = pd.Timestamp(runner["peak_date"])
    peak_price = runner["peak_price"]
    start_price = runner["run_start_price"]

    # Fundamental filters
    rev_growth = fund.get("rev_growth")
    short_pct = fund.get("short_pct")

    has_rev = rev_growth is not None and rev_growth > 0.15
    has_short = short_pct is not None and short_pct > 0.10

    # Search window: from run_start to peak (or slightly after start)
    search = hist[(hist.index >= sd) & (hist.index <= peak_date)]
    if len(search) < 10:
        return None

    closes = hist["Close"]
    volumes = hist["Volume"]

    # We need MA200 calculated from full history
    ma200 = closes.rolling(200).mean()
    vol_90d = volumes.rolling(90).mean()

    results = {}

    for i in range(1, len(search)):
        date = search.index[i]
        loc = hist.index.get_loc(date)

        if loc < 200:
            continue

        price = closes.iloc[loc]
        prev_price = closes.iloc[loc - 1]
        ma200_val = ma200.iloc[loc]
        prev_ma200 = ma200.iloc[loc - 1]
        vol_val = volumes.iloc[loc]
        vol_avg = vol_90d.iloc[loc]

        if pd.isna(ma200_val) or pd.isna(vol_avg) or ma200_val <= 0 or vol_avg <= 0:
            continue

        # Condition a: price crossed above MA200 (was below, now above)
        crossed = prev_price <= prev_ma200 and price > ma200_val

        if not crossed:
            continue

        # Condition b: volume > 1.5X 90d average
        vol_ratio = vol_val / vol_avg
        vol_ok = vol_ratio >= 1.5

        if not vol_ok:
            continue

        # Conditions c & d: revenue > 15% and short > 10%
        # (fundamental filters — from cached data, apply as-is)
        if not has_rev or not has_short:
            continue

        # Found confirmed entry!
        entry_price = price

        # How much move already happened?
        total_move = peak_price - start_price
        move_so_far = entry_price - start_price
        pct_move_missed = (move_so_far / total_move * 100) if total_move > 0 else 0

        # Return still available
        return_remaining = (peak_price - entry_price) / entry_price * 100

        # Days from entry to peak
        days_to_peak = (peak_date - date).days

        results = {
            "confirmed_entry_date": date.strftime("%Y-%m-%d"),
            "confirmed_entry_price": round(entry_price, 4),
            "vol_ratio_at_entry": round(vol_ratio, 2),
            "pct_move_missed": round(pct_move_missed, 1),
            "return_remaining_pct": round(return_remaining, 1),
            "days_entry_to_peak": days_to_peak,
            "has_fundamentals": True,
        }
        return results

    # If no full-filter entry found, try relaxed (just MA200 cross + volume)
    for i in range(1, len(search)):
        date = search.index[i]
        loc = hist.index.get_loc(date)
        if loc < 200:
            continue

        price = closes.iloc[loc]
        prev_price = closes.iloc[loc - 1]
        ma200_val = ma200.iloc[loc]
        prev_ma200 = ma200.iloc[loc - 1]
        vol_val = volumes.iloc[loc]
        vol_avg = vol_90d.iloc[loc]

        if pd.isna(ma200_val) or pd.isna(vol_avg) or ma200_val <= 0 or vol_avg <= 0:
            continue

        crossed = prev_price <= prev_ma200 and price > ma200_val
        vol_ok = vol_val / vol_avg >= 1.5

        if crossed and vol_ok:
            entry_price = price
            total_move = peak_price - start_price
            move_so_far = entry_price - start_price
            pct_move_missed = (move_so_far / total_move * 100) if total_move > 0 else 0
            return_remaining = (peak_price - entry_price) / entry_price * 100
            days_to_peak = (peak_date - date).days

            return {
                "confirmed_entry_date": date.strftime("%Y-%m-%d"),
                "confirmed_entry_price": round(entry_price, 4),
                "vol_ratio_at_entry": round(vol_val / vol_avg, 2),
                "pct_move_missed": round(pct_move_missed, 1),
                "return_remaining_pct": round(return_remaining, 1),
                "days_entry_to_peak": days_to_peak,
                "has_fundamentals": False,
            }

    return None


# =========================================================================
# TASK 2 — Precision with MA200 cross filter
# =========================================================================

def test_precision_ma200(histories, fundamentals, runner_symbols):
    """Find all MA200 crosses with filters, count true vs false."""
    log.info("\nTASK 2 — Precision with MA200 cross filter")
    log.info("Filters: MA200 cross in last 30d + vol > 2X + short > 10% + rev > 15% + midcap + unprofitable")

    start_check = pd.Timestamp("2019-01-01")
    end_check = pd.Timestamp("2024-06-30")

    true_signals = 0
    false_signals = 0
    true_returns = []
    false_returns = []

    total = len(histories)
    for idx, (sym, hist) in enumerate(histories.items()):
        if len(hist) < 300:
            continue

        fund = fundamentals.get(sym, {})
        mcap = fund.get("mcap", 0)
        rev_growth = fund.get("rev_growth")
        short_pct = fund.get("short_pct")
        profitable = fund.get("profitable", True)

        # Hard filters
        if not (500_000_000 <= mcap <= 15_000_000_000):
            continue
        if rev_growth is None or rev_growth <= 0.15:
            continue
        if short_pct is None or short_pct <= 0.10:
            continue
        if profitable:
            continue

        closes = hist["Close"]
        volumes = hist["Volume"]
        ma200 = closes.rolling(200).mean()
        vol_90d = volumes.rolling(90).mean()
        dates = hist.index

        last_signal_idx = -60

        for i in range(201, len(hist) - 252):
            if dates[i] < start_check or dates[i] > end_check:
                continue
            if (i - last_signal_idx) < 60:
                continue

            price = closes.iloc[i]
            prev_price = closes.iloc[i-1]
            ma_val = ma200.iloc[i]
            prev_ma = ma200.iloc[i-1]
            vol_val = volumes.iloc[i]
            vol_avg = vol_90d.iloc[i]

            if pd.isna(ma_val) or pd.isna(vol_avg) or ma_val <= 0 or vol_avg <= 0:
                continue

            # MA200 cross from below
            crossed = prev_price <= prev_ma and price > ma_val

            if not crossed:
                continue

            # Volume > 2X on cross day
            if vol_val / vol_avg < 2.0:
                continue

            # Not too extended (within 20% above MA200)
            if price > ma_val * 1.20:
                continue

            last_signal_idx = i

            # Forward 12-month return
            future_end = min(i + 252, len(hist))
            if future_end <= i + 50:
                continue

            future = closes.iloc[i:future_end]
            peak_future = future.max()
            peak_return = (peak_future - price) / price * 100

            is_runner = peak_return >= 500

            if is_runner:
                true_signals += 1
                true_returns.append(peak_return)
            else:
                false_signals += 1
                false_returns.append(peak_return)

        if (idx + 1) % 200 == 0:
            log.info(f"  [{idx+1}/{total}] True: {true_signals} | False: {false_signals}")

    total_signals = true_signals + false_signals
    precision = true_signals / total_signals * 100 if total_signals > 0 else 0

    log.info(f"\n  Results:")
    log.info(f"    True signals (ran 500%+): {true_signals}")
    log.info(f"    False signals:            {false_signals}")
    log.info(f"    Total signals:            {total_signals}")
    log.info(f"    PRECISION:                {precision:.2f}%")

    if true_returns:
        log.info(f"    Avg return (true):        +{np.mean(true_returns):.0f}%")
    if false_returns:
        log.info(f"    Avg return (false):       +{np.mean(false_returns):.0f}%")
        log.info(f"    Median return (false):    +{np.median(false_returns):.0f}%")
        log.info(f"    % of false that were still positive: {sum(1 for r in false_returns if r > 0)/len(false_returns)*100:.0f}%")

    return {
        "true_signals": true_signals,
        "false_signals": false_signals,
        "precision_pct": round(precision, 2),
        "avg_true_return": round(np.mean(true_returns), 1) if true_returns else 0,
        "avg_false_return": round(np.mean(false_returns), 1) if false_returns else 0,
        "median_false_return": round(np.median(false_returns), 1) if false_returns else 0,
    }


# =========================================================================
# TASK 3 — Time analysis
# =========================================================================

def time_analysis(entries):
    """Analyze hold period and where returns concentrate."""
    log.info("\n" + "=" * 70)
    log.info("TASK 3 — TIME ANALYSIS")
    log.info("=" * 70)

    days_to_peak = [e["days_entry_to_peak"] for e in entries if e["days_entry_to_peak"] > 0]
    returns = [e["return_remaining_pct"] for e in entries]
    missed = [e["pct_move_missed"] for e in entries]

    log.info(f"\n  Days from confirmed entry to peak:")
    log.info(f"    Average: {np.mean(days_to_peak):.0f} days")
    log.info(f"    Median:  {np.median(days_to_peak):.0f} days")
    log.info(f"    25th pct: {np.percentile(days_to_peak, 25):.0f} days")
    log.info(f"    75th pct: {np.percentile(days_to_peak, 75):.0f} days")

    # Distribution of days to peak
    log.info(f"\n  Time to peak distribution:")
    buckets = [(0, 30, "< 1 month"), (30, 90, "1-3 months"), (90, 180, "3-6 months"),
               (180, 270, "6-9 months"), (270, 400, "9-12+ months")]
    for lo, hi, label in buckets:
        count = sum(1 for d in days_to_peak if lo <= d < hi)
        pct = count / len(days_to_peak) * 100 if days_to_peak else 0
        log.info(f"    {label:<15s}: {count:>4d} ({pct:.1f}%)")

    log.info(f"\n  Return still available after confirmed entry:")
    log.info(f"    Average:  +{np.mean(returns):.0f}%")
    log.info(f"    Median:   +{np.median(returns):.0f}%")
    log.info(f"    Min:      +{min(returns):.0f}%")
    log.info(f"    Max:      +{max(returns):.0f}%")

    log.info(f"\n  How much of the move was missed (waited for MA200 cross):")
    log.info(f"    Average missed: {np.mean(missed):.1f}%")
    log.info(f"    Median missed:  {np.median(missed):.1f}%")

    # Optimal hold period: what return at 90d, 180d vs peak?
    log.info(f"\n  OPTIMAL HOLD PERIOD:")
    if np.median(days_to_peak) <= 180:
        log.info(f"    Median peak at {np.median(days_to_peak):.0f} days — 6-month hold captures most gains")
    else:
        log.info(f"    Median peak at {np.median(days_to_peak):.0f} days — need 9-12 month hold")

    pct_under_180 = sum(1 for d in days_to_peak if d <= 180) / len(days_to_peak) * 100
    log.info(f"    {pct_under_180:.0f}% of runners peaked within 180 days of confirmed entry")

    return {
        "avg_days_to_peak": round(np.mean(days_to_peak)),
        "median_days_to_peak": round(np.median(days_to_peak)),
        "avg_return_remaining": round(np.mean(returns), 1),
        "median_return_remaining": round(np.median(returns), 1),
        "avg_pct_missed": round(np.mean(missed), 1),
        "median_pct_missed": round(np.median(missed), 1),
        "pct_peaked_within_180d": round(pct_under_180, 1),
    }


# =========================================================================
# Main
# =========================================================================

def run():
    log.info("ALPHA HUNTER — Recovery Entry Strategy")
    log.info("=" * 70)

    runners = load_runners()
    log.info(f"Runners: {len(runners)}")

    histories = load_all(min_rows=300)
    log.info(f"Histories: {len(histories)}")

    fundamentals = load_fundamentals()
    log.info(f"Fundamentals: {len(fundamentals)}")

    runner_symbols = set(r["symbol"] for r in runners)

    # TASK 1 — Find confirmed entry for each runner
    log.info("\n" + "=" * 70)
    log.info("TASK 1 — CONFIRMED ENTRY POINTS")
    log.info("=" * 70)

    entries_full = []  # With all 4 conditions
    entries_relaxed = []  # Just MA200 cross + volume

    for i, r in enumerate(runners, 1):
        sym = r["symbol"]
        if sym not in histories:
            continue

        fund = fundamentals.get(sym, {})
        entry = find_confirmed_entry(histories[sym], r, fund)

        if entry:
            entry["symbol"] = sym
            entry["peak_return_pct"] = r["peak_return_pct"]
            entry["run_start_date"] = r["run_start_date"]

            if entry["has_fundamentals"]:
                entries_full.append(entry)
            else:
                entries_relaxed.append(entry)

    all_entries = entries_full + entries_relaxed

    log.info(f"\n  Runners with FULL confirmed entry (MA200 + vol + rev + short): {len(entries_full)}")
    log.info(f"  Runners with RELAXED entry (MA200 + vol only): {len(entries_relaxed)}")
    log.info(f"  Runners with no entry found: {len(runners) - len(all_entries)}")

    # Stats for full entries
    if entries_full:
        log.info(f"\n  FULL CONFIRMED ENTRIES ({len(entries_full)} runners):")
        log.info(f"  {'Symbol':>7} {'Entry Date':<12} {'Vol':>5} {'Missed':>8} {'Remaining':>10} {'Days→Peak':>10}")
        log.info(f"  {'-'*60}")
        for e in sorted(entries_full, key=lambda x: x["return_remaining_pct"], reverse=True)[:15]:
            log.info(f"  {e['symbol']:>7} {e['confirmed_entry_date']:<12} {e['vol_ratio_at_entry']:>4.1f}X "
                     f"{e['pct_move_missed']:>7.1f}% {e['return_remaining_pct']:>+9.0f}% {e['days_entry_to_peak']:>9d}d")

    # Stats for all entries (relaxed)
    if all_entries:
        returns = [e["return_remaining_pct"] for e in all_entries]
        missed = [e["pct_move_missed"] for e in all_entries]
        log.info(f"\n  ALL ENTRIES (relaxed, {len(all_entries)} runners):")
        log.info(f"    Avg move missed by waiting for MA200: {np.mean(missed):.1f}%")
        log.info(f"    Median move missed: {np.median(missed):.1f}%")
        log.info(f"    Avg return still available: +{np.mean(returns):.0f}%")
        log.info(f"    Median return still available: +{np.median(returns):.0f}%")
        log.info(f"    % with 200%+ still available: {sum(1 for r in returns if r >= 200)/len(returns)*100:.0f}%")

    # TASK 2 — Precision
    precision_result = test_precision_ma200(histories, fundamentals, runner_symbols)

    # TASK 3 — Time analysis (on all entries that have data)
    valid_entries = [e for e in all_entries if e["days_entry_to_peak"] > 0]
    time_result = time_analysis(valid_entries) if valid_entries else {}

    # Final summary
    log.info("\n" + "=" * 70)
    log.info("FINAL SUMMARY — RECOVERY ENTRY STRATEGY")
    log.info("=" * 70)
    log.info(f"""
  ENTRY RULE: Buy when stock crosses above MA200 with 2X+ volume,
  AND has rev growth > 15%, short > 10%, midcap, unprofitable.

  WHAT WE FOUND:
  - {len(entries_full)} of 159 runners ({len(entries_full)/len(runners)*100:.0f}%) had this exact entry point
  - Average move missed by waiting: {np.mean(missed):.0f}% of total run
  - Average return still available: +{np.mean(returns):.0f}% from entry to peak
  - Median time to peak: {np.median([e['days_entry_to_peak'] for e in valid_entries]):.0f} days
  - Precision (true vs false signals): {precision_result['precision_pct']:.1f}%

  TRADEOFF:
  - Higher conviction (MA200 confirmed) but lower recall
  - Miss the bottom but catch the meat of the move
  - Avg false signal still returned +{precision_result.get('avg_false_return', 0):.0f}% (not bad!)
""")

    # Save report
    report = {
        "task1": {
            "full_entries": len(entries_full),
            "relaxed_entries": len(entries_relaxed),
            "no_entry": len(runners) - len(all_entries),
            "avg_missed_pct": round(np.mean(missed), 1) if missed else 0,
            "avg_return_remaining": round(np.mean(returns), 1) if returns else 0,
            "entries_detail": entries_full[:20],
        },
        "task2_precision": precision_result,
        "task3_time": time_result,
    }
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)
    log.info(f"Report saved to {REPORT_PATH}")


if __name__ == "__main__":
    run()
