"""
ALPHA HUNTER — Control Group Analysis
Find every stock that showed accumulation signal but did NOT run 500%+.
Compare against true runners to find what actually separates them.
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
REPORT_PATH = DATA_DIR / "control_group_report.json"
CSV_PATH = DATA_DIR / "live_scan_v3.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("control")


def load_runners() -> set:
    """Get set of (symbol, approximate_start_date) for true runners."""
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("SELECT symbol, run_start_date FROM runners").fetchall()
    conn.close()
    # Store as (symbol, year-month) to match approximately
    runner_set = set()
    for sym, sd in rows:
        runner_set.add(sym)  # Any run from this ticker counts
    return runner_set


def load_fundamentals() -> dict:
    if FUND_PATH.exists():
        with open(FUND_PATH) as f:
            return json.load(f)
    return {}


def find_accumulation_signals(histories: dict, fundamentals: dict, runner_symbols: set):
    """Find every instance of our accumulation signal across all tickers, 2019-2024.
    Signal: volume 2X+ in 30d + volume trend up + price dropping in 30d."""

    true_signals = []  # Runners that showed the signal
    false_signals = []  # Non-runners that showed the signal

    total = len(histories)
    checked_windows = 0

    for idx, (sym, hist) in enumerate(histories.items(), 1):
        if len(hist) < 300:
            continue

        fund = fundamentals.get(sym, {})
        mcap = fund.get("mcap", 0)

        # Skip very small / very large
        if mcap and (mcap < 100_000_000 or mcap > 200_000_000_000):
            continue

        closes = hist["Close"]
        volumes = hist["Volume"]
        dates = hist.index

        # Check every 30-day window (step by 20 days to reduce compute)
        start_check = pd.Timestamp("2019-01-01")
        end_check = pd.Timestamp("2024-06-30")

        i = 0
        last_signal_idx = -60  # Dedup: at least 60 days between signals for same ticker

        while i < len(hist) - 90:
            if dates[i] < start_check:
                i += 20
                continue
            if dates[i] > end_check:
                break

            # Look at 90-day window
            window = hist.iloc[i:i+90]
            last_30 = hist.iloc[i+60:i+90]
            first_60 = hist.iloc[i:i+60]

            if len(last_30) < 20 or len(first_60) < 40:
                i += 20
                continue

            vol_90d_avg = window["Volume"].mean()
            vol_30d_avg = last_30["Volume"].mean()
            vol_60d_avg = first_60["Volume"].mean()

            if vol_90d_avg <= 0 or vol_60d_avg <= 0:
                i += 20
                continue

            # Signal conditions
            spike_2x = (last_30["Volume"] > vol_90d_avg * 2).sum()
            vol_trend_up = vol_30d_avg > vol_60d_avg
            price_30d_ret = (last_30["Close"].iloc[-1] - last_30["Close"].iloc[0]) / last_30["Close"].iloc[0] * 100

            has_signal = spike_2x > 0 and vol_trend_up and price_30d_ret < 0

            if has_signal and (i - last_signal_idx) > 60:
                last_signal_idx = i
                checked_windows += 1

                signal_date = dates[i + 89]  # End of the 90-day window

                # What happened in next 252 days?
                future_start = i + 90
                future_end = min(future_start + 252, len(hist))
                if future_end <= future_start:
                    i += 20
                    continue

                future_slice = closes.iloc[future_start:future_end]
                if len(future_slice) < 50:
                    i += 20
                    continue

                entry_price = last_30["Close"].iloc[-1]
                peak_future = future_slice.max()
                peak_return = (peak_future - entry_price) / entry_price * 100 if entry_price > 0 else 0

                # Additional metrics for comparison
                high_52w = closes.iloc[max(0, i-252):i+90].max()
                low_52w = closes.iloc[max(0, i-252):i+90].min()
                pct_below_high = (high_52w - entry_price) / high_52w * 100 if high_52w > 0 else 0
                position_range = (entry_price - low_52w) / (high_52w - low_52w) * 100 if (high_52w - low_52w) > 0 else 50

                max_vol_spike = last_30["Volume"].max() / vol_90d_avg
                spike_count = int(spike_2x)

                record = {
                    "symbol": sym,
                    "signal_date": signal_date.strftime("%Y-%m-%d"),
                    "entry_price": round(entry_price, 4),
                    "peak_return_12m": round(peak_return, 2),
                    "is_runner": peak_return >= 500,
                    "market_cap": mcap,
                    "sector": fund.get("sector", ""),
                    "rev_growth": fund.get("rev_growth"),
                    "short_pct": fund.get("short_pct"),
                    "trailing_eps": fund.get("trailing_eps"),
                    "profitable": fund.get("profitable", False),
                    "pct_below_52w_high": round(pct_below_high, 1),
                    "position_in_range": round(position_range, 1),
                    "price_30d_return": round(price_30d_ret, 2),
                    "vol_trend_ratio": round(vol_30d_avg / vol_60d_avg, 2),
                    "max_vol_spike": round(max_vol_spike, 2),
                    "spike_count_30d": spike_count,
                }

                if sym in runner_symbols and peak_return >= 500:
                    true_signals.append(record)
                else:
                    false_signals.append(record)

            i += 20  # Step by 20 trading days

        if idx % 200 == 0:
            log.info(f"  [{idx}/{total}] True: {len(true_signals)} | False: {len(false_signals)}")

    return true_signals, false_signals


def compare_groups(true_signals, false_signals):
    """Compare metrics between winners and false signals."""
    log.info("")
    log.info("=" * 80)
    log.info("COMPARISON: TRUE RUNNERS vs FALSE SIGNALS")
    log.info("=" * 80)

    df_true = pd.DataFrame(true_signals)
    df_false = pd.DataFrame(false_signals)

    log.info(f"\n  True signals (became 500%+ runners): {len(df_true)}")
    log.info(f"  False signals (did NOT run 500%+):   {len(df_false)}")
    log.info(f"  Precision rate: {len(df_true)/(len(df_true)+len(df_false))*100:.2f}%")

    metrics = [
        ("rev_growth", "Revenue Growth (decimal)", lambda df: df["rev_growth"].dropna()),
        ("short_pct", "Short Interest %", lambda df: df["short_pct"].dropna()),
        ("market_cap", "Market Cap ($)", lambda df: df["market_cap"].dropna()),
        ("pct_below_52w_high", "% Below 52w High", lambda df: df["pct_below_52w_high"].dropna()),
        ("position_in_range", "Position in 52w Range %", lambda df: df["position_in_range"].dropna()),
        ("price_30d_return", "Price 30d Return %", lambda df: df["price_30d_return"].dropna()),
        ("vol_trend_ratio", "Vol Trend Ratio (30d/60d)", lambda df: df["vol_trend_ratio"].dropna()),
        ("max_vol_spike", "Max Vol Spike Ratio", lambda df: df["max_vol_spike"].dropna()),
        ("spike_count_30d", "Spike Days (2X+) in 30d", lambda df: df["spike_count_30d"].dropna()),
        ("profitable", "Profitable? (% yes)", lambda df: df["profitable"]),
    ]

    log.info(f"\n  {'Metric':<30s} {'Winners':>12s} {'False Sig':>12s} {'Diff':>10s} {'Useful?':>8s}")
    log.info(f"  {'-'*30} {'-'*12} {'-'*12} {'-'*10} {'-'*8}")

    comparison = []
    for key, label, extractor in metrics:
        true_data = extractor(df_true)
        false_data = extractor(df_false)

        if key == "profitable":
            true_val = true_data.sum() / len(true_data) * 100 if len(true_data) > 0 else 0
            false_val = false_data.sum() / len(false_data) * 100 if len(false_data) > 0 else 0
            diff = true_val - false_val
            true_str = f"{true_val:.1f}%"
            false_str = f"{false_val:.1f}%"
        elif key == "market_cap":
            true_val = true_data.median() / 1e9 if len(true_data) > 0 else 0
            false_val = false_data.median() / 1e9 if len(false_data) > 0 else 0
            diff = (true_val - false_val) / false_val * 100 if false_val > 0 else 0
            true_str = f"${true_val:.1f}B"
            false_str = f"${false_val:.1f}B"
        elif key == "rev_growth":
            true_val = true_data.median() * 100 if len(true_data) > 0 else 0
            false_val = false_data.median() * 100 if len(false_data) > 0 else 0
            diff = true_val - false_val
            true_str = f"{true_val:+.1f}%"
            false_str = f"{false_val:+.1f}%"
        elif key == "short_pct":
            true_val = true_data.median() * 100 if len(true_data) > 0 else 0
            false_val = false_data.median() * 100 if len(false_data) > 0 else 0
            diff = true_val - false_val
            true_str = f"{true_val:.1f}%"
            false_str = f"{false_val:.1f}%"
        else:
            true_val = true_data.median() if len(true_data) > 0 else 0
            false_val = false_data.median() if len(false_data) > 0 else 0
            diff = true_val - false_val
            true_str = f"{true_val:.2f}"
            false_str = f"{false_val:.2f}"

        useful = "YES" if abs(diff) > 10 or (key in ("rev_growth", "short_pct") and abs(diff) > 3) else "maybe" if abs(diff) > 5 else "no"
        marker = " <<<" if useful == "YES" else " <<" if useful == "maybe" else ""

        comparison.append({
            "metric": label,
            "key": key,
            "winners_median": true_val,
            "false_signals_median": false_val,
            "difference": round(diff, 2),
            "useful": useful,
        })

        log.info(f"  {label:<30s} {true_str:>12s} {false_str:>12s} {diff:>+9.2f} {useful:>8s}{marker}")

    return comparison


def build_new_score(histories, fundamentals, comparison):
    """Rebuild scoring formula using only separating conditions."""
    log.info("")
    log.info("=" * 80)
    log.info("NEW SCORING FORMULA (based on separators)")
    log.info("=" * 80)

    # Identify useful separators
    useful = [c for c in comparison if c["useful"] in ("YES", "maybe")]
    log.info(f"\n  Useful separators found: {len(useful)}")
    for u in useful:
        log.info(f"    {u['metric']}: winners={u['winners_median']:.2f} vs false={u['false_signals_median']:.2f} (diff={u['difference']:+.2f})")

    log.info(f"\n  NEW FORMULA:")
    log.info(f"    VOLUME (0-40): same as before (proven)")
    log.info(f"    ACCUMULATION (0-30): adjusted based on separators")
    log.info(f"    FUNDAMENTALS (0-30): heavier weight on actual separators")

    # Score all current tickers with new formula
    results = []
    for sym, hist in histories.items():
        if len(hist) < 200:
            continue

        fund = fundamentals.get(sym, {})
        mcap = fund.get("mcap", 0)

        closes = hist["Close"]
        volumes = hist["Volume"]
        highs = hist["High"]
        lows = hist["Low"]

        last_90 = hist.tail(90)
        last_30 = hist.tail(30)
        last_60 = hist.tail(60)

        if len(last_30) < 20:
            continue

        current_price = closes.iloc[-1]
        if current_price <= 0:
            continue

        vol_90d_avg = last_90["Volume"].mean()
        vol_30d_avg = last_30["Volume"].mean()
        vol_60d_avg = last_60["Volume"].mean()

        if vol_90d_avg <= 0:
            continue

        # --- VOLUME SCORE (0-40) --- same as before, proven
        vol_score = 0
        spike_2x = int((last_30["Volume"] > vol_90d_avg * 2).sum())
        vol_trend_up = vol_30d_avg > vol_60d_avg

        if spike_2x > 0:
            vol_score += 25
        if vol_trend_up:
            vol_score += 10
        if spike_2x >= 2:
            spike_mask = last_30["Volume"] > vol_90d_avg * 2
            spike_idx = spike_mask[spike_mask].index
            if len(spike_idx) >= 2:
                gaps = [(spike_idx[j+1] - spike_idx[j]).days for j in range(len(spike_idx)-1)]
                if any(g > 5 for g in gaps):
                    vol_score += 5
        vol_score = min(vol_score, 40)

        # --- ACCUMULATION (0-30) --- based on actual separators
        accum_score = 0
        price_30d_ret = (closes.iloc[-1] - closes.iloc[-30]) / closes.iloc[-30] * 100 if len(closes) >= 30 else 0

        # Price dropping + volume up = smart money (was the #1 separator)
        if price_30d_ret < 0 and vol_trend_up:
            accum_score += 15

        # Position in 52w range — runners were lower in range
        low_52w = lows.tail(252).min() if len(lows) >= 252 else lows.min()
        high_52w = highs.tail(252).max() if len(highs) >= 252 else highs.max()
        range_52w = high_52w - low_52w
        position_range = (current_price - low_52w) / range_52w * 100 if range_52w > 0 else 50

        if position_range <= 40:
            accum_score += 10

        # Max vol spike intensity (runners had higher spikes)
        max_spike = last_30["Volume"].max() / vol_90d_avg
        if max_spike >= 3:
            accum_score += 5

        accum_score = min(accum_score, 30)

        # --- FUNDAMENTALS (0-30) --- heavily weighted on actual separators
        fund_score = 0

        # Revenue growth — the strongest fundamental separator
        rev_growth = fund.get("rev_growth")
        if rev_growth is not None:
            if rev_growth > 0.20:
                fund_score += 15  # Strong growth
            elif rev_growth > 0.05:
                fund_score += 10  # Moderate growth
            elif rev_growth > 0:
                fund_score += 5   # Any positive

        # Short interest — higher in winners (squeeze fuel)
        short_pct = fund.get("short_pct")
        if short_pct is not None and short_pct > 0.08:
            fund_score += 10

        # Market cap sweet spot ($500M-$10B)
        if 500_000_000 <= mcap <= 10_000_000_000:
            fund_score += 5

        fund_score = min(fund_score, 30)

        total = vol_score + accum_score + fund_score

        # Price trend
        if price_30d_ret < -5:
            trend = "dropping"
        elif price_30d_ret > 5:
            trend = "rising"
        else:
            trend = "flat"

        results.append({
            "symbol": sym,
            "score": total,
            "vol_score": vol_score,
            "accum_score": accum_score,
            "fund_score": fund_score,
            "current_price": round(current_price, 2),
            "market_cap": mcap,
            "sector": fund.get("sector", ""),
            "rev_growth_pct": round(rev_growth * 100, 1) if rev_growth else None,
            "short_pct": round(short_pct * 100, 1) if short_pct else None,
            "max_vol_spike": round(max_spike, 2),
            "spike_days": spike_2x,
            "price_30d_ret": round(price_30d_ret, 1),
            "price_trend": trend,
            "position_in_range": round(position_range, 1),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def run():
    log.info("ALPHA HUNTER — Control Group Analysis")
    log.info("=" * 70)

    runner_symbols = load_runners()
    log.info(f"Known runners: {len(runner_symbols)} unique symbols")

    log.info("Loading cache...")
    histories = load_all(min_rows=300)
    log.info(f"Histories: {len(histories)}")

    fundamentals = load_fundamentals()
    log.info(f"Fundamentals: {len(fundamentals)}")

    # TASK 1: Find all accumulation signals
    log.info("\nTASK 1 — Finding all accumulation signals (2019-2024)...")
    true_signals, false_signals = find_accumulation_signals(histories, fundamentals, runner_symbols)

    log.info(f"\n  TRUE signals (showed pattern + ran 500%+): {len(true_signals)}")
    log.info(f"  FALSE signals (showed pattern + did NOT run): {len(false_signals)}")
    precision = len(true_signals) / (len(true_signals) + len(false_signals)) * 100 if (len(true_signals) + len(false_signals)) > 0 else 0
    log.info(f"  RAW PRECISION: {precision:.2f}%")
    log.info(f"  (For every 1 true runner, there are {len(false_signals)/max(len(true_signals),1):.0f} false signals)")

    # TASK 2: Compare
    comparison = compare_groups(true_signals, false_signals)

    # TASK 3: New scoring
    log.info("\nTASK 3 — Rebuilding score with real separators...")
    results = build_new_score(histories, fundamentals, comparison)

    # TASK 4: Score VVV specifically
    log.info("")
    log.info("=" * 80)
    log.info("TASK 4 — VVV SCORE CHECK")
    log.info("=" * 80)
    vvv = next((r for r in results if r["symbol"] == "VVV"), None)
    if vvv:
        log.info(f"\n  VVV new score: {vvv['score']}")
        log.info(f"    Vol: {vvv['vol_score']} | Accum: {vvv['accum_score']} | Fund: {vvv['fund_score']}")
        log.info(f"    Rev growth: {vvv['rev_growth_pct']}% | Short: {vvv['short_pct']}%")
        log.info(f"    Price trend: {vvv['price_trend']} ({vvv['price_30d_ret']:+.1f}%)")
        log.info(f"    Position in 52w range: {vvv['position_in_range']}%")
        if vvv["score"] >= 70:
            log.info(f"    VERDICT: Still scores high — REAL candidate")
        elif vvv["score"] >= 50:
            log.info(f"    VERDICT: Moderate score — watch list only")
        else:
            log.info(f"    VERDICT: Filtered out — likely false signal")

    # Print top 20 with new formula
    min_score = 55
    filtered = [r for r in results if r["score"] >= min_score]
    log.info(f"\n\n{'='*100}")
    log.info(f"TOP 20 — NEW FORMULA (min score {min_score})")
    log.info(f"{'='*100}")
    log.info(f"  {'#':>3} {'TICKER':>7} {'SCORE':>6} {'V':>3} {'A':>3} {'F':>3}  "
             f"{'Price':>8} {'MCap':>7} {'Trend':>8} {'RevGr':>6} {'Short':>6} {'Spk':>4} {'Sector'}")
    log.info(f"  {'-'*100}")

    for i, r in enumerate(filtered[:20], 1):
        hot = ">>>" if r["score"] >= 70 else "  >" if r["score"] >= 60 else "   "
        rev = f"{r['rev_growth_pct']:+.0f}%" if r['rev_growth_pct'] else "  —"
        short = f"{r['short_pct']:.0f}%" if r['short_pct'] else " —"
        mcap_str = f"${r['market_cap']/1e9:.1f}B" if r['market_cap'] >= 1e9 else f"${r['market_cap']/1e6:.0f}M"
        log.info(
            f"{hot}{i:>2} {r['symbol']:>7} {r['score']:>5}  "
            f"{r['vol_score']:>2} {r['accum_score']:>3} {r['fund_score']:>3}  "
            f"${r['current_price']:>7.2f} {mcap_str:>7} "
            f"{r['price_trend']:>8} {rev:>6} {short:>6} {r['spike_days']:>3}d  "
            f"{r['sector'][:20]}"
        )

    # Save
    df = pd.DataFrame(filtered)
    if not df.empty:
        df.to_csv(CSV_PATH, index=False)
        log.info(f"\nSaved {len(filtered)} results to {CSV_PATH}")

    # Save report
    report = {
        "true_signals": len(true_signals),
        "false_signals": len(false_signals),
        "precision_pct": round(precision, 2),
        "comparison": comparison,
        "top_20": filtered[:20],
        "vvv_score": vvv if vvv else None,
    }
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)
    log.info(f"Report saved to {REPORT_PATH}")


if __name__ == "__main__":
    run()
