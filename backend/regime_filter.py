"""
ALPHA HUNTER — Regime Filter
Classifies market regimes and re-scores backtest signals to find
when the buy signal actually has edge.
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
BACKTEST_PATH = DATA_DIR / "backtest_report.json"
REPORT_PATH = DATA_DIR / "regime_report.json"
LOG_PATH = DATA_DIR / "regime.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("regime")

GAIN_THRESHOLD = 100  # % to count as hit

# ---------------------------------------------------------------------------
# Step 1 — Regime Classification
# ---------------------------------------------------------------------------

def build_regime_map() -> dict[str, dict]:
    """For every trading day 2017-2024, classify the market regime."""
    log.info("Building regime map from SPY + VIX...")

    spy = yf.Ticker("SPY")
    vix = yf.Ticker("^VIX")

    spy_hist = spy.history(start="2017-01-01", end="2024-12-31", auto_adjust=True)
    vix_hist = vix.history(start="2017-01-01", end="2024-12-31", auto_adjust=True)

    spy_hist.index = spy_hist.index.tz_localize(None)
    vix_hist.index = vix_hist.index.tz_localize(None)

    # Align VIX to SPY dates
    vix_close = vix_hist["Close"].reindex(spy_hist.index, method="ffill")

    spy_close = spy_hist["Close"]
    spy_ma50 = spy_close.rolling(50).mean()
    spy_ma200 = spy_close.rolling(200).mean()

    # Rolling 20-day low for bounce detection
    spy_low_20 = spy_close.rolling(20).min()

    regime_map = {}

    for i in range(200, len(spy_close)):
        date = spy_close.index[i]
        price = spy_close.iloc[i]
        ma50 = spy_ma50.iloc[i]
        ma200 = spy_ma200.iloc[i]
        v = vix_close.iloc[i] if not pd.isna(vix_close.iloc[i]) else 20
        low20 = spy_low_20.iloc[i]

        above_ma50 = price > ma50
        bounce = (price - low20) / low20 * 100 if low20 > 0 else 0

        if above_ma50 and v < 20:
            regime = "A"
            label = "Bull Calm"
        elif above_ma50 and v >= 20:
            regime = "B"
            label = "Bull Fear"
        elif not above_ma50 and bounce >= 3 and v > 25:
            regime = "C"
            label = "Bear Bounce"
        else:
            regime = "D"
            label = "Bear Grind"

        regime_map[date.strftime("%Y-%m-%d")] = {
            "regime": regime,
            "label": label,
            "spy": round(price, 2),
            "spy_ma50": round(ma50, 2),
            "spy_ma200": round(ma200, 2),
            "vix": round(v, 2),
            "bounce_pct": round(bounce, 1),
        }

    log.info(f"Regime map built: {len(regime_map)} days")

    # Summary
    counts = defaultdict(int)
    for v in regime_map.values():
        counts[v["label"]] += 1
    for label, c in sorted(counts.items()):
        log.info(f"  {label}: {c} days ({c/len(regime_map)*100:.1f}%)")

    return regime_map


def find_regime_for_date(regime_map: dict, date_str: str) -> dict | None:
    """Find regime for a date, or closest prior date."""
    if date_str in regime_map:
        return regime_map[date_str]
    # Walk backwards up to 7 days
    d = datetime.strptime(date_str, "%Y-%m-%d")
    for offset in range(1, 8):
        prev = (d - timedelta(days=offset)).strftime("%Y-%m-%d")
        if prev in regime_map:
            return regime_map[prev]
    return None


# ---------------------------------------------------------------------------
# Step 2 — Load backtest signals and tag with regime
# ---------------------------------------------------------------------------

def load_backtest_signals() -> list[dict]:
    """Load signals from backtest_report.json."""
    if not BACKTEST_PATH.exists():
        log.error(f"Backtest report not found at {BACKTEST_PATH}")
        log.error("Run backtest.py first!")
        sys.exit(1)

    with open(BACKTEST_PATH) as f:
        report = json.load(f)

    signals = report.get("results", {}).get("top_signals", [])
    if not signals:
        log.error("No signals in backtest report — need full signal list.")
        log.info("Re-running signal scan from backtest data...")
        return []

    return signals


def load_all_signals_from_db() -> list[dict]:
    """If backtest_report only has top signals, re-scan from histories."""
    # We stored the full report — check if it has all signals
    with open(BACKTEST_PATH) as f:
        report = json.load(f)

    # The report might only have top_signals (15 items).
    # We need ALL signals. Let's re-derive them.
    return report.get("results", {}).get("top_signals", [])


# ---------------------------------------------------------------------------
# Step 3 — Full re-scan with regime tagging
# ---------------------------------------------------------------------------

def full_regime_backtest(regime_map: dict) -> list[dict]:
    """Re-run the signal scan (like backtest.py) but tag each signal with regime
    and attempt fundamental checks."""

    log.info("Re-scanning signals with regime + fundamental tagging...")

    conn = sqlite3.connect(str(DB_PATH))
    symbols = [r[0] for r in conn.execute("SELECT symbol FROM tickers").fetchall()]
    conn.close()

    # Load histories
    log.info(f"Loading histories for {len(symbols)} tickers...")
    histories = {}
    for i, sym in enumerate(symbols, 1):
        try:
            t = yf.Ticker(sym)
            h = t.history(start="2016-01-01", end="2025-12-31", auto_adjust=True)
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

    # Get market caps + basic fundamental info (one call per ticker)
    log.info("Fetching fundamentals (market cap, revenue growth, EPS)...")
    fundamentals = {}
    for i, sym in enumerate(histories.keys(), 1):
        try:
            info = yf.Ticker(sym).info or {}
            fundamentals[sym] = {
                "mcap": info.get("marketCap", 0) or 0,
                "rev_growth": info.get("revenueGrowth"),  # decimal, e.g. 0.15 = 15%
                "trailing_eps": info.get("trailingEps"),
                "forward_eps": info.get("forwardEps"),
                "sector": info.get("sector", ""),
            }
        except:
            fundamentals[sym] = {"mcap": 0}
        if i % 200 == 0:
            log.info(f"  Fundamentals: {i}/{len(histories)}")
        time.sleep(0.1)

    # Scan signals
    bt_start = pd.Timestamp("2018-01-01")
    bt_end = pd.Timestamp("2023-12-31")
    mondays = pd.date_range(bt_start, bt_end, freq="W-MON")

    signals = []
    last_signal_date = {}  # for dedup

    for wk, monday in enumerate(mondays, 1):
        friday = monday + timedelta(days=4)

        for sym, hist in histories.items():
            fund = fundamentals.get(sym, {})
            mcap = fund.get("mcap", 0)
            if mcap < 500_000_000:
                continue

            try:
                week_mask = (hist.index >= monday) & (hist.index <= friday)
                week_data = hist[week_mask]
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

                    is_green = close > opn
                    vol_spike = vol >= vol_ma20 * 3.0
                    above_ma200 = close > ma200
                    near_high = close >= high_52w * 0.50

                    if not (is_green and vol_spike and above_ma200 and near_high):
                        continue

                    # Dedup: 60-day cooldown per ticker
                    d = idx.to_pydatetime()
                    if sym in last_signal_date and (d - last_signal_date[sym]).days < 60:
                        continue
                    last_signal_date[sym] = d

                    date_str = idx.strftime("%Y-%m-%d")
                    vol_ratio = round(vol / vol_ma20, 2)

                    # Forward return
                    sig_idx = hist.index.get_loc(idx)
                    fwd_return = None
                    fwd_max_return = None
                    if sig_idx + 252 < len(hist):
                        future = hist["Close"].iloc[sig_idx:sig_idx + 253]
                        fwd_return = round((future.iloc[-1] - close) / close * 100, 2)
                        fwd_max_return = round((future.max() - close) / close * 100, 2)

                    # Regime
                    regime_info = find_regime_for_date(regime_map, date_str)
                    regime = regime_info["regime"] if regime_info else "?"
                    regime_label = regime_info["label"] if regime_info else "Unknown"
                    vix_val = regime_info["vix"] if regime_info else 0

                    # Fundamental flags
                    rev_growth = fund.get("rev_growth")
                    trailing_eps = fund.get("trailing_eps")
                    forward_eps = fund.get("forward_eps")

                    has_rev_growth = rev_growth is not None and rev_growth > 0
                    has_eps_improving = (trailing_eps is not None and forward_eps is not None
                                        and forward_eps > trailing_eps)
                    is_midcap = 500_000_000 <= mcap <= 20_000_000_000

                    # Combined filters
                    passes_fundamental = has_rev_growth and is_midcap
                    passes_full = passes_fundamental and has_eps_improving

                    signals.append({
                        "symbol": sym,
                        "date": date_str,
                        "price": round(close, 4),
                        "vol_ratio": vol_ratio,
                        "market_cap": mcap,
                        "fwd_12m_return": fwd_return,
                        "fwd_12m_max_return": fwd_max_return,
                        "regime": regime,
                        "regime_label": regime_label,
                        "vix": vix_val,
                        "rev_growth_positive": has_rev_growth,
                        "eps_improving": has_eps_improving,
                        "is_midcap": is_midcap,
                        "passes_fundamental": passes_fundamental,
                        "passes_full": passes_full,
                        "sector": fund.get("sector", ""),
                    })
                    break  # One per ticker per week

            except:
                continue

        if wk % 40 == 0:
            log.info(f"  Week {wk}/{len(mondays)} | Signals: {len(signals)}")

    log.info(f"Total signals with regime tags: {len(signals)}")
    return signals


# ---------------------------------------------------------------------------
# Step 4 — Compute stats by regime and filter combination
# ---------------------------------------------------------------------------

def compute_regime_stats(signals: list[dict]) -> dict:
    """Compute hit rate for every regime x filter combination."""

    combos = {
        "signal_only": lambda s: True,
        "signal + midcap": lambda s: s["is_midcap"],
        "signal + rev_growth": lambda s: s["rev_growth_positive"],
        "signal + fundamental": lambda s: s["passes_fundamental"],
        "signal + full": lambda s: s["passes_full"],
        "signal + midcap + rev_growth + eps": lambda s: s["passes_full"],
    }

    regimes = ["A", "B", "C", "D"]
    regime_labels = {"A": "Bull Calm", "B": "Bull Fear", "C": "Bear Bounce", "D": "Bear Grind"}

    results = {}

    for combo_name, filt in combos.items():
        results[combo_name] = {"all_regimes": {}, "by_regime": {}}

        # All regimes combined
        filtered = [s for s in signals if filt(s) and s["fwd_12m_return"] is not None]
        hits = [s for s in filtered if s["fwd_12m_return"] >= GAIN_THRESHOLD]
        hits_peak = [s for s in filtered if s["fwd_12m_max_return"] is not None and s["fwd_12m_max_return"] >= GAIN_THRESHOLD]
        returns = [s["fwd_12m_return"] for s in filtered]

        results[combo_name]["all_regimes"] = {
            "sample": len(filtered),
            "hits_eoy": len(hits),
            "hits_peak": len(hits_peak),
            "hit_rate_eoy": round(len(hits) / len(filtered) * 100, 2) if filtered else 0,
            "hit_rate_peak": round(len(hits_peak) / len(filtered) * 100, 2) if filtered else 0,
            "avg_return": round(np.mean(returns), 2) if returns else 0,
            "median_return": round(np.median(returns), 2) if returns else 0,
        }

        # By regime
        for r in regimes:
            r_signals = [s for s in signals if s["regime"] == r and filt(s) and s["fwd_12m_return"] is not None]
            r_hits = [s for s in r_signals if s["fwd_12m_return"] >= GAIN_THRESHOLD]
            r_hits_peak = [s for s in r_signals if s["fwd_12m_max_return"] is not None and s["fwd_12m_max_return"] >= GAIN_THRESHOLD]
            r_returns = [s["fwd_12m_return"] for s in r_signals]

            results[combo_name]["by_regime"][regime_labels[r]] = {
                "sample": len(r_signals),
                "hits_eoy": len(r_hits),
                "hits_peak": len(r_hits_peak),
                "hit_rate_eoy": round(len(r_hits) / len(r_signals) * 100, 2) if r_signals else 0,
                "hit_rate_peak": round(len(r_hits_peak) / len(r_signals) * 100, 2) if r_signals else 0,
                "avg_return": round(np.mean(r_returns), 2) if r_returns else 0,
                "median_return": round(np.median(r_returns), 2) if r_returns else 0,
            }

    # Top performing signals
    with_fwd = [s for s in signals if s["fwd_12m_return"] is not None]
    top = sorted(with_fwd, key=lambda x: x["fwd_12m_return"], reverse=True)[:20]

    return results, top


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    log.info("=" * 60)
    log.info("ALPHA HUNTER — REGIME FILTER")
    log.info("=" * 60)

    # Step 1: Build regime map
    regime_map = build_regime_map()

    # Step 2+3: Full re-scan with regime + fundamentals
    signals = full_regime_backtest(regime_map)

    if not signals:
        log.error("No signals found!")
        return

    # Step 4: Compute stats
    results, top_signals = compute_regime_stats(signals)

    # Save report
    report = {
        "generated": datetime.now().isoformat(),
        "total_signals": len(signals),
        "signals_with_forward": len([s for s in signals if s["fwd_12m_return"] is not None]),
        "results": results,
        "top_signals": top_signals,
    }
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)

    # Print the money table
    log.info("")
    log.info("=" * 100)
    log.info("RESULTS TABLE")
    log.info("=" * 100)
    log.info(f"{'Filter':<40} {'Regime':<15} {'Sample':>8} {'Hits':>6} {'Rate':>8} {'Peak%':>8} {'AvgRet':>8}")
    log.info("-" * 100)

    for combo_name, data in results.items():
        # All regimes
        a = data["all_regimes"]
        log.info(f"{combo_name:<40} {'ALL':<15} {a['sample']:>8} {a['hits_eoy']:>6} {a['hit_rate_eoy']:>7.1f}% {a['hit_rate_peak']:>7.1f}% {a['avg_return']:>7.1f}%")

        # By regime
        for regime_label, rd in data["by_regime"].items():
            if rd["sample"] > 0:
                marker = " ***" if rd["hit_rate_eoy"] >= 15 and rd["sample"] >= 30 else \
                         " **" if rd["hit_rate_eoy"] >= 10 and rd["sample"] >= 30 else \
                         " *" if rd["hit_rate_eoy"] >= 8 else ""
                log.info(f"{'  ':<40} {regime_label:<15} {rd['sample']:>8} {rd['hits_eoy']:>6} {rd['hit_rate_eoy']:>7.1f}% {rd['hit_rate_peak']:>7.1f}% {rd['avg_return']:>7.1f}%{marker}")
        log.info("")

    # Find best combos
    log.info("=" * 60)
    log.info("BEST COMBINATIONS (hit rate > 10%, sample > 30):")
    log.info("=" * 60)

    best = []
    for combo_name, data in results.items():
        for regime_label, rd in data["by_regime"].items():
            if rd["sample"] >= 30 and rd["hit_rate_eoy"] >= 10:
                best.append({
                    "filter": combo_name,
                    "regime": regime_label,
                    "sample": rd["sample"],
                    "hits": rd["hits_eoy"],
                    "hit_rate": rd["hit_rate_eoy"],
                    "hit_rate_peak": rd["hit_rate_peak"],
                    "avg_return": rd["avg_return"],
                })
        # Also check all-regimes
        a = data["all_regimes"]
        if a["sample"] >= 50 and a["hit_rate_eoy"] >= 10:
            best.append({
                "filter": combo_name,
                "regime": "ALL",
                "sample": a["sample"],
                "hits": a["hits_eoy"],
                "hit_rate": a["hit_rate_eoy"],
                "hit_rate_peak": a["hit_rate_peak"],
                "avg_return": a["avg_return"],
            })

    best.sort(key=lambda x: x["hit_rate"], reverse=True)
    for b in best:
        log.info(f"  {b['filter']} + {b['regime']}: {b['hit_rate']}% hit rate "
                 f"({b['hits']}/{b['sample']}) | peak {b['hit_rate_peak']}% | avg {b['avg_return']}%")

    if not best:
        log.info("  None found with >10% hit rate and >30 samples.")
        log.info("  Checking >8% with >20 samples...")
        for combo_name, data in results.items():
            for regime_label, rd in data["by_regime"].items():
                if rd["sample"] >= 20 and rd["hit_rate_eoy"] >= 8:
                    log.info(f"  {combo_name} + {regime_label}: {rd['hit_rate_eoy']}% "
                             f"({rd['hits_eoy']}/{rd['sample']}) | avg {rd['avg_return']}%")

    log.info("")
    log.info("TOP 10 INDIVIDUAL SIGNALS:")
    for s in top_signals[:10]:
        log.info(f"  {s['symbol']} {s['date']} [{s['regime_label']}]: "
                 f"Vol {s['vol_ratio']}X -> +{s['fwd_12m_return']}% "
                 f"(rev_growth={s['rev_growth_positive']}, eps_up={s['eps_improving']}, midcap={s['is_midcap']})")

    log.info(f"\nReport saved to {REPORT_PATH}")


if __name__ == "__main__":
    run()
