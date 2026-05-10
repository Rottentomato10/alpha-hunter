"""
ALPHA HUNTER — EPS Acceleration Analysis
Fetch quarterly EPS for all runners, calculate acceleration metrics,
test against control group, update scanner.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "db.sqlite"
FUND_PATH = DATA_DIR / "cache" / "fundamentals.json"
EPS_CSV = DATA_DIR / "eps_analysis.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("eps")


# =========================================================================
# TASK 1 — Fetch quarterly EPS for all runners
# =========================================================================

def fetch_eps_for_runners():
    """Fetch quarterly earnings for each runner."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    runners = [dict(r) for r in conn.execute("SELECT * FROM runners ORDER BY peak_return_pct DESC").fetchall()]
    conn.close()

    log.info(f"TASK 1 — Fetching quarterly EPS for {len(runners)} runners")
    results = []
    failed = 0

    for i, r in enumerate(runners, 1):
        sym = r["symbol"]
        sd = r["run_start_date"]

        try:
            t = yf.Ticker(sym)

            # Get quarterly earnings
            earnings = t.quarterly_earnings
            if earnings is None or earnings.empty:
                # Try quarterly_financials as fallback
                financials = t.quarterly_financials
                if financials is not None and not financials.empty:
                    # financials has Revenue, Net Income, etc. as rows
                    earnings_data = []
                    if "Net Income" in financials.index:
                        for col in financials.columns:
                            date_str = col.strftime("%Y-%m-%d") if hasattr(col, 'strftime') else str(col)
                            ni = financials.loc["Net Income", col]
                            rev = financials.loc["Total Revenue", col] if "Total Revenue" in financials.index else None
                            earnings_data.append({"date": date_str, "net_income": ni, "revenue": rev})
                    if earnings_data:
                        # Filter to before run_start_date
                        before = [e for e in earnings_data if e["date"] < sd]
                        before.sort(key=lambda x: x["date"], reverse=True)
                        eps_values = [e.get("net_income", 0) for e in before[:8]]
                        rev_values = [e.get("revenue", 0) for e in before[:8]]
                    else:
                        failed += 1
                        continue
                else:
                    failed += 1
                    continue
            else:
                # quarterly_earnings has Earnings and Revenue columns
                # Filter to before run_start_date
                earnings.index = pd.to_datetime(earnings.index)
                before = earnings[earnings.index < pd.Timestamp(sd)].sort_index(ascending=False)
                if before.empty:
                    failed += 1
                    continue
                eps_values = before["Earnings"].head(8).tolist()
                rev_values = before["Revenue"].head(8).tolist() if "Revenue" in before.columns else []

            # Calculate metrics
            eps_values = [v for v in eps_values if v is not None and not (isinstance(v, float) and np.isnan(v))]
            rev_values = [v for v in rev_values if v is not None and not (isinstance(v, float) and np.isnan(v))]

            # EPS positive (last quarter)
            eps_positive = eps_values[0] > 0 if eps_values else None

            # EPS trend improving (Q0 > Q1)
            eps_trend = None
            if len(eps_values) >= 2:
                eps_trend = eps_values[0] > eps_values[1]

            # EPS acceleration (rate of improvement speeding up)
            # Q0-Q1 improvement > Q1-Q2 improvement
            eps_acceleration = None
            if len(eps_values) >= 3:
                delta_recent = eps_values[0] - eps_values[1]
                delta_prior = eps_values[1] - eps_values[2]
                eps_acceleration = delta_recent > delta_prior

            # Revenue acceleration (growth rate speeding up)
            rev_acceleration = None
            if len(rev_values) >= 3 and all(v > 0 for v in rev_values[:3]):
                growth_recent = (rev_values[0] - rev_values[1]) / rev_values[1]
                growth_prior = (rev_values[1] - rev_values[2]) / rev_values[2]
                rev_acceleration = growth_recent > growth_prior

            # Consecutive beats (estimate not available from yfinance easily,
            # approximate: consecutive QoQ improvements)
            consecutive_improvements = 0
            if len(eps_values) >= 2:
                for j in range(len(eps_values) - 1):
                    if eps_values[j] > eps_values[j + 1]:
                        consecutive_improvements += 1
                    else:
                        break

            results.append({
                "symbol": sym,
                "run_start_date": sd,
                "peak_return_pct": r["peak_return_pct"],
                "sector": r["sector"],
                "eps_q0": eps_values[0] if eps_values else None,
                "eps_q1": eps_values[1] if len(eps_values) > 1 else None,
                "eps_q2": eps_values[2] if len(eps_values) > 2 else None,
                "eps_q3": eps_values[3] if len(eps_values) > 3 else None,
                "eps_positive": eps_positive,
                "eps_trend_improving": eps_trend,
                "eps_acceleration": eps_acceleration,
                "rev_acceleration": rev_acceleration,
                "consecutive_improvements": consecutive_improvements,
                "quarters_available": len(eps_values),
            })

        except Exception as e:
            failed += 1
            log.debug(f"  {sym}: {e}")

        if i % 30 == 0:
            log.info(f"  [{i}/{len(runners)}] Success: {len(results)} | Failed: {failed}")
        time.sleep(0.3)

    log.info(f"\nDone: {len(results)} runners with EPS data, {failed} failed")

    df = pd.DataFrame(results)
    df.to_csv(EPS_CSV, index=False)
    log.info(f"Saved to {EPS_CSV}")

    # Summary
    log.info(f"\nEPS SUMMARY ({len(results)} runners with data):")
    if len(results) > 0:
        ep = sum(1 for r in results if r["eps_positive"] == True)
        et = sum(1 for r in results if r["eps_trend_improving"] == True)
        ea = sum(1 for r in results if r["eps_acceleration"] == True)
        ra = sum(1 for r in results if r["rev_acceleration"] == True)
        ci = sum(1 for r in results if r["consecutive_improvements"] >= 2)

        log.info(f"  EPS positive:       {ep}/{len(results)} ({ep/len(results)*100:.1f}%)")
        log.info(f"  EPS trend up:       {et}/{len(results)} ({et/len(results)*100:.1f}%)")
        log.info(f"  EPS accelerating:   {ea}/{len(results)} ({ea/len(results)*100:.1f}%)")
        log.info(f"  Rev accelerating:   {ra}/{len(results)} ({ra/len(results)*100:.1f}%)")
        log.info(f"  2+ consec improve:  {ci}/{len(results)} ({ci/len(results)*100:.1f}%)")

    return results


# =========================================================================
# TASK 2 — Test EPS filters against control group
# =========================================================================

def test_eps_filters():
    """Test how EPS filters affect precision using simulated control group logic."""
    log.info("\n" + "=" * 70)
    log.info("TASK 2 — Testing EPS filters against control group")
    log.info("=" * 70)

    # We can't re-fetch EPS for all 7,126 false signals (too many API calls).
    # Instead, use cached fundamentals as proxy:
    # - trailing_eps > 0 and forward_eps > trailing_eps = improving
    # - revenue_growth acceleration from cached data

    if FUND_PATH.exists():
        with open(FUND_PATH) as f:
            fundamentals = json.load(f)
    else:
        log.error("No fundamentals cache!")
        return

    # Load control group data
    from cache.price_cache import load_all
    histories = load_all(min_rows=300)

    conn = sqlite3.connect(str(DB_PATH))
    runner_symbols = set(r[0] for r in conn.execute("SELECT symbol FROM runners").fetchall())
    conn.close()

    start_check = pd.Timestamp("2019-01-01")
    end_check = pd.Timestamp("2024-06-30")

    # EPS filter definitions using cached fundamentals
    def has_eps_improving(fund):
        te = fund.get("trailing_eps")
        fe = fund.get("forward_eps")
        if te is not None and fe is not None:
            return fe > te
        return False

    def has_rev_growth_accel(fund):
        # Proxy: rev_growth > 0.20 (strong growth suggests acceleration)
        rg = fund.get("rev_growth")
        return rg is not None and rg > 0.20

    def has_both(fund):
        return has_eps_improving(fund) and has_rev_growth_accel(fund)

    # Test each filter combination with our MA200 cross signal
    configs = [
        {"name": "Base (current filters)", "extra": lambda f: True},
        {"name": "+ EPS improving (forward > trailing)", "extra": has_eps_improving},
        {"name": "+ Revenue acceleration (>20% growth)", "extra": has_rev_growth_accel},
        {"name": "+ Both (EPS improving + Rev >20%)", "extra": has_both},
    ]

    results = []

    for config in configs:
        true_signals = 0
        false_signals = 0
        extra_check = config["extra"]

        for sym, hist in histories.items():
            if len(hist) < 300:
                continue

            fund = fundamentals.get(sym, {})
            mcap = fund.get("mcap", 0)
            rev_growth = fund.get("rev_growth")
            short_pct = fund.get("short_pct")
            profitable = fund.get("profitable", True)

            # Base filters
            if not (500_000_000 <= mcap <= 15_000_000_000):
                continue
            if profitable:
                continue
            if rev_growth is None or rev_growth <= 0.15:
                continue
            if short_pct is None or short_pct <= 0.07:
                continue

            # Extra EPS filter
            if not extra_check(fund):
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
        per_year = total / 5.5

        results.append({
            "config": config["name"],
            "true": true_signals,
            "false": false_signals,
            "total": total,
            "precision": round(precision, 2),
            "per_year": round(per_year, 1),
        })

    log.info(f"\n  {'Filter':<50s} {'True':>5s} {'False':>6s} {'Total':>6s} {'Prec%':>7s} {'Sig/Yr':>7s}")
    log.info(f"  {'-'*50} {'-'*5} {'-'*6} {'-'*6} {'-'*7} {'-'*7}")
    for r in results:
        marker = " <<<" if r["precision"] > 15 and r["true"] >= 5 else ""
        log.info(f"  {r['config']:<50s} {r['true']:>5d} {r['false']:>6d} {r['total']:>6d} {r['precision']:>6.1f}% {r['per_year']:>6.1f}{marker}")

    # Recommendation
    log.info(f"\n  RECOMMENDATION:")
    best = max(results, key=lambda x: x["precision"] if x["true"] >= 5 else 0)
    log.info(f"    Best: {best['config']}")
    log.info(f"    Precision: {best['precision']}% | True signals: {best['true']} | Signals/year: {best['per_year']}")

    return results


# =========================================================================
# TASK 3 — Check watchlist stocks for EPS acceleration
# =========================================================================

def check_watchlist_eps():
    """Check MP, ALGM, OSCR for EPS acceleration."""
    log.info("\n" + "=" * 70)
    log.info("TASK 3 — Watchlist EPS Check")
    log.info("=" * 70)

    watchlist = ["MP", "ALGM", "OSCR"]
    results = {}

    for sym in watchlist:
        try:
            t = yf.Ticker(sym)
            info = t.info or {}

            trailing = info.get("trailingEps")
            forward = info.get("forwardEps")
            rev_growth = info.get("revenueGrowth")

            eps_improving = forward is not None and trailing is not None and forward > trailing
            rev_strong = rev_growth is not None and rev_growth > 0.20

            results[sym] = {
                "trailing_eps": trailing,
                "forward_eps": forward,
                "eps_improving": eps_improving,
                "rev_growth": round(rev_growth * 100, 1) if rev_growth else None,
                "rev_acceleration": rev_strong,
                "passes_eps_filter": eps_improving,
            }

            status = "✅ EPS מאיץ" if eps_improving else "⚠️ EPS לא מאיץ"
            log.info(f"  {sym}: trailing={trailing}, forward={forward}, {status}")
            log.info(f"    Rev growth: {results[sym]['rev_growth']}% | Rev accel (>20%): {'Yes' if rev_strong else 'No'}")

        except Exception as e:
            log.error(f"  {sym}: {e}")
            results[sym] = {"passes_eps_filter": None}
        time.sleep(0.5)

    return results


# =========================================================================
# Main
# =========================================================================

def run():
    log.info("ALPHA HUNTER — EPS Acceleration Analysis")
    log.info("=" * 70)

    # Task 1
    runner_eps = fetch_eps_for_runners()

    # Task 2
    filter_results = test_eps_filters()

    # Task 3
    watchlist_eps = check_watchlist_eps()

    # Save combined report
    report = {
        "runner_eps_summary": {
            "total_with_data": len(runner_eps),
            "eps_positive_pct": round(sum(1 for r in runner_eps if r.get("eps_positive") == True) / max(len(runner_eps), 1) * 100, 1),
            "eps_trend_up_pct": round(sum(1 for r in runner_eps if r.get("eps_trend_improving") == True) / max(len(runner_eps), 1) * 100, 1),
            "eps_accel_pct": round(sum(1 for r in runner_eps if r.get("eps_acceleration") == True) / max(len(runner_eps), 1) * 100, 1),
            "rev_accel_pct": round(sum(1 for r in runner_eps if r.get("rev_acceleration") == True) / max(len(runner_eps), 1) * 100, 1),
        },
        "filter_results": filter_results,
        "watchlist_eps": watchlist_eps,
    }

    report_path = DATA_DIR / "eps_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    log.info(f"\nReport saved to {report_path}")


if __name__ == "__main__":
    run()
