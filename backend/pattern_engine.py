"""
ALPHA HUNTER — Pattern Engine (Task 4)
Extracts repeatable patterns from runner fingerprints.
"""

import sqlite3
import sys
import json
import logging
from pathlib import Path
from collections import Counter
from itertools import combinations

import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "db.sqlite"
CSV_PATH = DATA_DIR / "fingerprints.csv"
REPORT_PATH = DATA_DIR / "pattern_report_v3.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("patterns")


def load_fingerprints() -> pd.DataFrame:
    if CSV_PATH.exists():
        return pd.read_csv(CSV_PATH)
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql("SELECT * FROM fingerprints", conn)
    conn.close()
    return df


def run():
    df = load_fingerprints()
    n = len(df)
    log.info(f"Loaded {n} runner fingerprints")

    if n == 0:
        log.error("No fingerprints found. Run fingerprint.py first.")
        return

    log.info("")
    log.info("=" * 70)
    log.info("VOLUME ANALYSIS")
    log.info("=" * 70)

    # Volume spike days before run
    log.info(f"\n  Runners with volume > 2X in 30d before run:")
    had_2x = (df["spike_days_2x"] > 0).sum()
    log.info(f"    {had_2x}/{n} = {had_2x/n*100:.1f}%")

    had_3x = (df["spike_days_3x"] > 0).sum()
    log.info(f"  Runners with volume > 3X in 30d before run:")
    log.info(f"    {had_3x}/{n} = {had_3x/n*100:.1f}%")

    log.info(f"\n  Median spike_days_2x (in 30d window): {df['spike_days_2x'].median():.0f}")
    log.info(f"  Median spike_days_3x (in 30d window): {df['spike_days_3x'].median():.0f}")

    # Volume ratio on run start
    vol_start = df["vol_ratio_on_start"].dropna()
    log.info(f"\n  Volume ratio ON run_start_date:")
    log.info(f"    Median: {vol_start.median():.2f}X")
    log.info(f"    Mean:   {vol_start.mean():.2f}X")
    log.info(f"    75th percentile: {vol_start.quantile(0.75):.2f}X")

    # Volume ratio buckets
    log.info(f"\n  Volume ratio distribution (on start date):")
    buckets = [(0, 1, "< 1X (below avg)"), (1, 2, "1-2X"), (2, 3, "2-3X"), (3, 5, "3-5X"), (5, 100, "5X+")]
    for lo, hi, label in buckets:
        count = ((vol_start >= lo) & (vol_start < hi)).sum()
        log.info(f"    {label:>20s}: {count:>4d} ({count/n*100:.1f}%)")

    # Max volume ratio in 30d before
    max_vol = df["max_vol_ratio_30d"].dropna()
    log.info(f"\n  Highest single-day volume in 30d before run:")
    log.info(f"    Median ratio: {max_vol.median():.2f}X")
    log.info(f"    Mean ratio:   {max_vol.mean():.2f}X")

    log.info("")
    log.info("=" * 70)
    log.info("SIGNAL CONVERGENCE")
    log.info("=" * 70)

    log.info(f"\n  Signal score distribution (0-6 signals present on start date):")
    for score in range(7):
        count = (df["signals_present"] == score).sum()
        pct = count / n * 100
        bar = "#" * int(pct / 2)
        log.info(f"    Score {score}: {count:>4d} ({pct:>5.1f}%)  {bar}")

    # Minimum score capturing 80% of runners
    cumulative = 0
    for score in range(7):
        cumulative += (df["signals_present"] == score).sum()
        if cumulative >= n * 0.80:
            log.info(f"\n  Score >= {max(score-1, 0)} captures 80%+ of runners")
            break

    # Median and mean score
    log.info(f"  Median score: {df['signals_present'].median():.1f}")
    log.info(f"  Mean score:   {df['signals_present'].mean():.2f}")

    log.info("")
    log.info("=" * 70)
    log.info("TOP INDIVIDUAL CONDITIONS")
    log.info("=" * 70)

    conditions = [
        ("above_ma200", "Price above MA200", lambda row: row.get("above_ma200") == True or row.get("above_ma200") == 1),
        ("green_on_start", "Green day on start", lambda row: row.get("green_on_start") == True or row.get("green_on_start") == 1),
        ("vol_trend_up", "Volume trend increasing (30d > 60d)", lambda row: row.get("vol_trend_increasing") == True or row.get("vol_trend_increasing") == 1),
        ("vol_3x_spike", "Had 3X volume spike in 30d before", lambda row: (row.get("spike_days_3x") or 0) > 0),
        ("vol_2x_spike", "Had 2X volume spike in 30d before", lambda row: (row.get("spike_days_2x") or 0) > 0),
        ("near_52w_high", "Within 20% of 52w high", lambda row: (row.get("pct_below_52w_high") or 100) <= 20),
        ("has_base", "Base pattern present (30d <15% range)", lambda row: row.get("has_base") == True or row.get("has_base") == 1),
        ("bull_fear", "VIX > 20 (elevated fear)", lambda row: (row.get("vix") or 0) > 20),
        ("spy_above_ma50", "SPY above MA50", lambda row: row.get("spy_regime") == "above_ma50"),
        ("rising_10d", "Price rising in prior 10 days (>5%)", lambda row: (row.get("ret_10d") or 0) > 5),
        ("rising_30d", "Price rising in prior 30 days (>10%)", lambda row: (row.get("ret_30d") or 0) > 10),
        ("vol_start_3x", "Volume 3X+ on run start date", lambda row: (row.get("vol_ratio_on_start") or 0) >= 3),
        ("rev_growth_pos", "Revenue growth positive", lambda row: (row.get("revenue_growth_yoy") or 0) > 0),
        ("eps_positive", "EPS positive (profitable)", lambda row: row.get("eps_positive") == True or row.get("eps_positive") == 1),
    ]

    condition_results = []
    for cond_id, desc, check_fn in conditions:
        count = 0
        for _, row in df.iterrows():
            if check_fn(row.to_dict()):
                count += 1
        pct = round(count / n * 100, 1)
        condition_results.append({"id": cond_id, "description": desc, "count": count, "pct": pct})

    condition_results.sort(key=lambda x: x["pct"], reverse=True)
    log.info(f"\n  Top conditions (sorted by % of runners with this condition):")
    for i, c in enumerate(condition_results, 1):
        log.info(f"    {i:>2d}. {c['description']:<45s} {c['count']:>4d}/{n}  ({c['pct']:>5.1f}%)")

    log.info("")
    log.info("=" * 70)
    log.info("TOP COMBINATIONS (3+ conditions co-occurring)")
    log.info("=" * 70)

    # For each runner, compute which conditions are true
    runner_conditions = []
    for _, row in df.iterrows():
        r = row.to_dict()
        active = []
        for cond_id, _, check_fn in conditions:
            if check_fn(r):
                active.append(cond_id)
        runner_conditions.append(set(active))

    # Find most common 3-condition combos
    combo_counter = Counter()
    top_conds = [c["id"] for c in condition_results[:10]]  # Only top 10 for combo search
    for combo in combinations(top_conds, 3):
        combo_set = set(combo)
        count = sum(1 for rc in runner_conditions if combo_set.issubset(rc))
        if count >= n * 0.3:  # At least 30% of runners
            combo_counter[combo] = count

    top_combos = combo_counter.most_common(10)
    log.info(f"\n  Most common 3-condition combinations (present in 30%+ of runners):")
    for combo, count in top_combos:
        pct = round(count / n * 100, 1)
        combo_desc = " + ".join(combo)
        log.info(f"    {combo_desc}")
        log.info(f"      -> {count}/{n} runners ({pct}%)")

    # THE FORMULA
    log.info("")
    log.info("=" * 70)
    log.info("THE FORMULA")
    log.info("=" * 70)

    if top_combos:
        best_combo, best_count = top_combos[0]
        best_pct = round(best_count / n * 100, 1)
        formula_conditions = " AND ".join(best_combo)
        log.info(f"\n  Stocks with [{formula_conditions}]")
        log.info(f"  historically preceded 500%+ runs in {best_pct}% of qualifying cases.")
        log.info(f"  ({best_count} out of {n} runners had all these conditions before their run)")

    # Save report
    report = {
        "total_runners": n,
        "volume_analysis": {
            "pct_with_2x_spike_30d": round(had_2x / n * 100, 1),
            "pct_with_3x_spike_30d": round(had_3x / n * 100, 1),
            "median_vol_ratio_on_start": round(vol_start.median(), 2),
            "mean_vol_ratio_on_start": round(vol_start.mean(), 2),
            "vol_ratio_distribution": {label: int(((vol_start >= lo) & (vol_start < hi)).sum()) for lo, hi, label in buckets},
        },
        "signal_convergence": {
            "distribution": {str(s): int((df["signals_present"] == s).sum()) for s in range(7)},
            "median_score": round(df["signals_present"].median(), 1),
            "mean_score": round(df["signals_present"].mean(), 2),
        },
        "top_conditions": condition_results,
        "top_combinations": [
            {"conditions": list(combo), "count": count, "pct": round(count / n * 100, 1)}
            for combo, count in top_combos
        ],
        "formula": {
            "conditions": list(best_combo) if top_combos else [],
            "pct_of_runners": best_pct if top_combos else 0,
            "count": best_count if top_combos else 0,
        },
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    log.info(f"\nReport saved to {REPORT_PATH}")


if __name__ == "__main__":
    run()
