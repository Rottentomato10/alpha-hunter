"""
ALPHA HUNTER — Live Scanner v3 (Revised)
Scoring based on actual runner DNA findings:
- Volume accumulation is king (76.5% of runners had 2X spikes)
- Price BELOW MA200 is bullish, not bearish (64% were below)
- Smart money accumulation = volume up + price down/flat
"""

import sys
import json
import logging
import argparse
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cache.price_cache import load_all

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FUND_PATH = DATA_DIR / "cache" / "fundamentals.json"
CSV_PATH = DATA_DIR / "live_scan_v2.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("scanner_v3")


def load_fundamentals() -> dict:
    if FUND_PATH.exists():
        with open(FUND_PATH) as f:
            return json.load(f)
    return {}


def score_ticker(hist: pd.DataFrame, fund: dict) -> dict | None:
    """Score based on runner DNA: volume accumulation + beaten down + rev growth."""
    if len(hist) < 200:
        return None

    closes = hist["Close"]
    volumes = hist["Volume"]
    highs = hist["High"]
    lows = hist["Low"]

    last_90 = hist.tail(90)
    last_30 = hist.tail(30)
    last_60 = hist.tail(60)

    if len(last_30) < 20:
        return None

    current_price = closes.iloc[-1]
    if current_price <= 0:
        return None

    vol_90d_avg = last_90["Volume"].mean()
    vol_30d_avg = last_30["Volume"].mean()
    vol_60d_avg = last_60["Volume"].mean()

    if vol_90d_avg <= 0:
        return None

    # =============================================
    # VOLUME SCORE (0-50)
    # =============================================
    vol_score = 0
    vol_reasons = []

    # Days with 2X spike in last 30 days
    spike_2x_days = int((last_30["Volume"] > vol_90d_avg * 2).sum())
    spike_3x_days = int((last_30["Volume"] > vol_90d_avg * 3).sum())

    if spike_2x_days > 0:
        vol_score += 25
        vol_reasons.append(f"2X spike ({spike_2x_days}d)")

    # Volume trend increasing (30d avg > 60d avg)
    vol_trend_up = vol_30d_avg > vol_60d_avg
    if vol_trend_up:
        vol_score += 15
        vol_reasons.append("vol trend up")

    # Multiple spike days not in same week
    if spike_2x_days >= 2:
        spike_mask = last_30["Volume"] > vol_90d_avg * 2
        spike_indices = spike_mask[spike_mask].index
        if len(spike_indices) >= 2:
            gaps = [(spike_indices[i+1] - spike_indices[i]).days for i in range(len(spike_indices)-1)]
            if any(g > 5 for g in gaps):
                vol_score += 10
                vol_reasons.append("separate spike events")

    vol_score = min(vol_score, 50)

    # Biggest spike ratio
    max_vol_ratio = round(last_30["Volume"].max() / vol_90d_avg, 2)

    # =============================================
    # ACCUMULATION PATTERN (0-30)
    # =============================================
    accum_score = 0
    accum_reasons = []

    # Price dropping while volume increasing = smart money accumulation
    price_30d_return = (closes.iloc[-1] - closes.iloc[-30]) / closes.iloc[-30] * 100 if len(closes) >= 30 else 0
    if price_30d_return < 0 and vol_trend_up:
        accum_score += 15
        accum_reasons.append(f"price down + vol up")

    # Price within 40% of 52w low (beaten down)
    low_52w = lows.tail(252).min() if len(lows) >= 252 else lows.min()
    high_52w = highs.tail(252).max() if len(highs) >= 252 else highs.max()
    if low_52w > 0:
        pct_above_low = (current_price - low_52w) / low_52w * 100
        range_52w = high_52w - low_52w
        if range_52w > 0:
            position_in_range = (current_price - low_52w) / range_52w * 100
        else:
            position_in_range = 50

        if position_in_range <= 40:
            accum_score += 10
            accum_reasons.append(f"near 52w low ({position_in_range:.0f}% range)")

    # Quiet price action (low volatility last 30d)
    if len(last_30) >= 20:
        daily_ret = last_30["Close"].pct_change().dropna()
        vol_30d_std = daily_ret.std() * 100
        if vol_30d_std < 2.5:  # Less than 2.5% daily volatility
            accum_score += 5
            accum_reasons.append("quiet/consolidating")

    accum_score = min(accum_score, 30)

    # =============================================
    # FUNDAMENTAL SCORE (0-20)
    # =============================================
    fund_score = 0
    fund_reasons = []

    rev_growth = fund.get("rev_growth")
    if rev_growth is not None and rev_growth > 0:
        fund_score += 15
        fund_reasons.append(f"rev +{rev_growth*100:.0f}%")

    mcap = fund.get("mcap", 0)
    if 500_000_000 <= mcap <= 10_000_000_000:
        fund_score += 5
        fund_reasons.append("midcap sweet spot")

    fund_score = min(fund_score, 20)

    # =============================================
    # TOTAL
    # =============================================
    total_score = vol_score + accum_score + fund_score

    # Price trend label
    if price_30d_return < -5:
        price_trend = "dropping"
    elif price_30d_return > 5:
        price_trend = "rising"
    else:
        price_trend = "flat"

    # Summary
    all_reasons = vol_reasons + accum_reasons + fund_reasons
    summary = " | ".join(all_reasons[:5]) if all_reasons else "—"

    # MA200 distance (informational, not scored)
    ma200 = closes.tail(200).mean() if len(closes) >= 200 else closes.mean()
    dist_ma200 = round((current_price - ma200) / ma200 * 100, 1) if ma200 > 0 else 0

    return {
        "score": total_score,
        "vol_score": vol_score,
        "accum_score": accum_score,
        "fund_score": fund_score,
        "current_price": round(current_price, 2),
        "market_cap": mcap,
        "sector": fund.get("sector", ""),
        "industry": fund.get("industry", ""),
        "max_vol_spike_30d": max_vol_ratio,
        "spike_days_2x": spike_2x_days,
        "spike_days_3x": spike_3x_days,
        "vol_trend_ratio": round(vol_30d_avg / vol_60d_avg, 2) if vol_60d_avg > 0 else 1,
        "price_30d_return_pct": round(price_30d_return, 1),
        "price_trend": price_trend,
        "dist_ma200_pct": dist_ma200,
        "position_in_52w_range_pct": round(position_in_range, 1) if low_52w > 0 else None,
        "summary": summary,
    }


def mini_timeline(hist: pd.DataFrame, symbol: str) -> str:
    """Show 90-day timeline in 2-week blocks."""
    last_90 = hist.tail(90)
    if len(last_90) < 60:
        return "Insufficient data"

    vol_90d_avg = last_90["Volume"].mean()
    lines = []
    # Split into ~6 blocks of ~15 trading days (about 2 weeks each)
    block_size = 15
    blocks = [last_90.iloc[i:i+block_size] for i in range(0, len(last_90), block_size)]

    for i, block in enumerate(blocks):
        if len(block) < 5:
            continue
        price_change = (block["Close"].iloc[-1] - block["Close"].iloc[0]) / block["Close"].iloc[0] * 100
        avg_vol = block["Volume"].mean()
        vol_ratio = avg_vol / vol_90d_avg if vol_90d_avg > 0 else 1
        spike_days = (block["Volume"] > vol_90d_avg * 2).sum()
        max_spike = block["Volume"].max() / vol_90d_avg if vol_90d_avg > 0 else 1

        week_label = f"Wk {i*2+1}-{i*2+2}"
        trend = "↑" if price_change > 2 else "↓" if price_change < -2 else "→"
        spike_note = f" *** {spike_days} spike days (max {max_spike:.1f}X)" if spike_days > 0 else ""

        lines.append(f"    {week_label}: price {trend} {price_change:+.1f}% | vol {vol_ratio:.2f}X avg{spike_note}")

    return "\n".join(lines)


def format_mcap(val):
    if not val: return "N/A"
    if val >= 1e12: return f"${val/1e12:.1f}T"
    if val >= 1e9: return f"${val/1e9:.1f}B"
    if val >= 1e6: return f"${val/1e6:.0f}M"
    return f"${val:,.0f}"


def run(min_score: int = 55, sector_filter: str = None):
    log.info("ALPHA HUNTER — Live Scanner v3 (DNA-Based)")
    log.info("=" * 70)
    log.info("Scoring: Volume Accumulation (0-50) + Smart Money Pattern (0-30) + Fundamentals (0-20)")
    log.info("")

    log.info("Loading price cache...")
    histories = load_all(min_rows=200)
    log.info(f"Tickers: {len(histories)}")

    fundamentals = load_fundamentals()
    log.info(f"Fundamentals: {len(fundamentals)}")

    # Score all
    results = []
    for sym, hist in histories.items():
        fund = fundamentals.get(sym, {})
        if sector_filter and sector_filter.lower() not in (fund.get("sector", "") or "").lower():
            continue

        result = score_ticker(hist, fund)
        if result and result["score"] >= min_score:
            result["symbol"] = sym
            results.append(result)

    results.sort(key=lambda x: x["score"], reverse=True)

    log.info(f"Tickers scoring >= {min_score}: {len(results)}")
    log.info("")

    # Print top 20
    log.info("=" * 120)
    log.info(f"{'#':>3} {'TICKER':>7} {'SCORE':>6} {'VOL':>4} {'ACC':>4} {'FND':>4}  "
             f"{'Price':>8} {'MCap':>8} {'Trend':>8} {'MaxSpk':>7} {'Spikes':>7} {'Sector':<18} Summary")
    log.info("-" * 120)

    for i, r in enumerate(results[:20], 1):
        hot = ">>>" if r["score"] >= 70 else "  >" if r["score"] >= 60 else "   "
        log.info(
            f"{hot}{i:>2} {r['symbol']:>7} {r['score']:>5}  "
            f"{r['vol_score']:>3} {r['accum_score']:>4} {r['fund_score']:>4}  "
            f"${r['current_price']:>7.2f} {format_mcap(r['market_cap']):>8} "
            f"{r['price_trend']:>8} {r['max_vol_spike_30d']:>5.1f}X {r['spike_days_2x']:>6}d  "
            f"{r['sector'][:16]:<18} {r['summary'][:45]}"
        )

    # Task 3 — Mini timeline for top 5
    log.info("")
    log.info("=" * 70)
    log.info("TOP 5 — 90-Day Accumulation Timeline")
    log.info("=" * 70)

    for r in results[:5]:
        sym = r["symbol"]
        hist = histories[sym]
        log.info(f"\n  {sym} (score={r['score']}) — {r['sector']} — ${r['current_price']} — MCap {format_mcap(r['market_cap'])}")
        log.info(f"  Price trend: {r['price_trend']} ({r['price_30d_return_pct']:+.1f}% in 30d) | MA200 distance: {r['dist_ma200_pct']:+.1f}%")
        log.info(f"  Volume: max spike {r['max_vol_spike_30d']:.1f}X | {r['spike_days_2x']} spike days | trend ratio {r['vol_trend_ratio']:.2f}")
        log.info(f"  52w range position: {r['position_in_52w_range_pct']:.0f}% (0%=at low, 100%=at high)")
        log.info(f"  Timeline:")
        log.info(mini_timeline(hist, sym))

    # Save CSV
    df = pd.DataFrame(results)
    if not df.empty:
        cols = ["symbol", "score", "vol_score", "accum_score", "fund_score",
                "current_price", "market_cap", "sector", "industry",
                "max_vol_spike_30d", "spike_days_2x", "spike_days_3x",
                "vol_trend_ratio", "price_30d_return_pct", "price_trend",
                "dist_ma200_pct", "position_in_52w_range_pct", "summary"]
        df[cols].to_csv(CSV_PATH, index=False)
        log.info(f"\nSaved {len(results)} results to {CSV_PATH}")

    # Summary
    if results:
        log.info(f"\nSUMMARY:")
        log.info(f"  Score 70+: {sum(1 for r in results if r['score'] >= 70)} stocks")
        log.info(f"  Score 60-69: {sum(1 for r in results if 60 <= r['score'] < 70)} stocks")
        log.info(f"  Score 55-59: {sum(1 for r in results if 55 <= r['score'] < 60)} stocks")
        log.info(f"  Avg max spike (top 20): {np.mean([r['max_vol_spike_30d'] for r in results[:20]]):.1f}X")
        dropping = sum(1 for r in results[:20] if r["price_trend"] == "dropping")
        log.info(f"  Top 20 with price DROPPING (accumulation!): {dropping}/20")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ALPHA HUNTER Scanner v3")
    parser.add_argument("--min-score", type=int, default=55)
    parser.add_argument("--sector", type=str, default=None)
    args = parser.parse_args()
    run(min_score=args.min_score, sector_filter=args.sector)
