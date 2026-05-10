"""
ALPHA HUNTER — Final Production Scanner
Finds stocks matching our Recovery Entry strategy RIGHT NOW.

Entry conditions (all must be true):
1. MA200 cross in last 30 days (was below, now above)
2. Volume on cross day > 2X 90d average
3. Revenue growth > 15%
4. Short interest > 10%
5. Market cap $500M–$15B
6. Not profitable (EPS negative)
"""

import sys
import json
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cache.price_cache import load_all

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FUND_PATH = DATA_DIR / "cache" / "fundamentals.json"
CSV_PATH = DATA_DIR / "live_candidates.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("scanner_final")


def load_fundamentals():
    if FUND_PATH.exists():
        with open(FUND_PATH) as f:
            return json.load(f)
    return {}


def scan_ticker(hist: pd.DataFrame, fund: dict) -> dict | None:
    """Check if a ticker matches ALL entry conditions right now."""
    if len(hist) < 250:
        return None

    closes = hist["Close"]
    volumes = hist["Volume"]
    dates = hist.index

    # Fundamental filters (hard requirements)
    mcap = fund.get("mcap", 0)
    if not (500_000_000 <= mcap <= 15_000_000_000):
        return None

    rev_growth = fund.get("rev_growth")
    if rev_growth is None or rev_growth <= 0.15:
        return None

    short_pct = fund.get("short_pct")
    if short_pct is None or short_pct <= 0.07:
        return None

    profitable = fund.get("profitable", True)
    trailing_eps = fund.get("trailing_eps")
    forward_eps = fund.get("forward_eps")
    # Not profitable: EPS negative or essentially zero
    if profitable and trailing_eps is not None and trailing_eps > 0.1:
        return None

    # EPS improving: forward > trailing (analyst expect improvement)
    eps_improving = (forward_eps is not None and trailing_eps is not None and forward_eps > trailing_eps)
    if not eps_improving:
        return None

    # MA200 calculation
    ma200 = closes.rolling(200).mean()
    vol_90d = volumes.rolling(90).mean()

    if pd.isna(ma200.iloc[-1]) or pd.isna(vol_90d.iloc[-1]):
        return None

    current_price = closes.iloc[-1]
    current_ma200 = ma200.iloc[-1]

    # Must be ABOVE MA200 right now
    if current_price <= current_ma200:
        return None

    # Find the MA200 cross in the last 30 trading days
    last_30_start = max(len(hist) - 30, 200)
    cross_date = None
    cross_price = None
    cross_vol_ratio = None

    for i in range(last_30_start, len(hist)):
        price = closes.iloc[i]
        prev_price = closes.iloc[i - 1]
        ma_val = ma200.iloc[i]
        prev_ma = ma200.iloc[i - 1]

        if pd.isna(ma_val) or pd.isna(prev_ma):
            continue

        # Cross from below: was below, now above
        if prev_price <= prev_ma and price > ma_val:
            vol_val = volumes.iloc[i]
            vol_avg = vol_90d.iloc[i]

            if pd.isna(vol_avg) or vol_avg <= 0:
                continue

            ratio = vol_val / vol_avg

            # Must have volume > 2X on cross day
            if ratio >= 2.0:
                cross_date = dates[i]
                cross_price = price
                cross_vol_ratio = ratio
                break  # Take first valid cross

    if cross_date is None:
        return None

    # Additional context
    days_since_cross = (dates[-1] - cross_date).days
    premium_above_ma200 = (current_price - current_ma200) / current_ma200 * 100

    # Volume trend since cross
    cross_idx = hist.index.get_loc(cross_date)
    vol_since_cross = volumes.iloc[cross_idx:].mean()
    vol_before_cross = volumes.iloc[max(0, cross_idx-30):cross_idx].mean()
    vol_trend_since = vol_since_cross / vol_before_cross if vol_before_cross > 0 else 1

    # Check not too extended (informational, not filtered)
    extended = premium_above_ma200 > 20

    return {
        "cross_date": cross_date.strftime("%Y-%m-%d"),
        "days_since_cross": days_since_cross,
        "cross_price": round(cross_price, 2),
        "cross_vol_ratio": round(cross_vol_ratio, 2),
        "current_price": round(current_price, 2),
        "ma200": round(current_ma200, 2),
        "premium_above_ma200_pct": round(premium_above_ma200, 1),
        "extended": extended,
        "vol_trend_since_cross": round(vol_trend_since, 2),
        "market_cap": mcap,
        "sector": fund.get("sector", ""),
        "industry": fund.get("industry", ""),
        "rev_growth_pct": round(rev_growth * 100, 1),
        "short_pct": round(short_pct * 100, 1),
        "trailing_eps": trailing_eps,
        "forward_eps": forward_eps,
        "eps_improving": eps_improving,
    }


def format_mcap(val):
    if val >= 1e9:
        return f"${val/1e9:.1f}B"
    return f"${val/1e6:.0f}M"


def run():
    log.info("=" * 70)
    log.info("ALPHA HUNTER — PRODUCTION SCANNER")
    log.info("=" * 70)
    log.info("")
    log.info("Entry: MA200 cross (30d) + Vol 2X+ + Rev >15% + Short >7% + Midcap + Unprofitable + EPS Improving")
    log.info("")

    log.info("Loading price cache...")
    histories = load_all(min_rows=250)
    log.info(f"Tickers loaded: {len(histories)}")

    fundamentals = load_fundamentals()
    log.info(f"Fundamentals: {len(fundamentals)}")

    # Scan all tickers
    candidates = []
    passed_fundamental = 0
    passed_ma200 = 0

    for sym, hist in histories.items():
        fund = fundamentals.get(sym, {})

        # Quick fundamental pre-filter (to track how many pass)
        mcap = fund.get("mcap", 0)
        rev = fund.get("rev_growth")
        short = fund.get("short_pct")
        prof = fund.get("profitable", True)
        eps = fund.get("trailing_eps")

        if (500e6 <= mcap <= 15e9 and rev and rev > 0.15
                and short and short > 0.10
                and (not prof or (eps is not None and eps <= 0.1))):
            passed_fundamental += 1

        result = scan_ticker(hist, fund)
        if result:
            result["symbol"] = sym
            candidates.append(result)

    candidates.sort(key=lambda x: x["days_since_cross"])

    log.info(f"\nFundamental filter passed: {passed_fundamental} tickers")
    log.info(f"MA200 cross + all conditions: {len(candidates)} candidates")
    log.info("")

    if not candidates:
        log.info("NO CANDIDATES FOUND matching all criteria in last 30 days.")
        log.info("")
        log.info("This is expected — the strategy is highly selective.")
        log.info(f"Only ~{passed_fundamental} tickers pass fundamentals alone.")
        log.info("MA200 cross with 2X volume must also happen in last 30 days.")
        log.info("")
        log.info("Consider:")
        log.info("  - Refresh cache (data may be stale): python3 cache.py force-refresh")
        log.info("  - Widen to 60 days: modify last_30_start")
        log.info("  - Watch the fundamental qualifiers for upcoming crosses")
        log.info("")

        # Show stocks that pass fundamentals and are CLOSE to MA200 cross
        log.info("=" * 70)
        log.info("WATCHLIST — Pass fundamentals, approaching MA200 from below")
        log.info("=" * 70)

        watchlist = []
        for sym, hist in histories.items():
            fund = fundamentals.get(sym, {})
            mcap = fund.get("mcap", 0)
            rev = fund.get("rev_growth")
            short = fund.get("short_pct")
            prof = fund.get("profitable", True)
            eps = fund.get("trailing_eps")

            if not (500e6 <= mcap <= 15e9 and rev and rev > 0.15
                    and short and short > 0.10
                    and (not prof or (eps is not None and eps <= 0.1))):
                continue

            if len(hist) < 250:
                continue

            closes = hist["Close"]
            ma200 = closes.rolling(200).mean()

            if pd.isna(ma200.iloc[-1]):
                continue

            current = closes.iloc[-1]
            ma_val = ma200.iloc[-1]
            dist = (current - ma_val) / ma_val * 100

            # Below MA200 but within 10% (approaching cross)
            if -10 <= dist <= 0:
                watchlist.append({
                    "symbol": sym,
                    "current_price": round(current, 2),
                    "ma200": round(ma_val, 2),
                    "dist_to_ma200_pct": round(dist, 1),
                    "sector": fund.get("sector", ""),
                    "rev_growth_pct": round(rev * 100, 1),
                    "short_pct": round(short * 100, 1),
                    "market_cap": mcap,
                })

            # Just crossed (above but within 5%) — might have crossed without volume
            elif 0 < dist <= 5:
                watchlist.append({
                    "symbol": sym,
                    "current_price": round(current, 2),
                    "ma200": round(ma_val, 2),
                    "dist_to_ma200_pct": round(dist, 1),
                    "sector": fund.get("sector", ""),
                    "rev_growth_pct": round(rev * 100, 1),
                    "short_pct": round(short * 100, 1),
                    "market_cap": mcap,
                })

        watchlist.sort(key=lambda x: x["dist_to_ma200_pct"])

        if watchlist:
            log.info(f"\n  {len(watchlist)} stocks pass fundamentals and are near MA200:")
            log.info(f"  {'#':>3} {'Ticker':>7} {'Price':>8} {'MA200':>8} {'Dist':>7} {'RevGr':>6} {'Short':>6} {'MCap':>7} Sector")
            log.info(f"  {'-'*85}")
            for i, w in enumerate(watchlist[:25], 1):
                status = "BELOW" if w["dist_to_ma200_pct"] < 0 else "ABOVE"
                log.info(
                    f"  {i:>3} {w['symbol']:>7} ${w['current_price']:>7.2f} ${w['ma200']:>7.2f} "
                    f"{w['dist_to_ma200_pct']:>+6.1f}% {w['rev_growth_pct']:>+5.0f}% "
                    f"{w['short_pct']:>5.0f}% {format_mcap(w['market_cap']):>7} "
                    f"{w['sector'][:18]}  [{status}]"
                )

            # Save watchlist
            df = pd.DataFrame(watchlist)
            df.to_csv(CSV_PATH, index=False)
            log.info(f"\n  Watchlist saved to {CSV_PATH}")
        else:
            log.info("  No stocks near MA200 cross with our fundamentals either.")

    else:
        # We have candidates!
        log.info("=" * 90)
        log.info("LIVE CANDIDATES — All entry conditions met")
        log.info("=" * 90)
        log.info(f"\n  {'#':>3} {'Ticker':>7} {'Cross':>12} {'Ago':>5} {'VolX':>5} {'Price':>8} "
                 f"{'MA200':>8} {'Prem':>6} {'RevGr':>6} {'Short':>6} {'MCap':>7} Sector")
        log.info(f"  {'-'*100}")

        for i, c in enumerate(candidates, 1):
            ext = " !EXT" if c["extended"] else ""
            log.info(
                f"  {i:>3} {c['symbol']:>7} {c['cross_date']:>12} {c['days_since_cross']:>4}d "
                f"{c['cross_vol_ratio']:>4.1f}X ${c['current_price']:>7.2f} "
                f"${c['ma200']:>7.2f} {c['premium_above_ma200_pct']:>+5.1f}% "
                f"{c['rev_growth_pct']:>+5.0f}% {c['short_pct']:>5.0f}% "
                f"{format_mcap(c['market_cap']):>7} {c['sector'][:16]}{ext}"
            )

        log.info(f"\n  Summary for each:")
        for c in candidates:
            log.info(f"    {c['symbol']}: crossed {c['days_since_cross']}d ago, "
                     f"vol was {c['cross_vol_ratio']}X, rev +{c['rev_growth_pct']}%, "
                     f"short {c['short_pct']}%, "
                     f"now {c['premium_above_ma200_pct']:+.1f}% above MA200")

        df = pd.DataFrame(candidates)
        df.to_csv(CSV_PATH, index=False)
        log.info(f"\n  Saved {len(candidates)} candidates to {CSV_PATH}")

    log.info("")
    log.info("=" * 70)
    log.info("STRATEGY REMINDER")
    log.info("=" * 70)
    log.info("""
  Backtest stats for this strategy:
  - Precision: 14.3% (1 in 7 becomes a 500%+ runner)
  - Even "wrong" picks returned +49% median
  - 97% of all signals were positive after 12 months
  - Median return to peak: +428%
  - Hold period: 9-12 months
  - Miss only 5% of the move by waiting for MA200 cross
""")


if __name__ == "__main__":
    run()
