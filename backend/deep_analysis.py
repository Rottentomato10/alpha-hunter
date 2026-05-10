"""
ALPHA HUNTER — Deep Analysis
For each runner, build a full timeline: accumulation → launch → peak → entry window.
All data from cache. No network calls.
"""

import sqlite3
import sys
import logging
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cache.price_cache import load_all

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "db.sqlite"
CSV_PATH = DATA_DIR / "deep_analysis.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("deep_analysis")


def load_runners() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM runners ORDER BY peak_return_pct DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def analyze_runner(hist: pd.DataFrame, runner: dict) -> dict | None:
    """Build full timeline for one runner."""
    sd = pd.Timestamp(runner["run_start_date"])
    peak_date = pd.Timestamp(runner["peak_date"])

    # Need 90 days before + the run itself
    pre_90 = hist[hist.index < sd].tail(90)
    if len(pre_90) < 30:
        return None

    run_period = hist[(hist.index >= sd) & (hist.index <= peak_date)]
    if run_period.empty:
        return None

    closes_pre = pre_90["Close"]
    volumes_pre = pre_90["Volume"]
    avg_vol_90d = volumes_pre.mean()

    if avg_vol_90d <= 0:
        return None

    start_price = runner["run_start_price"]
    peak_price = runner["peak_price"]

    # =========================================================
    # PART 1 — Accumulation Phase
    # =========================================================

    # Find all volume spikes (2X+) in 90 days before run
    spike_mask = volumes_pre > avg_vol_90d * 2
    spike_dates = volumes_pre[spike_mask].index
    spike_count_90d = len(spike_dates)

    # First spike relative to run_start_date
    if spike_count_90d > 0:
        first_spike_date = spike_dates[0]
        days_first_spike_to_run = (sd - first_spike_date).days
        last_spike_date = spike_dates[-1]
        days_last_spike_to_run = (sd - last_spike_date).days
    else:
        days_first_spike_to_run = None
        days_last_spike_to_run = None

    # Price behavior during accumulation
    # Compare first half vs second half of pre-90
    if len(closes_pre) >= 60:
        first_half = closes_pre.iloc[:30].mean()
        second_half = closes_pre.iloc[-30:].mean()
        accum_price_change = round((second_half - first_half) / first_half * 100, 2)
        if accum_price_change > 5:
            accum_price_trend = "rising"
        elif accum_price_change < -5:
            accum_price_trend = "dropping"
        else:
            accum_price_trend = "flat"
    else:
        accum_price_change = 0
        accum_price_trend = "unknown"

    # =========================================================
    # PART 2 — Launch Event
    # =========================================================

    # What happened on run_start_date
    run_day = hist[hist.index == sd]
    if run_day.empty:
        # Use closest day
        after_start = hist[hist.index >= sd].head(1)
        if after_start.empty:
            return None
        run_day = after_start

    run_day_row = run_day.iloc[0]
    run_day_vol = run_day_row["Volume"]
    run_day_open = run_day_row["Open"]
    run_day_close = run_day_row["Close"]
    run_day_vol_ratio = round(run_day_vol / avg_vol_90d, 2) if avg_vol_90d > 0 else 1
    run_day_change_pct = round((run_day_close - run_day_open) / run_day_open * 100, 2) if run_day_open > 0 else 0

    # Gap up? Compare open to previous close
    pre_last = closes_pre.iloc[-1] if len(closes_pre) > 0 else run_day_open
    gap_pct = round((run_day_open - pre_last) / pre_last * 100, 2) if pre_last > 0 else 0

    # Launch type classification
    if gap_pct > 5 and run_day_vol_ratio > 3:
        launch_type = "explosive_gap"
    elif run_day_change_pct > 5 and run_day_vol_ratio > 2:
        launch_type = "strong_breakout"
    elif run_day_change_pct > 2:
        launch_type = "gradual_push"
    else:
        launch_type = "quiet_start"

    # =========================================================
    # PART 3 — Time to Peak
    # =========================================================

    days_to_peak = (peak_date - sd).days

    # Max drawdown during the run
    if len(run_period) > 1:
        run_closes = run_period["Close"]
        running_max = run_closes.expanding().max()
        drawdown = (run_closes - running_max) / running_max * 100
        max_drawdown_during_run = round(float(drawdown.min()), 2)

        # Was it straight up or had pullbacks?
        pullback_days = (drawdown < -10).sum()
        if pullback_days == 0:
            run_style = "straight_up"
        elif pullback_days < 10:
            run_style = "minor_pullbacks"
        else:
            run_style = "volatile_climb"
    else:
        max_drawdown_during_run = 0
        run_style = "unknown"

    # =========================================================
    # PART 4 — Entry Point Analysis
    # =========================================================

    # "Ideal entry" = first day where:
    #   a) volume spike (2X+) occurred in prior 5 days
    #   b) price is still within 15% of run_start_price
    # Look from 30 days before run to 30 days after run start

    search_start = hist[hist.index < sd].tail(30)
    search_after = hist[(hist.index >= sd)].head(30)
    search_window = pd.concat([search_start, search_after])

    ideal_entry_date = None
    ideal_entry_price = None
    entry_window_days = 0

    for i in range(5, len(search_window)):
        date = search_window.index[i]
        price = search_window["Close"].iloc[i]

        # Check: volume spike in prior 5 days
        prior_5 = search_window["Volume"].iloc[max(0, i-5):i]
        had_spike = (prior_5 > avg_vol_90d * 2).any()

        # Check: price within 15% of run_start_price
        within_range = abs(price - start_price) / start_price * 100 <= 15

        if had_spike and within_range:
            if ideal_entry_date is None:
                ideal_entry_date = date
                ideal_entry_price = price

            # Count how many days this entry window stays open
            entry_window_days += 1

    # Return from ideal entry to peak
    if ideal_entry_price and ideal_entry_price > 0:
        ideal_entry_return = round((peak_price - ideal_entry_price) / ideal_entry_price * 100, 2)
        days_entry_to_run = (sd - ideal_entry_date).days if ideal_entry_date < sd else -(ideal_entry_date - sd).days
    else:
        ideal_entry_return = None
        days_entry_to_run = None

    return {
        "symbol": runner["symbol"],
        "run_start_date": runner["run_start_date"],
        "peak_date": runner["peak_date"],
        "peak_return_pct": runner["peak_return_pct"],
        "sector": runner["sector"],
        # Part 1
        "spike_count_90d": spike_count_90d,
        "days_first_spike_to_run": days_first_spike_to_run,
        "days_last_spike_to_run": days_last_spike_to_run,
        "accum_price_trend": accum_price_trend,
        "accum_price_change_pct": accum_price_change,
        # Part 2
        "launch_vol_ratio": run_day_vol_ratio,
        "launch_change_pct": run_day_change_pct,
        "launch_gap_pct": gap_pct,
        "launch_type": launch_type,
        # Part 3
        "days_to_peak": days_to_peak,
        "max_drawdown_during_run_pct": max_drawdown_during_run,
        "run_style": run_style,
        # Part 4
        "ideal_entry_date": ideal_entry_date.strftime("%Y-%m-%d") if ideal_entry_date else None,
        "ideal_entry_price": round(ideal_entry_price, 4) if ideal_entry_price else None,
        "ideal_entry_return_pct": ideal_entry_return,
        "days_entry_to_run": days_entry_to_run,
        "entry_window_days": entry_window_days,
    }


def run():
    log.info("ALPHA HUNTER — Deep Analysis")
    log.info("=" * 60)

    runners = load_runners()
    log.info(f"Runners: {len(runners)}")

    log.info("Loading price cache...")
    histories = load_all(min_rows=100)
    log.info(f"Histories: {len(histories)}")

    results = []
    skipped = 0

    for i, r in enumerate(runners, 1):
        sym = r["symbol"]
        if sym not in histories:
            skipped += 1
            continue

        analysis = analyze_runner(histories[sym], r)
        if analysis:
            results.append(analysis)
        else:
            skipped += 1

        if i % 30 == 0:
            log.info(f"  [{i}/{len(runners)}] Analyzed: {len(results)} | Skipped: {skipped}")

    log.info(f"\nTotal analyzed: {len(results)} | Skipped: {skipped}")

    # Save CSV
    df = pd.DataFrame(results)
    df.to_csv(CSV_PATH, index=False)
    log.info(f"Saved to {CSV_PATH}")

    # =========================================================
    # Print summary table
    # =========================================================
    log.info("")
    log.info("=" * 130)
    log.info(f"{'Ticker':>7} {'Spk→Run':>8} {'SpkCnt':>7} {'Accum':>8} {'Launch':>15} {'DaysPk':>7} "
             f"{'MaxDD':>7} {'Style':>15} {'EntryRet':>9} {'Window':>7}")
    log.info("-" * 130)

    for r in results[:40]:
        spike_to_run = f"{r['days_first_spike_to_run']}d" if r['days_first_spike_to_run'] else "none"
        entry_ret = f"+{r['ideal_entry_return_pct']:.0f}%" if r['ideal_entry_return_pct'] else "—"
        window = f"{r['entry_window_days']}d" if r['entry_window_days'] else "—"

        log.info(
            f"{r['symbol']:>7} {spike_to_run:>8} {r['spike_count_90d']:>7} "
            f"{r['accum_price_trend']:>8} {r['launch_type']:>15} {r['days_to_peak']:>7} "
            f"{r['max_drawdown_during_run_pct']:>6.1f}% {r['run_style']:>15} "
            f"{entry_ret:>9} {window:>7}"
        )

    # =========================================================
    # Aggregate stats
    # =========================================================
    log.info("")
    log.info("=" * 70)
    log.info("AGGREGATE STATISTICS")
    log.info("=" * 70)

    # Days from first spike to run
    spike_days = [r["days_first_spike_to_run"] for r in results if r["days_first_spike_to_run"] is not None]
    if spike_days:
        log.info(f"\n  First volume spike appeared on average {np.mean(spike_days):.0f} days before run start")
        log.info(f"  Median: {np.median(spike_days):.0f} days before run")
        log.info(f"  Range: {min(spike_days)} to {max(spike_days)} days")

    # Spike count
    spike_counts = [r["spike_count_90d"] for r in results]
    log.info(f"\n  Average volume spikes in 90d before run: {np.mean(spike_counts):.1f}")
    log.info(f"  Median: {np.median(spike_counts):.0f}")
    log.info(f"  Runners with 0 spikes: {sum(1 for s in spike_counts if s == 0)} ({sum(1 for s in spike_counts if s == 0)/len(results)*100:.1f}%)")

    # Accumulation price trend
    trends = [r["accum_price_trend"] for r in results]
    for t in ["rising", "flat", "dropping"]:
        count = trends.count(t)
        log.info(f"  Price during accumulation — {t}: {count} ({count/len(results)*100:.1f}%)")

    # Launch type
    log.info(f"\n  Launch types:")
    launches = [r["launch_type"] for r in results]
    for lt in ["explosive_gap", "strong_breakout", "gradual_push", "quiet_start"]:
        count = launches.count(lt)
        log.info(f"    {lt}: {count} ({count/len(results)*100:.1f}%)")

    # Days to peak
    days_peaks = [r["days_to_peak"] for r in results]
    log.info(f"\n  Days from run start to peak:")
    log.info(f"    Average: {np.mean(days_peaks):.0f} days")
    log.info(f"    Median:  {np.median(days_peaks):.0f} days")
    log.info(f"    Range:   {min(days_peaks)} - {max(days_peaks)} days")

    # Max drawdown during run
    drawdowns = [r["max_drawdown_during_run_pct"] for r in results]
    log.info(f"\n  Max drawdown DURING the run (before hitting peak):")
    log.info(f"    Average: {np.mean(drawdowns):.1f}%")
    log.info(f"    Median:  {np.median(drawdowns):.1f}%")
    log.info(f"    Worst:   {min(drawdowns):.1f}%")
    log.info(f"    Runners with > 30% drawdown during run: {sum(1 for d in drawdowns if d < -30)} ({sum(1 for d in drawdowns if d < -30)/len(results)*100:.1f}%)")

    # Run style
    styles = [r["run_style"] for r in results]
    for s in ["straight_up", "minor_pullbacks", "volatile_climb"]:
        count = styles.count(s)
        log.info(f"  Run style — {s}: {count} ({count/len(results)*100:.1f}%)")

    # Entry point analysis
    entry_returns = [r["ideal_entry_return_pct"] for r in results if r["ideal_entry_return_pct"] is not None]
    entry_windows = [r["entry_window_days"] for r in results if r["entry_window_days"] and r["entry_window_days"] > 0]
    entry_to_run = [r["days_entry_to_run"] for r in results if r["days_entry_to_run"] is not None]

    if entry_returns:
        log.info(f"\n  IDEAL ENTRY ANALYSIS:")
        log.info(f"    Runners where ideal entry was found: {len(entry_returns)}/{len(results)} ({len(entry_returns)/len(results)*100:.1f}%)")
        log.info(f"    Average return from ideal entry to peak: +{np.mean(entry_returns):.0f}%")
        log.info(f"    Median return from ideal entry to peak:  +{np.median(entry_returns):.0f}%")

    if entry_windows:
        log.info(f"    Average entry window (days you had to spot it): {np.mean(entry_windows):.0f} days")
        log.info(f"    Median entry window: {np.median(entry_windows):.0f} days")

    if entry_to_run:
        log.info(f"    Average days from ideal entry to run start: {np.mean(entry_to_run):.0f} days")
        log.info(f"    (negative = entry was before run, positive = entry was after run started)")

    # Final summary
    log.info("")
    log.info("=" * 70)
    log.info("THE STORY IN NUMBERS")
    log.info("=" * 70)
    if spike_days and entry_returns and entry_windows and days_peaks:
        log.info(f"""
  1. Volume starts building {np.median(spike_days):.0f} days before the run
     (you see the accumulation ~{np.median(spike_days):.0f} days early)

  2. The typical launch is a "{max(set(launches), key=launches.count)}"
     (vol ratio {np.median([r['launch_vol_ratio'] for r in results]):.1f}X on day 1)

  3. Peak comes {np.median(days_peaks):.0f} days after run starts
     (but expect {np.median(drawdowns):.0f}% drawdowns along the way)

  4. If you caught the ideal entry, your return was +{np.median(entry_returns):.0f}%
     and you had ~{np.median(entry_windows):.0f} days to spot it

  5. {sum(1 for d in drawdowns if d < -30)}/{len(results)} runners ({sum(1 for d in drawdowns if d < -30)/len(results)*100:.0f}%)
     had a 30%+ drawdown DURING the run — diamond hands required
""")


if __name__ == "__main__":
    run()
