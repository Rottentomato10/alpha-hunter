"""
ALPHA HUNTER — Risk Analysis
Deep dive into return distribution of Bull Fear + Midcap signals.
"""

import json
import sys
import logging
import math
from pathlib import Path

import numpy as np

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
REGIME_PATH = DATA_DIR / "regime_report.json"
LOG_PATH = DATA_DIR / "risk_analysis.log"
DB_PATH = DATA_DIR / "db.sqlite"
REPORT_PATH = DATA_DIR / "risk_report.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("risk")

# ---------------------------------------------------------------------------
# Load signals
# ---------------------------------------------------------------------------

def load_bull_fear_midcap() -> list[dict]:
    with open(REGIME_PATH) as f:
        report = json.load(f)

    # We need the raw signal list — regime_report doesn't store all signals,
    # only top 20. We need to re-extract from the backtest data.
    # But regime_report.json might not have them all.
    # Let's check what we have and also load from backtest_report.json

    # Actually the regime_filter stores "top_signals" (20 items).
    # We need to re-derive from the regime backtest.
    # Best approach: load the full regime data if available.
    # Since we ran the regime filter, the signals are in regime_report.json
    # but only top_signals. We need ALL signals.

    # Let's reload from the regime_filter output properly.
    # The regime_report has total counts but not individual signals.
    # We need to re-compute from backtest_report.json + regime map.

    log.info("Loading backtest signals and re-tagging with regime...")

    backtest_path = DATA_DIR / "backtest_report.json"
    with open(backtest_path) as f:
        bt = json.load(f)

    # backtest_report only stores top_signals (15).
    # We need the full signal scan. Let's check if regime stored them.
    # Since neither has all signals, we need to re-derive.
    # The cleanest path: scan the regime_report structure.

    log.info("Full signal list not in reports. Re-scanning from DB...")
    return None


def rescan_signals():
    """Re-run the signal scan for Bull Fear + Midcap specifically."""
    import yfinance as yf
    import pandas as pd
    import sqlite3
    import time
    from datetime import timedelta

    conn = sqlite3.connect(str(DB_PATH))
    symbols = [r[0] for r in conn.execute("SELECT symbol FROM tickers").fetchall()]
    conn.close()

    # Load SPY + VIX for regime
    log.info("Loading SPY + VIX for regime classification...")
    spy_hist = yf.Ticker("SPY").history(start="2017-01-01", end="2024-12-31", auto_adjust=True)
    vix_hist = yf.Ticker("^VIX").history(start="2017-01-01", end="2024-12-31", auto_adjust=True)
    spy_hist.index = spy_hist.index.tz_localize(None)
    vix_hist.index = vix_hist.index.tz_localize(None)

    spy_close = spy_hist["Close"]
    spy_ma50 = spy_close.rolling(50).mean()
    vix_close = vix_hist["Close"].reindex(spy_hist.index, method="ffill")

    def get_regime(date):
        if date not in spy_close.index:
            # Find closest prior
            mask = spy_close.index <= date
            if mask.any():
                date = spy_close.index[mask][-1]
            else:
                return None
        idx = spy_close.index.get_loc(date)
        if idx < 50:
            return None
        price = spy_close.iloc[idx]
        ma50 = spy_ma50.iloc[idx]
        v = vix_close.iloc[idx] if not pd.isna(vix_close.iloc[idx]) else 20
        above_ma50 = price > ma50
        if above_ma50 and v >= 20:
            return "Bull Fear"
        return None  # We only care about Bull Fear

    # Load ticker histories
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

    # Get market caps
    log.info("Fetching market caps...")
    mcaps = {}
    for i, sym in enumerate(histories.keys(), 1):
        try:
            info = yf.Ticker(sym).info or {}
            mcaps[sym] = info.get("marketCap", 0) or 0
        except:
            mcaps[sym] = 0
        if i % 200 == 0:
            log.info(f"  MCaps: {i}/{len(histories)}")
        time.sleep(0.1)

    # Scan
    bt_start = pd.Timestamp("2018-01-01")
    bt_end = pd.Timestamp("2023-12-31")
    mondays = pd.date_range(bt_start, bt_end, freq="W-MON")

    signals = []
    last_signal = {}

    log.info(f"Scanning {len(mondays)} weeks for Bull Fear + Midcap signals...")

    for wk, monday in enumerate(mondays, 1):
        friday = monday + timedelta(days=4)

        for sym, hist in histories.items():
            mcap = mcaps.get(sym, 0)
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

                    # Check regime
                    regime = get_regime(idx)
                    if regime != "Bull Fear":
                        continue

                    # Dedup
                    d = idx.to_pydatetime()
                    if sym in last_signal and (d - last_signal[sym]).days < 60:
                        continue
                    last_signal[sym] = d

                    # Forward return
                    sig_idx = hist.index.get_loc(idx)
                    if sig_idx + 252 >= len(hist):
                        continue

                    future = hist["Close"].iloc[sig_idx:sig_idx + 253]
                    fwd_return = round((future.iloc[-1] - close) / close * 100, 2)
                    fwd_max = round((future.max() - close) / close * 100, 2)
                    fwd_min = round((future.min() - close) / close * 100, 2)

                    signals.append({
                        "symbol": sym,
                        "date": idx.strftime("%Y-%m-%d"),
                        "price": round(close, 4),
                        "vol_ratio": round(vol / vol_ma20, 2),
                        "market_cap": mcap,
                        "fwd_12m_return": fwd_return,
                        "fwd_12m_max": fwd_max,
                        "fwd_12m_min": fwd_min,
                    })
                    break

            except:
                continue

        if wk % 50 == 0:
            log.info(f"  Week {wk}/{len(mondays)} | Bull Fear Midcap signals: {len(signals)}")

    log.info(f"Total Bull Fear + Midcap signals with forward data: {len(signals)}")
    return signals


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze(signals: list[dict]):
    returns = [s["fwd_12m_return"] for s in signals]
    max_returns = [s["fwd_12m_max"] for s in signals]
    min_returns = [s["fwd_12m_min"] for s in signals]
    n = len(returns)

    if n == 0:
        log.error("No signals to analyze!")
        return

    returns_arr = np.array(returns)
    max_arr = np.array(max_returns)
    min_arr = np.array(min_returns)

    # --- 1. Return Distribution Buckets ---
    buckets = [
        ("< -50%", lambda r: r < -50),
        ("-50% to -20%", lambda r: -50 <= r < -20),
        ("-20% to 0%", lambda r: -20 <= r < 0),
        ("0% to +50%", lambda r: 0 <= r < 50),
        ("+50% to +100%", lambda r: 50 <= r < 100),
        ("+100% to +300%", lambda r: 100 <= r < 300),
        ("+300%+", lambda r: r >= 300),
    ]

    distribution = []
    for label, cond in buckets:
        count = int(np.sum([cond(r) for r in returns]))
        pct = round(count / n * 100, 1)
        distribution.append({"bucket": label, "count": count, "pct": pct})

    # --- 2. Central Stats ---
    avg_return = round(float(np.mean(returns_arr)), 2)
    median_return = round(float(np.median(returns_arr)), 2)
    std_return = round(float(np.std(returns_arr)), 2)

    # --- 3. Max Loss ---
    max_loss = round(float(np.min(returns_arr)), 2)
    max_loss_signal = min(signals, key=lambda s: s["fwd_12m_return"])

    # Max gain
    max_gain = round(float(np.max(returns_arr)), 2)
    max_gain_signal = max(signals, key=lambda s: s["fwd_12m_return"])

    # --- 4. Negative Outcomes ---
    negative_count = int(np.sum(returns_arr < 0))
    negative_pct = round(negative_count / n * 100, 1)

    loss_30_count = int(np.sum(returns_arr < -30))
    loss_30_pct = round(loss_30_count / n * 100, 1)

    loss_50_count = int(np.sum(returns_arr < -50))
    loss_50_pct = round(loss_50_count / n * 100, 1)

    # --- 5. Kelly Criterion ---
    # p = probability of win (positive return)
    # We'll use 100%+ as "win" for the aggressive version,
    # and positive return for the conservative version
    winners = returns_arr[returns_arr >= 100]
    losers = returns_arr[returns_arr < 100]
    hit_rate = len(winners) / n

    # For Kelly, define win/loss differently:
    # Conservative Kelly: win = positive, loss = negative
    pos = returns_arr[returns_arr > 0]
    neg = returns_arr[returns_arr < 0]

    p_win = len(pos) / n if n > 0 else 0
    avg_win = float(np.mean(pos)) if len(pos) > 0 else 0
    avg_loss = float(np.mean(np.abs(neg))) if len(neg) > 0 else 1

    # Kelly = p - (1-p)/(W/L) = p - q/b where b = avg_win/avg_loss
    b = avg_win / avg_loss if avg_loss > 0 else 1
    q = 1 - p_win
    kelly_full = round((p_win - q / b) * 100, 2)
    kelly_half = round(kelly_full / 2, 2)  # Half Kelly is standard practice
    kelly_quarter = round(kelly_full / 4, 2)

    # Aggressive Kelly (only 100%+ counts as win)
    if len(winners) > 0 and len(losers) > 0:
        avg_win_agg = float(np.mean(winners))
        avg_loss_agg = float(np.mean(np.abs(losers[losers < 0]))) if len(losers[losers < 0]) > 0 else 1
        b_agg = avg_win_agg / avg_loss_agg if avg_loss_agg > 0 else 1
        q_agg = 1 - hit_rate
        kelly_aggressive = round((hit_rate - q_agg / b_agg) * 100, 2)
    else:
        kelly_aggressive = 0

    # --- 6. Sharpe-like Ratio ---
    risk_free = 5.0  # 5% annual
    sharpe = round((avg_return - risk_free) / std_return, 3) if std_return > 0 else 0

    # Also Sortino (downside deviation only)
    downside = returns_arr[returns_arr < 0]
    downside_std = round(float(np.std(downside)), 2) if len(downside) > 0 else 1
    sortino = round((avg_return - risk_free) / downside_std, 3) if downside_std > 0 else 0

    # --- 7. Drawdown Analysis ---
    # Max intra-period drawdown (using fwd_12m_min)
    max_drawdowns = min_arr
    avg_max_dd = round(float(np.mean(max_drawdowns)), 2)
    worst_dd = round(float(np.min(max_drawdowns)), 2)
    dd_below_30 = int(np.sum(max_drawdowns < -30))
    dd_below_30_pct = round(dd_below_30 / n * 100, 1)

    # --- 8. Win Streaks / Loss Streaks ---
    sorted_by_date = sorted(signals, key=lambda s: s["date"])
    streak_wins = 0
    streak_losses = 0
    max_win_streak = 0
    max_loss_streak = 0
    for s in sorted_by_date:
        if s["fwd_12m_return"] > 0:
            streak_wins += 1
            streak_losses = 0
        else:
            streak_losses += 1
            streak_wins = 0
        max_win_streak = max(max_win_streak, streak_wins)
        max_loss_streak = max(max_loss_streak, streak_losses)

    # --- 9. Percentiles ---
    p10 = round(float(np.percentile(returns_arr, 10)), 2)
    p25 = round(float(np.percentile(returns_arr, 25)), 2)
    p75 = round(float(np.percentile(returns_arr, 75)), 2)
    p90 = round(float(np.percentile(returns_arr, 90)), 2)

    # --- Build Report ---
    report = {
        "signal_description": "Bull Fear (SPY > MA50, VIX > 20) + Midcap ($500M-$20B) + Vol 3X+ + Green + Above MA200 + Near 52w High",
        "total_signals": n,

        "return_distribution": distribution,

        "central_stats": {
            "average_return_pct": avg_return,
            "median_return_pct": median_return,
            "std_deviation_pct": std_return,
            "p10": p10,
            "p25": p25,
            "p75": p75,
            "p90": p90,
        },

        "extremes": {
            "max_gain_pct": max_gain,
            "max_gain_signal": f"{max_gain_signal['symbol']} {max_gain_signal['date']}",
            "max_loss_pct": max_loss,
            "max_loss_signal": f"{max_loss_signal['symbol']} {max_loss_signal['date']}",
        },

        "loss_analysis": {
            "negative_count": negative_count,
            "negative_pct": negative_pct,
            "loss_over_30_count": loss_30_count,
            "loss_over_30_pct": loss_30_pct,
            "loss_over_50_count": loss_50_count,
            "loss_over_50_pct": loss_50_pct,
            "avg_max_intraperiod_drawdown_pct": avg_max_dd,
            "worst_intraperiod_drawdown_pct": worst_dd,
            "signals_with_30pct_drawdown": dd_below_30,
            "signals_with_30pct_drawdown_pct": dd_below_30_pct,
        },

        "kelly_criterion": {
            "win_rate_positive_pct": round(p_win * 100, 1),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "win_loss_ratio": round(b, 2),
            "kelly_full_pct": kelly_full,
            "kelly_half_pct": kelly_half,
            "kelly_quarter_pct": kelly_quarter,
            "kelly_aggressive_100pct_wins": kelly_aggressive,
            "recommendation": f"Risk {max(kelly_quarter, 1)}% of capital per signal (quarter Kelly)"
        },

        "risk_adjusted": {
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "risk_free_rate_pct": risk_free,
        },

        "streaks": {
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
        },
    }

    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    log.info("=" * 60)
    log.info("RISK ANALYSIS — Bull Fear + Midcap")
    log.info("=" * 60)

    signals = rescan_signals()

    if not signals:
        log.error("No signals found!")
        return

    report = analyze(signals)

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    # Print
    log.info("")
    log.info("=" * 60)
    log.info("RETURN DISTRIBUTION")
    log.info("=" * 60)
    for b in report["return_distribution"]:
        bar = "#" * int(b["pct"] / 2)
        log.info(f"  {b['bucket']:>20s}  {b['count']:>5d}  ({b['pct']:>5.1f}%)  {bar}")

    log.info("")
    log.info("=" * 60)
    log.info("CENTRAL STATS")
    log.info("=" * 60)
    cs = report["central_stats"]
    log.info(f"  Average return:       {cs['average_return_pct']:>+8.2f}%")
    log.info(f"  Median return:        {cs['median_return_pct']:>+8.2f}%")
    log.info(f"  Std deviation:        {cs['std_deviation_pct']:>8.2f}%")
    log.info(f"  10th percentile:      {cs['p10']:>+8.2f}%")
    log.info(f"  25th percentile:      {cs['p25']:>+8.2f}%")
    log.info(f"  75th percentile:      {cs['p75']:>+8.2f}%")
    log.info(f"  90th percentile:      {cs['p90']:>+8.2f}%")

    log.info("")
    log.info("=" * 60)
    log.info("LOSS ANALYSIS")
    log.info("=" * 60)
    la = report["loss_analysis"]
    log.info(f"  Signals ending negative:      {la['negative_count']:>5d}  ({la['negative_pct']}%)")
    log.info(f"  Signals losing >30%:          {la['loss_over_30_count']:>5d}  ({la['loss_over_30_pct']}%)")
    log.info(f"  Signals losing >50%:          {la['loss_over_50_count']:>5d}  ({la['loss_over_50_pct']}%)")
    log.info(f"  Avg max drawdown (intra):     {la['avg_max_intraperiod_drawdown_pct']:>+8.2f}%")
    log.info(f"  Worst drawdown (intra):       {la['worst_intraperiod_drawdown_pct']:>+8.2f}%")
    log.info(f"  Signals with 30%+ drawdown:   {la['signals_with_30pct_drawdown']:>5d}  ({la['signals_with_30pct_drawdown_pct']}%)")

    log.info("")
    ex = report["extremes"]
    log.info(f"  Best signal:  {ex['max_gain_signal']} -> {ex['max_gain_pct']:>+.2f}%")
    log.info(f"  Worst signal: {ex['max_loss_signal']} -> {ex['max_loss_pct']:>+.2f}%")

    log.info("")
    log.info("=" * 60)
    log.info("KELLY CRITERION")
    log.info("=" * 60)
    kc = report["kelly_criterion"]
    log.info(f"  Win rate (positive return):  {kc['win_rate_positive_pct']}%")
    log.info(f"  Average win:                {kc['avg_win_pct']:>+.2f}%")
    log.info(f"  Average loss:               {kc['avg_loss_pct']:>.2f}%")
    log.info(f"  Win/Loss ratio:             {kc['win_loss_ratio']:.2f}")
    log.info(f"  Kelly Full:                 {kc['kelly_full_pct']}%")
    log.info(f"  Kelly Half:                 {kc['kelly_half_pct']}%")
    log.info(f"  Kelly Quarter:              {kc['kelly_quarter_pct']}%")
    log.info(f"  Kelly (100%+ wins only):    {kc['kelly_aggressive_100pct_wins']}%")
    log.info(f"  >> {kc['recommendation']}")

    log.info("")
    log.info("=" * 60)
    log.info("RISK-ADJUSTED RETURNS")
    log.info("=" * 60)
    ra = report["risk_adjusted"]
    log.info(f"  Sharpe ratio:   {ra['sharpe_ratio']}")
    log.info(f"  Sortino ratio:  {ra['sortino_ratio']}")

    log.info("")
    st = report["streaks"]
    log.info(f"  Max win streak:  {st['max_win_streak']}")
    log.info(f"  Max loss streak: {st['max_loss_streak']}")

    log.info(f"\nReport saved to {REPORT_PATH}")


if __name__ == "__main__":
    run()
