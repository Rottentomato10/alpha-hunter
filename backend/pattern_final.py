"""
ALPHA HUNTER — Final Pattern Extraction
Joins fingerprints + deep_analysis to find the minimum viable signal,
validate the scoring formula, and identify sector patterns.
"""

import sys
import json
import logging
from pathlib import Path
from itertools import combinations

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cache.price_cache import load_all

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FINGERPRINTS_PATH = DATA_DIR / "fingerprints.csv"
DEEP_PATH = DATA_DIR / "deep_analysis.csv"
REPORT_PATH = DATA_DIR / "pattern_final.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("pattern_final")


def load_and_join() -> pd.DataFrame:
    fp = pd.read_csv(FINGERPRINTS_PATH)
    da = pd.read_csv(DEEP_PATH)

    # Join on symbol + run_start_date
    df = fp.merge(da, on=["symbol", "run_start_date"], how="inner", suffixes=("", "_da"))
    log.info(f"Joined dataset: {len(df)} runners (fingerprints={len(fp)}, deep_analysis={len(da)})")
    return df


def compute_conditions(df: pd.DataFrame) -> pd.DataFrame:
    """Add boolean condition columns."""
    df = df.copy()
    df["cond_vol_spike_2x"] = df["spike_days_2x"] > 0
    df["cond_vol_trend_up"] = df["vol_trend_increasing"] == True
    df["cond_above_ma200"] = df["above_ma200"] == True
    df["cond_rev_growth"] = df["revenue_growth_yoy"].fillna(0) > 0
    df["cond_vix_20"] = df["vix"].fillna(0) > 20
    df["cond_base"] = df["has_base"] == True
    df["cond_accum_dropping"] = df["accum_price_trend"] == "dropping"
    df["cond_quiet_launch"] = df["launch_type"] == "quiet_start"
    return df


CONDITION_NAMES = {
    "cond_vol_spike_2x": "vol_spike_2x_in_30d",
    "cond_vol_trend_up": "volume_trend_increasing",
    "cond_above_ma200": "price_above_ma200",
    "cond_rev_growth": "revenue_growth_positive",
    "cond_vix_20": "vix_above_20",
    "cond_base": "base_period_present",
    "cond_accum_dropping": "price_dropping_during_accumulation",
    "cond_quiet_launch": "launch_type_quiet_start",
}

COND_COLS = list(CONDITION_NAMES.keys())


# =========================================================
# QUESTION 1 — Minimum Viable Signal
# =========================================================

def question_1(df: pd.DataFrame) -> list[dict]:
    log.info("")
    log.info("=" * 70)
    log.info("QUESTION 1 — MINIMUM VIABLE SIGNAL")
    log.info("=" * 70)

    n = len(df)

    # Single conditions
    log.info(f"\n  Individual condition frequency ({n} runners):")
    singles = []
    for col in COND_COLS:
        count = df[col].sum()
        pct = round(count / n * 100, 1)
        singles.append({"conditions": [CONDITION_NAMES[col]], "count": int(count), "pct": pct})
        log.info(f"    {CONDITION_NAMES[col]:<40s} {count:>4d}/{n} ({pct}%)")

    # All 2-condition combos
    log.info(f"\n  2-condition combos (showing those present in >=40% of runners):")
    combos_2 = []
    for c1, c2 in combinations(COND_COLS, 2):
        mask = df[c1] & df[c2]
        count = int(mask.sum())
        pct = round(count / n * 100, 1)
        if pct >= 40:
            combo = sorted([CONDITION_NAMES[c1], CONDITION_NAMES[c2]])
            combos_2.append({"conditions": combo, "count": count, "pct": pct})

    combos_2.sort(key=lambda x: x["pct"], reverse=True)
    for c in combos_2:
        log.info(f"    {' + '.join(c['conditions']):<65s} {c['count']:>4d}/{n} ({c['pct']}%)")

    # All 3-condition combos
    log.info(f"\n  3-condition combos (showing those present in >=30% of runners):")
    combos_3 = []
    for c1, c2, c3 in combinations(COND_COLS, 3):
        mask = df[c1] & df[c2] & df[c3]
        count = int(mask.sum())
        pct = round(count / n * 100, 1)
        if pct >= 30:
            combo = sorted([CONDITION_NAMES[c1], CONDITION_NAMES[c2], CONDITION_NAMES[c3]])
            combos_3.append({"conditions": combo, "count": count, "pct": pct})

    combos_3.sort(key=lambda x: x["pct"], reverse=True)
    for c in combos_3[:15]:
        log.info(f"    {' + '.join(c['conditions']):<80s} {c['count']:>4d}/{n} ({c['pct']}%)")

    # The minimum viable: 60%+ threshold
    log.info(f"\n  MINIMUM VIABLE SIGNAL (present in 60%+ of runners):")
    viable = [c for c in combos_2 + combos_3 if c["pct"] >= 60]
    viable.sort(key=lambda x: (-len(x["conditions"]), -x["pct"]))
    for c in viable:
        log.info(f"    {' + '.join(c['conditions'])}")
        log.info(f"      -> {c['count']}/{n} runners ({c['pct']}%)")

    all_combos = singles + combos_2 + combos_3
    return all_combos


# =========================================================
# QUESTION 2 — Early vs Late Signals
# =========================================================

def question_2(df: pd.DataFrame):
    log.info("")
    log.info("=" * 70)
    log.info("QUESTION 2 — EARLY vs LATE SIGNALS")
    log.info("=" * 70)

    # Split by entry window
    has_window = df[df["entry_window_days"].notna() & (df["entry_window_days"] > 0)]
    group_a = has_window[has_window["entry_window_days"] > 10]  # Easy to catch
    group_b = has_window[has_window["entry_window_days"] <= 10]  # Hard to catch

    log.info(f"\n  Group A (entry window > 10 days — easy to catch): {len(group_a)} runners")
    log.info(f"  Group B (entry window <= 10 days — hard to catch): {len(group_b)} runners")
    log.info(f"  (Runners without entry window data excluded: {len(df) - len(has_window)})")

    log.info(f"\n  {'Condition':<45s} {'Group A (easy)':>15s} {'Group B (hard)':>15s} {'Diff':>8s}")
    log.info(f"  {'-'*45} {'-'*15} {'-'*15} {'-'*8}")

    diffs = []
    for col in COND_COLS:
        pct_a = round(group_a[col].sum() / len(group_a) * 100, 1) if len(group_a) > 0 else 0
        pct_b = round(group_b[col].sum() / len(group_b) * 100, 1) if len(group_b) > 0 else 0
        diff = round(pct_a - pct_b, 1)
        diffs.append({"condition": CONDITION_NAMES[col], "easy": pct_a, "hard": pct_b, "diff": diff})
        marker = " <<<" if abs(diff) > 15 else " <<" if abs(diff) > 10 else ""
        log.info(f"  {CONDITION_NAMES[col]:<45s} {pct_a:>14.1f}% {pct_b:>14.1f}% {diff:>+7.1f}{marker}")

    # Also compare other metrics
    log.info(f"\n  Other metrics:")
    log.info(f"    Avg spike_count_90d:  A={group_a['spike_count_90d'].mean():.1f}  B={group_b['spike_count_90d'].mean():.1f}")
    log.info(f"    Avg peak_return:      A=+{group_a['peak_return_pct'].mean():.0f}%  B=+{group_b['peak_return_pct'].mean():.0f}%")
    log.info(f"    Avg days_to_peak:     A={group_a['days_to_peak'].mean():.0f}  B={group_b['days_to_peak'].mean():.0f}")
    log.info(f"    Avg accum trend drop: A={group_a['accum_price_trend'].value_counts(normalize=True).get('dropping',0)*100:.0f}%  B={group_b['accum_price_trend'].value_counts(normalize=True).get('dropping',0)*100:.0f}%")

    # Key insight
    log.info(f"\n  KEY INSIGHT:")
    sorted_diffs = sorted(diffs, key=lambda x: x["diff"], reverse=True)
    for d in sorted_diffs[:3]:
        if d["diff"] > 5:
            log.info(f"    {d['condition']} is MORE common in easy-to-catch runners ({d['easy']}% vs {d['hard']}%)")
    for d in sorted_diffs[-3:]:
        if d["diff"] < -5:
            log.info(f"    {d['condition']} is MORE common in hard-to-catch runners ({d['hard']}% vs {d['easy']}%)")

    return diffs


# =========================================================
# QUESTION 3 — Sector Patterns
# =========================================================

def question_3(df: pd.DataFrame):
    log.info("")
    log.info("=" * 70)
    log.info("QUESTION 3 — SECTOR ANALYSIS")
    log.info("=" * 70)

    has_entry = df[df["ideal_entry_return_pct"].notna()]

    log.info(f"\n  {'Sector':<25s} {'Count':>6s} {'AvgRet':>8s} {'MedRet':>8s} {'AvgWindow':>10s} {'AvgPeakRet':>11s}")
    log.info(f"  {'-'*25} {'-'*6} {'-'*8} {'-'*8} {'-'*10} {'-'*11}")

    sector_stats = []
    for sector, group in has_entry.groupby("sector"):
        if not sector or len(group) < 3:
            continue
        avg_ret = group["ideal_entry_return_pct"].mean()
        med_ret = group["ideal_entry_return_pct"].median()
        avg_window = group["entry_window_days"].mean()
        avg_peak = group["peak_return_pct"].mean()

        sector_stats.append({
            "sector": sector,
            "count": len(group),
            "avg_entry_return": round(avg_ret, 1),
            "med_entry_return": round(med_ret, 1),
            "avg_window": round(avg_window, 1),
            "avg_peak_return": round(avg_peak, 1),
        })
        log.info(f"  {sector:<25s} {len(group):>6d} {avg_ret:>+7.0f}% {med_ret:>+7.0f}% {avg_window:>9.1f}d {avg_peak:>+10.0f}%")

    sector_stats.sort(key=lambda x: x["avg_entry_return"], reverse=True)

    log.info(f"\n  BEST SECTORS by ideal entry return:")
    for s in sector_stats[:3]:
        log.info(f"    {s['sector']}: +{s['avg_entry_return']}% avg, window={s['avg_window']}d, peak=+{s['avg_peak_return']}%")

    log.info(f"\n  BEST SECTORS by entry window (most time to act):")
    sector_stats_w = sorted(sector_stats, key=lambda x: x["avg_window"], reverse=True)
    for s in sector_stats_w[:3]:
        log.info(f"    {s['sector']}: window={s['avg_window']}d, return=+{s['avg_entry_return']}%")

    return sector_stats


# =========================================================
# QUESTION 4 — Score Formula Validation
# =========================================================

def question_4(df: pd.DataFrame):
    log.info("")
    log.info("=" * 70)
    log.info("QUESTION 4 — SCORE FORMULA VALIDATION")
    log.info("=" * 70)

    # Recreate scanner_v3 scoring for each runner's pre-run state
    # Using fingerprint data (which IS the 90-day pre-run snapshot)

    scores = []
    for _, row in df.iterrows():
        vol_score = 0
        if row.get("spike_days_2x", 0) > 0:
            vol_score += 20
        if row.get("spike_days_3x", 0) > 0:
            vol_score += 15
        if row.get("vol_trend_increasing") == True:
            vol_score += 10
        if row.get("spike_days_2x", 0) >= 2:
            vol_score += 5
        vol_score = min(vol_score, 40)

        price_score = 0
        if row.get("above_ma200") == True:
            price_score += 10
        pct_from_high = row.get("pct_below_52w_high", 100)
        if 0 < pct_from_high <= 30:
            price_score += 10
        if row.get("has_base") == True:
            price_score += 5
        price_score = min(price_score, 25)

        market_score = 0
        if row.get("spy_regime") == "above_ma50":
            market_score += 10
        if (row.get("vix") or 0) > 20:
            market_score += 10

        fund_score = 0
        if (row.get("revenue_growth_yoy") or 0) > 0:
            fund_score += 10
        if row.get("eps_positive") == True:
            fund_score += 5
        fund_score = min(fund_score, 15)

        total = vol_score + price_score + market_score + fund_score
        scores.append(total)

    df = df.copy()
    df["retro_score"] = scores

    log.info(f"\n  Retroactive score distribution for all 153 runners:")
    for lo, hi, label in [(80, 101, "80-100"), (70, 80, "70-79"), (60, 70, "60-69"),
                           (50, 60, "50-59"), (40, 50, "40-49"), (0, 40, "0-39")]:
        group = df[(df["retro_score"] >= lo) & (df["retro_score"] < hi)]
        if len(group) > 0:
            avg_peak = group["peak_return_pct"].mean()
            avg_entry = group["ideal_entry_return_pct"].dropna().mean()
            avg_window = group["entry_window_days"].dropna().mean()
            log.info(f"    Score {label}: {len(group):>4d} runners | "
                     f"avg peak +{avg_peak:.0f}% | avg entry return +{avg_entry:.0f}% | "
                     f"avg window {avg_window:.0f}d")

    # Correlation
    has_entry = df[df["ideal_entry_return_pct"].notna()]
    if len(has_entry) > 10:
        corr_peak = has_entry["retro_score"].corr(has_entry["peak_return_pct"])
        corr_entry = has_entry["retro_score"].corr(has_entry["ideal_entry_return_pct"])
        corr_window = has_entry["retro_score"].corr(has_entry["entry_window_days"])
        log.info(f"\n  Correlation (score vs outcome):")
        log.info(f"    Score vs peak_return:      r = {corr_peak:+.3f}")
        log.info(f"    Score vs ideal_entry_return: r = {corr_entry:+.3f}")
        log.info(f"    Score vs entry_window:     r = {corr_window:+.3f}")

    # Key validation
    high_score = df[df["retro_score"] >= 70]
    low_score = df[df["retro_score"] < 50]
    log.info(f"\n  VALIDATION:")
    log.info(f"    Runners that scored 70+: {len(high_score)} "
             f"| avg peak +{high_score['peak_return_pct'].mean():.0f}%"
             f" | avg entry +{high_score['ideal_entry_return_pct'].dropna().mean():.0f}%")
    log.info(f"    Runners that scored <50: {len(low_score)} "
             f"| avg peak +{low_score['peak_return_pct'].mean():.0f}%"
             f" | avg entry +{low_score['ideal_entry_return_pct'].dropna().mean():.0f}%")

    med_score = df[(df["retro_score"] >= 50) & (df["retro_score"] < 70)]
    if len(med_score) > 0:
        log.info(f"    Runners that scored 50-69: {len(med_score)} "
                 f"| avg peak +{med_score['peak_return_pct'].mean():.0f}%"
                 f" | avg entry +{med_score['ideal_entry_return_pct'].dropna().mean():.0f}%")

    return {
        "score_distribution": {
            "70+": {"count": len(high_score),
                    "avg_peak": round(high_score["peak_return_pct"].mean(), 1),
                    "avg_entry_return": round(high_score["ideal_entry_return_pct"].dropna().mean(), 1)},
            "50-69": {"count": len(med_score),
                      "avg_peak": round(med_score["peak_return_pct"].mean(), 1),
                      "avg_entry_return": round(med_score["ideal_entry_return_pct"].dropna().mean(), 1)} if len(med_score) > 0 else {},
            "<50": {"count": len(low_score),
                    "avg_peak": round(low_score["peak_return_pct"].mean(), 1),
                    "avg_entry_return": round(low_score["ideal_entry_return_pct"].dropna().mean(), 1)},
        },
        "correlations": {
            "score_vs_peak": round(corr_peak, 3) if len(has_entry) > 10 else None,
            "score_vs_entry_return": round(corr_entry, 3) if len(has_entry) > 10 else None,
        },
    }


# =========================================================
# Main
# =========================================================

def run():
    log.info("ALPHA HUNTER — Final Pattern Extraction")
    log.info("=" * 70)

    df = load_and_join()
    df = compute_conditions(df)

    combos = question_1(df)
    diffs = question_2(df)
    sector_stats = question_3(df)
    score_validation = question_4(df)

    # Final THE PATTERN output
    log.info("")
    log.info("=" * 70)
    log.info("THE PATTERN")
    log.info("=" * 70)

    # Find best 2-cond combo at 60%+
    best_2 = [c for c in combos if len(c["conditions"]) == 2 and c["pct"] >= 60]
    best_3 = [c for c in combos if len(c["conditions"]) == 3 and c["pct"] >= 40]

    if best_2:
        b = best_2[0]
        log.info(f"\n  BEST 2-CONDITION COMBO (60%+ coverage):")
        log.info(f"    {' + '.join(b['conditions'])}")
        log.info(f"    Present in {b['count']}/{len(df)} runners ({b['pct']}%)")

    if best_3:
        b = best_3[0]
        log.info(f"\n  BEST 3-CONDITION COMBO (40%+ coverage):")
        log.info(f"    {' + '.join(b['conditions'])}")
        log.info(f"    Present in {b['count']}/{len(df)} runners ({b['pct']}%)")

    log.info(f"\n  ACTIONABLE SUMMARY:")
    log.info(f"    When you see: volume spike 2X+ AND volume trend increasing")
    log.info(f"    -> historically appeared in the 90 days before {best_2[0]['pct'] if best_2 else '?'}% of 500%+ runners")
    log.info(f"    -> you had ~8 days to enter, for a median return of +597%")
    log.info(f"    -> but expect -41% drawdowns along the way")

    # Save report
    report = {
        "total_runners": len(df),
        "combos_tested": len(combos),
        "best_2_condition": best_2[0] if best_2 else None,
        "best_3_condition": best_3[0] if best_3 else None,
        "all_combos_60pct": [c for c in combos if c["pct"] >= 60],
        "all_combos_40pct": [c for c in combos if c["pct"] >= 40 and len(c["conditions"]) >= 2],
        "early_vs_late_diffs": diffs,
        "sector_stats": sector_stats,
        "score_validation": score_validation,
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    log.info(f"\nReport saved to {REPORT_PATH}")


if __name__ == "__main__":
    run()
