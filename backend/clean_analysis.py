"""
ALPHA HUNTER — Clean Analysis
Remove meme/squeeze events, then compare top winners vs worst losers
to find filters that separate real breakouts from false signals.
"""

import sqlite3
import json
import sys
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

import yfinance as yf
import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "db.sqlite"
LOG_PATH = DATA_DIR / "clean_analysis.log"
REPORT_PATH = DATA_DIR / "clean_report.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("clean")

# Meme stocks to exclude entirely + any signal from 2021 Q1
MEME_TICKERS = {"GME", "AMC", "BBBY", "SPCE", "BB", "NOK"}
MEME_Q1_2021_START = "2021-01-01"
MEME_Q1_2021_END = "2021-03-31"

GAIN_THRESHOLD = 100

# ---------------------------------------------------------------------------
# Step 0 — Re-scan Bull Fear + Midcap signals (reuse logic)
# ---------------------------------------------------------------------------

def rescan_signals():
    """Re-scan for Bull Fear + Midcap signals, same as risk_analysis."""
    conn = sqlite3.connect(str(DB_PATH))
    symbols = [r[0] for r in conn.execute("SELECT symbol FROM tickers").fetchall()]
    conn.close()

    log.info("Loading SPY + VIX...")
    spy_hist = yf.Ticker("SPY").history(start="2017-01-01", end="2024-12-31", auto_adjust=True)
    vix_hist = yf.Ticker("^VIX").history(start="2017-01-01", end="2024-12-31", auto_adjust=True)
    spy_hist.index = spy_hist.index.tz_localize(None)
    vix_hist.index = vix_hist.index.tz_localize(None)

    spy_close = spy_hist["Close"]
    spy_ma50 = spy_close.rolling(50).mean()
    vix_close = vix_hist["Close"].reindex(spy_hist.index, method="ffill")

    def is_bull_fear(date):
        if date not in spy_close.index:
            mask = spy_close.index <= date
            if mask.any():
                date = spy_close.index[mask][-1]
            else:
                return False, 0
        idx = spy_close.index.get_loc(date)
        if idx < 50:
            return False, 0
        price = spy_close.iloc[idx]
        ma50 = spy_ma50.iloc[idx]
        v = vix_close.iloc[idx] if not pd.isna(vix_close.iloc[idx]) else 20
        return (price > ma50 and v >= 20), round(v, 2)

    log.info(f"Loading {len(symbols)} ticker histories...")
    histories = {}
    for i, sym in enumerate(symbols, 1):
        try:
            h = yf.Ticker(sym).history(start="2016-01-01", end="2025-12-31", auto_adjust=True)
            if h.empty or len(h) < 300:
                continue
            h.index = h.index.tz_localize(None)
            histories[sym] = h
        except:
            pass
        if i % 200 == 0:
            log.info(f"  Loaded {i}/{len(symbols)}")
        time.sleep(0.2)
    log.info(f"Loaded {len(histories)} tickers")

    log.info("Fetching fundamentals...")
    fund_cache = {}
    for i, sym in enumerate(histories.keys(), 1):
        try:
            info = yf.Ticker(sym).info or {}
            fund_cache[sym] = {
                "mcap": info.get("marketCap", 0) or 0,
                "rev_growth": info.get("revenueGrowth"),
                "trailing_eps": info.get("trailingEps"),
                "forward_eps": info.get("forwardEps"),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "short_pct": info.get("shortPercentOfFloat"),
                "profitable": (info.get("trailingEps") or 0) > 0,
            }
        except:
            fund_cache[sym] = {"mcap": 0}
        if i % 200 == 0:
            log.info(f"  Fundamentals: {i}/{len(histories)}")
        time.sleep(0.1)

    bt_start = pd.Timestamp("2018-01-01")
    bt_end = pd.Timestamp("2023-12-31")
    mondays = pd.date_range(bt_start, bt_end, freq="W-MON")

    signals = []
    last_signal = {}

    log.info(f"Scanning {len(mondays)} weeks...")

    for wk, monday in enumerate(mondays, 1):
        friday = monday + timedelta(days=4)

        for sym, hist in histories.items():
            fund = fund_cache.get(sym, {})
            mcap = fund.get("mcap", 0)
            if mcap < 500_000_000 or mcap > 20_000_000_000:
                continue

            try:
                week_data = hist[(hist.index >= monday) & (hist.index <= friday)]
                if week_data.empty:
                    continue

                pre_week = hist[hist.index < monday]
                if len(pre_week) < 200:
                    continue

                ma200 = pre_week["Close"].iloc[-200:].mean()
                high_52w = pre_week["Close"].iloc[-252:].max() if len(pre_week) >= 252 else pre_week["Close"].max()
                vol_ma20 = pre_week["Volume"].iloc[-20:].mean()

                if vol_ma20 <= 0 or ma200 <= 0 or high_52w <= 0:
                    continue

                for idx, row in week_data.iterrows():
                    close = row["Close"]
                    opn = row["Open"]
                    vol = row["Volume"]

                    if not (close > opn and vol >= vol_ma20 * 3.0 and close > ma200 and close >= high_52w * 0.50):
                        continue

                    bf, vix_val = is_bull_fear(idx)
                    if not bf:
                        continue

                    d = idx.to_pydatetime()
                    if sym in last_signal and (d - last_signal[sym]).days < 60:
                        continue
                    last_signal[sym] = d

                    sig_idx = hist.index.get_loc(idx)
                    if sig_idx + 252 >= len(hist):
                        continue

                    future = hist["Close"].iloc[sig_idx:sig_idx + 253]
                    fwd_return = round((future.iloc[-1] - close) / close * 100, 2)
                    fwd_max = round((future.max() - close) / close * 100, 2)

                    # Price action 10 days before signal
                    pre_10 = pre_week["Close"].iloc[-10:]
                    if len(pre_10) >= 10:
                        p10_start = pre_10.iloc[0]
                        p10_end = pre_10.iloc[-1]
                        p10_low = pre_10.min()
                        p10_change = (p10_end - p10_start) / p10_start * 100

                        if p10_change > 5:
                            prior_action = "rising"
                        elif p10_change < -5:
                            # Check if bouncing from drop
                            bounce_from_low = (p10_end - p10_low) / p10_low * 100 if p10_low > 0 else 0
                            prior_action = "bouncing" if bounce_from_low > 3 else "dropping"
                        else:
                            prior_action = "sideways"
                        p10_change_val = round(p10_change, 2)
                    else:
                        prior_action = "unknown"
                        p10_change_val = 0

                    date_str = idx.strftime("%Y-%m-%d")

                    signals.append({
                        "symbol": sym,
                        "date": date_str,
                        "price": round(close, 4),
                        "vol_ratio": round(vol / vol_ma20, 2),
                        "market_cap": mcap,
                        "fwd_12m_return": fwd_return,
                        "fwd_12m_max": fwd_max,
                        "vix": vix_val,
                        "sector": fund.get("sector", ""),
                        "industry": fund.get("industry", ""),
                        "short_pct": fund.get("short_pct"),
                        "profitable": fund.get("profitable", False),
                        "rev_growth": fund.get("rev_growth"),
                        "prior_10d_action": prior_action,
                        "prior_10d_change_pct": p10_change_val,
                    })
                    break

            except:
                continue

        if wk % 50 == 0:
            log.info(f"  Week {wk}/{len(mondays)} | Signals: {len(signals)}")

    log.info(f"Total raw signals: {len(signals)}")
    return signals


# ---------------------------------------------------------------------------
# Step 1 — Clean meme stocks
# ---------------------------------------------------------------------------

def clean_memes(signals):
    cleaned = []
    removed = 0
    for s in signals:
        # Remove meme tickers entirely
        if s["symbol"] in MEME_TICKERS:
            removed += 1
            continue
        # Remove any signal from 2021 Q1 (meme season)
        if MEME_Q1_2021_START <= s["date"] <= MEME_Q1_2021_END:
            removed += 1
            continue
        cleaned.append(s)
    log.info(f"Removed {removed} meme/Q1-2021 signals. Remaining: {len(cleaned)}")
    return cleaned


# ---------------------------------------------------------------------------
# Step 2 — Core stats
# ---------------------------------------------------------------------------

def core_stats(signals):
    returns = np.array([s["fwd_12m_return"] for s in signals])
    n = len(returns)
    hits = int(np.sum(returns >= GAIN_THRESHOLD))
    neg = returns[returns < 0]
    pos = returns[returns > 0]
    avg_win = float(np.mean(pos)) if len(pos) > 0 else 0
    avg_loss = float(np.mean(np.abs(neg))) if len(neg) > 0 else 1
    downside_std = float(np.std(neg)) if len(neg) > 0 else 1
    avg_ret = float(np.mean(returns))
    sortino = (avg_ret - 5) / downside_std if downside_std > 0 else 0

    return {
        "total_signals": n,
        "hit_rate_pct": round(hits / n * 100, 2) if n > 0 else 0,
        "hits_100pct": hits,
        "avg_return_pct": round(avg_ret, 2),
        "median_return_pct": round(float(np.median(returns)), 2),
        "std_pct": round(float(np.std(returns)), 2),
        "negative_pct": round(int(np.sum(returns < 0)) / n * 100, 1),
        "loss_30_pct": round(int(np.sum(returns < -30)) / n * 100, 1),
        "loss_50_pct": round(int(np.sum(returns < -50)) / n * 100, 1),
        "sortino": round(sortino, 3),
        "win_rate_pct": round(len(pos) / n * 100, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
    }


# ---------------------------------------------------------------------------
# Step 3 — Profile top winners and worst losers
# ---------------------------------------------------------------------------

def profile_group(group, label):
    n = len(group)
    sectors = defaultdict(int)
    mcaps = []
    shorts = []
    profitable_count = 0
    vixes = []
    prior_actions = defaultdict(int)
    rev_growths = []

    for s in group:
        sectors[s["sector"] or "Unknown"] += 1
        mcaps.append(s["market_cap"])
        if s["short_pct"] is not None:
            shorts.append(s["short_pct"] * 100)  # Convert to %
        if s["profitable"]:
            profitable_count += 1
        vixes.append(s["vix"])
        prior_actions[s["prior_10d_action"]] += 1
        if s["rev_growth"] is not None:
            rev_growths.append(s["rev_growth"] * 100)  # Convert to %

    top_sector = max(sectors, key=sectors.get) if sectors else "?"

    profile = {
        "label": label,
        "count": n,
        "top_sector": top_sector,
        "sector_breakdown": dict(sectors),
        "avg_market_cap": round(np.mean(mcaps)) if mcaps else 0,
        "median_market_cap": round(np.median(mcaps)) if mcaps else 0,
        "avg_short_pct": round(np.mean(shorts), 2) if shorts else None,
        "median_short_pct": round(np.median(shorts), 2) if shorts else None,
        "profitable_pct": round(profitable_count / n * 100, 1) if n > 0 else 0,
        "avg_vix": round(np.mean(vixes), 2) if vixes else 0,
        "prior_action_breakdown": dict(prior_actions),
        "prior_rising_pct": round(prior_actions.get("rising", 0) / n * 100, 1) if n > 0 else 0,
        "prior_sideways_pct": round(prior_actions.get("sideways", 0) / n * 100, 1) if n > 0 else 0,
        "prior_bouncing_pct": round(prior_actions.get("bouncing", 0) / n * 100, 1) if n > 0 else 0,
        "avg_rev_growth_pct": round(np.mean(rev_growths), 2) if rev_growths else None,
        "median_rev_growth_pct": round(np.median(rev_growths), 2) if rev_growths else None,
        "stocks": [
            {
                "symbol": s["symbol"],
                "date": s["date"],
                "fwd_return": s["fwd_12m_return"],
                "sector": s["sector"],
                "mcap_M": round(s["market_cap"] / 1e6),
                "short_pct": round(s["short_pct"] * 100, 1) if s["short_pct"] else None,
                "profitable": s["profitable"],
                "vix": s["vix"],
                "prior_action": s["prior_10d_action"],
                "rev_growth_pct": round(s["rev_growth"] * 100, 1) if s["rev_growth"] else None,
            }
            for s in group
        ],
    }
    return profile


# ---------------------------------------------------------------------------
# Step 4 — Suggest filters
# ---------------------------------------------------------------------------

def suggest_filters(winners_profile, losers_profile, all_signals):
    suggestions = []

    # Test candidate filters
    filters = [
        {
            "name": "Revenue Growth > 10%",
            "cond": lambda s: s["rev_growth"] is not None and s["rev_growth"] > 0.10,
        },
        {
            "name": "Profitable (EPS > 0)",
            "cond": lambda s: s["profitable"],
        },
        {
            "name": "Short Interest < 15%",
            "cond": lambda s: s["short_pct"] is not None and s["short_pct"] < 0.15,
        },
        {
            "name": "Prior 10d Rising or Sideways (no drop)",
            "cond": lambda s: s["prior_10d_action"] in ("rising", "sideways"),
        },
        {
            "name": "MCap $1B-$10B",
            "cond": lambda s: 1e9 <= s["market_cap"] <= 10e9,
        },
        {
            "name": "VIX 20-30 (elevated but not panic)",
            "cond": lambda s: 20 <= s["vix"] <= 30,
        },
        {
            "name": "Rev Growth > 10% AND Profitable",
            "cond": lambda s: s["rev_growth"] is not None and s["rev_growth"] > 0.10 and s["profitable"],
        },
        {
            "name": "Rev Growth > 10% AND Prior Rising/Sideways",
            "cond": lambda s: s["rev_growth"] is not None and s["rev_growth"] > 0.10 and s["prior_10d_action"] in ("rising", "sideways"),
        },
        {
            "name": "Profitable AND Prior Rising/Sideways AND MCap $1B-$10B",
            "cond": lambda s: s["profitable"] and s["prior_10d_action"] in ("rising", "sideways") and 1e9 <= s["market_cap"] <= 10e9,
        },
        {
            "name": "Rev Growth > 10% AND Profitable AND Short < 15%",
            "cond": lambda s: (s["rev_growth"] is not None and s["rev_growth"] > 0.10
                               and s["profitable"]
                               and s["short_pct"] is not None and s["short_pct"] < 0.15),
        },
        {
            "name": "Rev Growth > 5% AND MCap $1B-$10B AND Prior Rising/Sideways",
            "cond": lambda s: (s["rev_growth"] is not None and s["rev_growth"] > 0.05
                               and 1e9 <= s["market_cap"] <= 10e9
                               and s["prior_10d_action"] in ("rising", "sideways")),
        },
    ]

    for f in filters:
        passing = [s for s in all_signals if f["cond"](s) and s["fwd_12m_return"] is not None]
        if len(passing) < 10:
            continue
        returns = np.array([s["fwd_12m_return"] for s in passing])
        hits = int(np.sum(returns >= 100))
        neg = int(np.sum(returns < 0))

        suggestions.append({
            "filter": f["name"],
            "sample": len(passing),
            "hits_100pct": hits,
            "hit_rate_pct": round(hits / len(passing) * 100, 2),
            "avg_return_pct": round(float(np.mean(returns)), 2),
            "median_return_pct": round(float(np.median(returns)), 2),
            "negative_pct": round(neg / len(passing) * 100, 1),
            "loss_30_pct": round(int(np.sum(returns < -30)) / len(passing) * 100, 1),
        })

    suggestions.sort(key=lambda x: x["hit_rate_pct"], reverse=True)
    return suggestions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    log.info("=" * 60)
    log.info("CLEAN ANALYSIS — Meme-Free, Winners vs Losers")
    log.info("=" * 60)

    # Scan
    raw_signals = rescan_signals()
    log.info(f"Raw signals: {len(raw_signals)}")

    # Clean
    signals = clean_memes(raw_signals)

    # Core stats
    stats = core_stats(signals)
    log.info("")
    log.info("=" * 60)
    log.info("CORE STATS (MEME-FREE)")
    log.info("=" * 60)
    for k, v in stats.items():
        log.info(f"  {k}: {v}")

    # Sort by return
    sorted_signals = sorted(
        [s for s in signals if s["fwd_12m_return"] is not None],
        key=lambda x: x["fwd_12m_return"], reverse=True,
    )

    top_15 = sorted_signals[:15]
    bottom_15 = sorted_signals[-15:]

    # Profile
    winners_prof = profile_group(top_15, "Top 15 Winners")
    losers_prof = profile_group(bottom_15, "Bottom 15 Losers")

    log.info("")
    log.info("=" * 60)
    log.info("TOP 15 WINNERS (organic, no memes)")
    log.info("=" * 60)
    for s in winners_prof["stocks"]:
        log.info(f"  {s['symbol']:>6s} {s['date']}  +{s['fwd_return']:>8.1f}%  {s['sector']:<25s}  MCap ${s['mcap_M']}M  "
                 f"Short={s['short_pct'] or '?'}%  Prof={'Y' if s['profitable'] else 'N'}  "
                 f"VIX={s['vix']}  Prior={s['prior_action']}  RevGr={s['rev_growth_pct'] or '?'}%")

    log.info("")
    log.info("=" * 60)
    log.info("BOTTOM 15 LOSERS")
    log.info("=" * 60)
    for s in losers_prof["stocks"]:
        log.info(f"  {s['symbol']:>6s} {s['date']}  {s['fwd_return']:>+8.1f}%  {s['sector']:<25s}  MCap ${s['mcap_M']}M  "
                 f"Short={s['short_pct'] or '?'}%  Prof={'Y' if s['profitable'] else 'N'}  "
                 f"VIX={s['vix']}  Prior={s['prior_action']}  RevGr={s['rev_growth_pct'] or '?'}%")

    log.info("")
    log.info("=" * 80)
    log.info("SIDE-BY-SIDE COMPARISON")
    log.info("=" * 80)
    log.info(f"  {'Metric':<30s} {'Top 15 Winners':>20s} {'Bottom 15 Losers':>20s}")
    log.info(f"  {'-'*30} {'-'*20} {'-'*20}")
    log.info(f"  {'Top Sector':<30s} {winners_prof['top_sector']:>20s} {losers_prof['top_sector']:>20s}")
    log.info(f"  {'Avg Market Cap ($M)':<30s} {winners_prof['avg_market_cap']/1e6:>20,.0f} {losers_prof['avg_market_cap']/1e6:>20,.0f}")
    log.info(f"  {'Avg Short Interest %':<30s} {str(winners_prof['avg_short_pct'])+'%' if winners_prof['avg_short_pct'] else 'N/A':>20s} {str(losers_prof['avg_short_pct'])+'%' if losers_prof['avg_short_pct'] else 'N/A':>20s}")
    log.info(f"  {'Profitable? (% yes)':<30s} {str(winners_prof['profitable_pct'])+'%':>20s} {str(losers_prof['profitable_pct'])+'%':>20s}")
    log.info(f"  {'Avg VIX':<30s} {winners_prof['avg_vix']:>20.1f} {losers_prof['avg_vix']:>20.1f}")
    log.info(f"  {'Prior 10d: Rising %':<30s} {str(winners_prof['prior_rising_pct'])+'%':>20s} {str(losers_prof['prior_rising_pct'])+'%':>20s}")
    log.info(f"  {'Prior 10d: Sideways %':<30s} {str(winners_prof['prior_sideways_pct'])+'%':>20s} {str(losers_prof['prior_sideways_pct'])+'%':>20s}")
    log.info(f"  {'Prior 10d: Bouncing %':<30s} {str(winners_prof['prior_bouncing_pct'])+'%':>20s} {str(losers_prof['prior_bouncing_pct'])+'%':>20s}")
    log.info(f"  {'Avg Revenue Growth %':<30s} {str(winners_prof['avg_rev_growth_pct'])+'%' if winners_prof['avg_rev_growth_pct'] else 'N/A':>20s} {str(losers_prof['avg_rev_growth_pct'])+'%' if losers_prof['avg_rev_growth_pct'] else 'N/A':>20s}")

    # Suggest filters
    log.info("")
    log.info("=" * 80)
    log.info("FILTER SUGGESTIONS — Testing combinations on full signal set")
    log.info("=" * 80)
    suggestions = suggest_filters(winners_prof, losers_prof, signals)
    log.info(f"  {'Filter':<55s} {'Sample':>7s} {'Hits':>5s} {'Rate':>7s} {'AvgRet':>8s} {'MedRet':>8s} {'Neg%':>6s} {'L30%':>6s}")
    log.info(f"  {'-'*55} {'-'*7} {'-'*5} {'-'*7} {'-'*8} {'-'*8} {'-'*6} {'-'*6}")
    for s in suggestions:
        marker = " <<<" if s["hit_rate_pct"] >= 15 and s["sample"] >= 30 else \
                 " <<" if s["hit_rate_pct"] >= 12 and s["sample"] >= 20 else \
                 " <" if s["hit_rate_pct"] >= 10 else ""
        log.info(f"  {s['filter']:<55s} {s['sample']:>7d} {s['hits_100pct']:>5d} {s['hit_rate_pct']:>6.1f}% {s['avg_return_pct']:>+7.1f}% {s['median_return_pct']:>+7.1f}% {s['negative_pct']:>5.1f}% {s['loss_30_pct']:>5.1f}%{marker}")

    # Save report
    report = {
        "core_stats_clean": stats,
        "winners_profile": winners_prof,
        "losers_profile": losers_prof,
        "filter_suggestions": suggestions,
    }
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)

    log.info(f"\nReport saved to {REPORT_PATH}")


if __name__ == "__main__":
    run()
