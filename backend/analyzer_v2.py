"""
ALPHA HUNTER v2 — Deep Analyzer
Historical fundamentals (FMP), breakout detection, and entry point identification.
"""

import sqlite3
import os
import sys
import time
import json
import logging
import csv
from datetime import datetime, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd
import numpy as np
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "db.sqlite"
LOG_PATH = DATA_DIR / "errors.log"
CSV_PATH = DATA_DIR / "winners_v2_analysis.csv"

FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FMP_STABLE = "https://financialmodelingprep.com/stable"

REQUEST_DELAY = 0.5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("analyzer_v2")

# ---------------------------------------------------------------------------
# FMP API
# ---------------------------------------------------------------------------

fmp_calls_used = 0
FMP_DAILY_LIMIT = 220  # Stay under 250

def fmp_get(endpoint: str, params: dict = None):
    global fmp_calls_used
    if fmp_calls_used >= FMP_DAILY_LIMIT:
        return None
    if not FMP_API_KEY or FMP_API_KEY == "your_key_here":
        return None
    params = params or {}
    params["apikey"] = FMP_API_KEY
    url = f"{FMP_STABLE}/{endpoint}"
    try:
        resp = requests.get(url, params=params, timeout=15)
        fmp_calls_used += 1
        if resp.status_code == 200:
            data = resp.json()
            return data
        else:
            log.debug(f"FMP {resp.status_code} for {endpoint}")
    except Exception as e:
        log.debug(f"FMP error: {e}")
    return None

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_winners(conn):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        "SELECT * FROM winners_v2 ORDER BY return_pct DESC"
    ).fetchall()]

def save_param(conn, symbol, start_date, param, value):
    val = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    conn.execute(
        "INSERT OR REPLACE INTO analysis_v2 (symbol, start_date, param, value) VALUES (?,?,?,?)",
        (symbol, start_date, param, val),
    )

def save_breakout(conn, symbol, start_date, breakout_date, btype, price, vol_ratio, desc):
    conn.execute(
        """INSERT OR REPLACE INTO breakouts
           (symbol, start_date, breakout_date, breakout_type, breakout_price, volume_ratio, description)
           VALUES (?,?,?,?,?,?,?)""",
        (symbol, start_date, breakout_date, btype, price, vol_ratio, desc),
    )

def is_analyzed(conn, symbol, start_date):
    count = conn.execute(
        "SELECT COUNT(*) FROM analysis_v2 WHERE symbol=? AND start_date=?",
        (symbol, start_date),
    ).fetchone()[0]
    return count > 10

# ---------------------------------------------------------------------------
# 1. Historical Fundamentals (FMP)
# ---------------------------------------------------------------------------

def analyze_fundamentals_historical(symbol, start_date, conn):
    """Get quarterly financials from FMP for the 4 quarters before the move started."""
    sd = datetime.strptime(start_date, "%Y-%m-%d")

    # Annual income statement
    data = fmp_get("income-statement", {"symbol": symbol, "period": "quarter"})
    if not data or not isinstance(data, list):
        save_param(conn, symbol, start_date, "fmp_data_available", "No")
        return
    time.sleep(REQUEST_DELAY)

    save_param(conn, symbol, start_date, "fmp_data_available", "Yes")

    # Filter quarters BEFORE the start date
    prior_quarters = []
    for stmt in data:
        stmt_date = stmt.get("date", "")
        if stmt_date and stmt_date < start_date:
            prior_quarters.append(stmt)
        if len(prior_quarters) >= 8:  # Up to 8 quarters back
            break

    if len(prior_quarters) < 2:
        return

    # --- EPS Growth QoQ (3 consecutive quarters?) ---
    eps_values = []
    for q in prior_quarters[:4]:
        eps = q.get("eps")
        if eps is not None:
            eps_values.append({"date": q["date"], "eps": eps})

    save_param(conn, symbol, start_date, "quarterly_eps", eps_values)

    if len(eps_values) >= 3:
        # Check if EPS grew 3 quarters in a row (most recent first)
        eps_growing = all(
            eps_values[i]["eps"] > eps_values[i + 1]["eps"]
            for i in range(min(3, len(eps_values) - 1))
        )
        save_param(conn, symbol, start_date, "eps_growth_3q_consecutive", "Yes" if eps_growing else "No")

        # EPS growth rate (latest vs 4Q ago)
        if len(eps_values) >= 4 and eps_values[3]["eps"] != 0:
            eps_growth = (eps_values[0]["eps"] - eps_values[3]["eps"]) / abs(eps_values[3]["eps"]) * 100
            save_param(conn, symbol, start_date, "eps_growth_yoy_pct", round(eps_growth, 2))

    # --- Revenue Acceleration ---
    rev_values = []
    for q in prior_quarters[:8]:
        rev = q.get("revenue")
        if rev is not None:
            rev_values.append({"date": q["date"], "revenue": rev})

    save_param(conn, symbol, start_date, "quarterly_revenue", rev_values[:4])

    if len(rev_values) >= 5:
        # QoQ revenue growth for last 4 quarters
        rev_growth_rates = []
        for i in range(min(4, len(rev_values) - 1)):
            if rev_values[i + 1]["revenue"] and rev_values[i + 1]["revenue"] != 0:
                gr = (rev_values[i]["revenue"] - rev_values[i + 1]["revenue"]) / abs(rev_values[i + 1]["revenue"]) * 100
                rev_growth_rates.append(round(gr, 2))

        save_param(conn, symbol, start_date, "revenue_growth_rates_qoq", rev_growth_rates)

        # Is growth accelerating? (each quarter's growth > previous)
        if len(rev_growth_rates) >= 3:
            accelerating = all(
                rev_growth_rates[i] > rev_growth_rates[i + 1]
                for i in range(min(2, len(rev_growth_rates) - 1))
            )
            save_param(conn, symbol, start_date, "revenue_accelerating", "Yes" if accelerating else "No")

        # YoY revenue growth
        if len(rev_values) >= 4 and rev_values[3]["revenue"] and rev_values[3]["revenue"] != 0:
            rev_yoy = (rev_values[0]["revenue"] - rev_values[3]["revenue"]) / abs(rev_values[3]["revenue"]) * 100
            save_param(conn, symbol, start_date, "revenue_growth_yoy_pct", round(rev_yoy, 2))

    # --- Gross Margin Trend ---
    margins = []
    for q in prior_quarters[:4]:
        rev = q.get("revenue", 0)
        cogs = q.get("costOfRevenue", 0)
        if rev and rev > 0:
            gm = (rev - cogs) / rev * 100
            margins.append({"date": q["date"], "gross_margin": round(gm, 2)})

    save_param(conn, symbol, start_date, "gross_margins_quarterly", margins)

    if len(margins) >= 2:
        margin_expanding = margins[0]["gross_margin"] > margins[-1]["gross_margin"]
        save_param(conn, symbol, start_date, "gross_margin_expanding", "Yes" if margin_expanding else "No")
        margin_delta = round(margins[0]["gross_margin"] - margins[-1]["gross_margin"], 2)
        save_param(conn, symbol, start_date, "gross_margin_change_pp", margin_delta)

    # --- Was company profitable? ---
    latest_ni = prior_quarters[0].get("netIncome", 0) if prior_quarters else 0
    save_param(conn, symbol, start_date, "net_income_last_q", latest_ni)
    save_param(conn, symbol, start_date, "was_profitable", "Yes" if latest_ni and latest_ni > 0 else "No")

    # --- FCF Breakeven ---
    # Try cash flow statement
    cf_data = fmp_get("cash-flow-statement", {"symbol": symbol, "period": "quarter"})
    time.sleep(REQUEST_DELAY)
    if cf_data and isinstance(cf_data, list):
        fcf_values = []
        for stmt in cf_data:
            if stmt.get("date", "") < start_date and len(fcf_values) < 4:
                fcf = stmt.get("freeCashFlow")
                if fcf is not None:
                    fcf_values.append({"date": stmt["date"], "fcf": fcf})

        save_param(conn, symbol, start_date, "quarterly_fcf", fcf_values)

        if len(fcf_values) >= 2:
            # Did FCF cross from negative to positive?
            fcf_turned_positive = fcf_values[0]["fcf"] > 0 and fcf_values[-1]["fcf"] < 0
            save_param(conn, symbol, start_date, "fcf_turned_positive", "Yes" if fcf_turned_positive else "No")

    log.info(f"    Fundamentals: EPS={eps_values[0]['eps'] if eps_values else 'N/A'} | "
             f"RevGrowth={'acc' if len(rev_values) >= 5 else 'N/A'}")


def analyze_earnings_surprise(symbol, start_date, conn):
    """Check earnings surprise around the start of the move."""
    sd = datetime.strptime(start_date, "%Y-%m-%d")

    # Get earnings surprises from FMP
    data = fmp_get("earnings-surprises", {"symbol": symbol})
    time.sleep(REQUEST_DELAY)

    if not data or not isinstance(data, list):
        return

    # Find the first earnings report in the 3 months around start_date
    window_start = (sd - timedelta(days=90)).strftime("%Y-%m-%d")
    window_end = (sd + timedelta(days=90)).strftime("%Y-%m-%d")

    relevant = []
    for e in data:
        edate = e.get("date", "")
        if window_start <= edate <= window_end:
            relevant.append(e)

    if not relevant:
        return

    # Sort by closest to start_date
    relevant.sort(key=lambda x: abs((datetime.strptime(x["date"], "%Y-%m-%d") - sd).days))
    closest = relevant[0]

    actual_eps = closest.get("actualEarningResult")
    est_eps = closest.get("estimatedEarning")

    if actual_eps is not None and est_eps is not None and est_eps != 0:
        surprise_pct = round((actual_eps - est_eps) / abs(est_eps) * 100, 2)
        save_param(conn, symbol, start_date, "earnings_surprise_pct", surprise_pct)
        save_param(conn, symbol, start_date, "earnings_surprise_date", closest["date"])
        save_param(conn, symbol, start_date, "earnings_actual_eps", actual_eps)
        save_param(conn, symbol, start_date, "earnings_est_eps", est_eps)

        log.info(f"    Earnings surprise: {surprise_pct}% on {closest['date']}")


# ---------------------------------------------------------------------------
# 2. Breakout Detection
# ---------------------------------------------------------------------------

def detect_breakouts(ticker, symbol, start_date, end_date, conn):
    """Find the exact breakout moment: volume surge + price breakout + catalyst convergence."""
    sd = pd.Timestamp(start_date)

    # Get 6 months before + the move period
    lookback_start = (sd - timedelta(days=180)).strftime("%Y-%m-%d")
    hist = ticker.history(start=lookback_start, end=end_date, auto_adjust=True)

    if hist.empty or len(hist) < 50:
        return

    # Make index timezone-naive for comparison
    hist.index = hist.index.tz_localize(None)

    closes = hist["Close"]
    volumes = hist["Volume"]
    highs = hist["High"]
    lows = hist["Low"]
    dates = hist.index

    # --- A. Volume Breakout Days ---
    # Days where volume >= 3X 20-day average AND close > open (green day)
    vol_ma20 = volumes.rolling(20).mean()
    volume_breakouts = []

    for i in range(20, len(hist)):
        if dates[i] < sd:
            continue  # Only look from start_date onwards

        avg_vol = vol_ma20.iloc[i]
        if avg_vol and avg_vol > 0:
            ratio = volumes.iloc[i] / avg_vol
            is_green = closes.iloc[i] > hist["Open"].iloc[i]

            if ratio >= 3.0 and is_green:
                volume_breakouts.append({
                    "date": dates[i].strftime("%Y-%m-%d"),
                    "volume_ratio": round(ratio, 2),
                    "price": round(closes.iloc[i], 4),
                    "change_pct": round((closes.iloc[i] - closes.iloc[i-1]) / closes.iloc[i-1] * 100, 2),
                })

    save_param(conn, symbol, start_date, "volume_breakout_days", volume_breakouts[:10])

    if volume_breakouts:
        first_vb = volume_breakouts[0]
        save_param(conn, symbol, start_date, "first_volume_breakout_date", first_vb["date"])
        save_param(conn, symbol, start_date, "first_volume_breakout_ratio", first_vb["volume_ratio"])
        save_breakout(conn, symbol, start_date, first_vb["date"], "volume",
                      first_vb["price"], first_vb["volume_ratio"],
                      f"Volume {first_vb['volume_ratio']}X avg, +{first_vb['change_pct']}%")

    # --- B. Price Breakout (52w high or MA200 cross) ---
    ma200 = closes.rolling(200).mean()
    high_52w = closes.rolling(252).max()

    price_breakouts = []
    for i in range(252, len(hist)):
        if dates[i] < sd:
            continue

        # Cross above MA200
        if i > 0 and ma200.iloc[i] and ma200.iloc[i-1]:
            if closes.iloc[i] > ma200.iloc[i] and closes.iloc[i-1] <= ma200.iloc[i-1]:
                price_breakouts.append({
                    "date": dates[i].strftime("%Y-%m-%d"),
                    "type": "ma200_cross",
                    "price": round(closes.iloc[i], 4),
                })

        # New 52-week high
        if high_52w.iloc[i] and closes.iloc[i] >= high_52w.iloc[i]:
            if i > 0 and closes.iloc[i-1] < high_52w.iloc[i-1]:
                price_breakouts.append({
                    "date": dates[i].strftime("%Y-%m-%d"),
                    "type": "52w_high",
                    "price": round(closes.iloc[i], 4),
                })

    save_param(conn, symbol, start_date, "price_breakout_days", price_breakouts[:10])

    if price_breakouts:
        first_pb = price_breakouts[0]
        save_param(conn, symbol, start_date, "first_price_breakout_date", first_pb["date"])
        save_param(conn, symbol, start_date, "first_price_breakout_type", first_pb["type"])
        save_breakout(conn, symbol, start_date, first_pb["date"], "price",
                      first_pb["price"], 0,
                      f"Price breakout: {first_pb['type']} at ${first_pb['price']}")

    # --- C. Combined Buy Signal ---
    # Find days where volume breakout + price breakout converge (within 5 trading days)
    buy_signals = []
    for vb in volume_breakouts[:5]:
        vb_date = datetime.strptime(vb["date"], "%Y-%m-%d")
        for pb in price_breakouts[:10]:
            pb_date = datetime.strptime(pb["date"], "%Y-%m-%d")
            gap = abs((vb_date - pb_date).days)
            if gap <= 7:  # Within a week
                signal_date = min(vb["date"], pb["date"])
                buy_signals.append({
                    "date": signal_date,
                    "volume_ratio": vb["volume_ratio"],
                    "price_breakout_type": pb["type"],
                    "price": vb["price"],
                })

    save_param(conn, symbol, start_date, "buy_signals", buy_signals)

    if buy_signals:
        first_bs = buy_signals[0]
        save_param(conn, symbol, start_date, "first_buy_signal_date", first_bs["date"])
        save_param(conn, symbol, start_date, "first_buy_signal_price", first_bs["price"])
        save_breakout(conn, symbol, start_date, first_bs["date"], "buy_signal",
                      first_bs["price"], first_bs["volume_ratio"],
                      f"BUY SIGNAL: Vol {first_bs['volume_ratio']}X + {first_bs['price_breakout_type']}")

        # Calculate return from buy signal to peak
        peak_date_str = conn.execute(
            "SELECT peak_date, peak_price FROM winners_v2 WHERE symbol=? AND start_date=?",
            (symbol, start_date)
        ).fetchone()
        if peak_date_str:
            peak_price = peak_date_str[1]
            signal_return = round((peak_price - first_bs["price"]) / first_bs["price"] * 100, 2)
            save_param(conn, symbol, start_date, "return_from_buy_signal_to_peak_pct", signal_return)
            log.info(f"    BUY SIGNAL: {first_bs['date']} at ${first_bs['price']} -> peak return +{signal_return}%")
    else:
        log.info(f"    No converged buy signal found")

    # --- Accumulation/Distribution ---
    # Count up-volume days vs down-volume days in 60 days before start
    pre_start = hist[hist.index < sd].tail(60)
    if len(pre_start) > 10:
        up_vol = pre_start[pre_start["Close"] > pre_start["Open"]]["Volume"].sum()
        down_vol = pre_start[pre_start["Close"] <= pre_start["Open"]]["Volume"].sum()
        if down_vol > 0:
            ad_ratio = round(up_vol / down_vol, 2)
            save_param(conn, symbol, start_date, "accumulation_distribution_ratio", ad_ratio)
            save_param(conn, symbol, start_date, "accumulation_signal",
                       "Yes" if ad_ratio > 1.5 else "No")

    # --- Days from start to first buy signal ---
    if buy_signals:
        bs_date = datetime.strptime(buy_signals[0]["date"], "%Y-%m-%d")
        days_to_signal = (bs_date - sd).days
        save_param(conn, symbol, start_date, "days_from_start_to_buy_signal", days_to_signal)


# ---------------------------------------------------------------------------
# 3. Price Action (pre-move)
# ---------------------------------------------------------------------------

def analyze_price_action(ticker, symbol, start_date, conn):
    """Price action analysis for the 12 months before the move."""
    sd = pd.Timestamp(start_date)
    pre_start = (sd - timedelta(days=365)).strftime("%Y-%m-%d")

    hist = ticker.history(start=pre_start, end=start_date, auto_adjust=True)
    if hist.empty or len(hist) < 20:
        return

    hist.index = hist.index.tz_localize(None)
    closes = hist["Close"]

    # 52-week low
    low_52w = round(closes.min(), 4)
    entry_price = round(closes.iloc[-1], 4)
    save_param(conn, symbol, start_date, "52w_low_before", low_52w)
    save_param(conn, symbol, start_date, "entry_price", entry_price)

    if low_52w > 0:
        pct_above = round((entry_price - low_52w) / low_52w * 100, 2)
        save_param(conn, symbol, start_date, "pct_above_52w_low", pct_above)

    # Days below $10 / $20
    save_param(conn, symbol, start_date, "days_below_10", int((closes < 10).sum()))
    save_param(conn, symbol, start_date, "days_below_20", int((closes < 20).sum()))

    # Volume ratio
    vol = hist["Volume"]
    avg_vol_12m = vol.mean()
    avg_vol_3m = vol.tail(60).mean() if len(vol) > 60 else avg_vol_12m
    vol_ratio = round(avg_vol_3m / avg_vol_12m, 2) if avg_vol_12m > 0 else 1.0
    save_param(conn, symbol, start_date, "volume_ratio_3m_vs_12m", vol_ratio)

    # Max daily move in 6 months before
    last_6m = hist.tail(126)
    if len(last_6m) > 1:
        daily_ret = last_6m["Close"].pct_change().dropna()
        if len(daily_ret) > 0:
            save_param(conn, symbol, start_date, "max_daily_move_6m_pct",
                       round(daily_ret.abs().max() * 100, 2))

    # Volatility
    if len(closes) > 20:
        daily_ret = closes.pct_change().dropna()
        save_param(conn, symbol, start_date, "daily_volatility_pct",
                   round(daily_ret.std() * 100, 4))

    # Prior year trend
    if len(closes) >= 20:
        first_avg = closes.iloc[:20].mean()
        last_avg = closes.iloc[-20:].mean()
        if first_avg > 0:
            trend = round((last_avg - first_avg) / first_avg * 100, 2)
            save_param(conn, symbol, start_date, "prior_year_trend_pct", trend)

    # Monthly prices
    monthly = {}
    for month in range(1, 13):
        md = hist[hist.index.month == month]
        if not md.empty:
            monthly[f"M{month:02d}"] = round(md["Close"].mean(), 4)
    save_param(conn, symbol, start_date, "monthly_prices_prior", monthly)


# ---------------------------------------------------------------------------
# 4. Market Context
# ---------------------------------------------------------------------------

def analyze_market_context(symbol, start_date, end_date, conn):
    """SPY, sector, VIX during the move period."""
    try:
        spy = yf.Ticker("SPY")
        spy_hist = spy.history(start=start_date, end=end_date, auto_adjust=True)
        if not spy_hist.empty and len(spy_hist) > 1:
            spy_ret = round((spy_hist["Close"].iloc[-1] - spy_hist["Close"].iloc[0]) / spy_hist["Close"].iloc[0] * 100, 2)
            save_param(conn, symbol, start_date, "spy_return_same_period", spy_ret)
    except: pass

    try:
        vix = yf.Ticker("^VIX")
        vix_hist = vix.history(start=start_date, end=end_date, auto_adjust=True)
        if not vix_hist.empty:
            save_param(conn, symbol, start_date, "avg_vix", round(vix_hist["Close"].mean(), 2))
    except: pass


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_csv(conn):
    conn.row_factory = sqlite3.Row
    winners = conn.execute("SELECT * FROM winners_v2 ORDER BY return_pct DESC").fetchall()
    params = [p[0] for p in conn.execute("SELECT DISTINCT param FROM analysis_v2 ORDER BY param").fetchall()]

    rows = []
    for w in winners:
        row = dict(w)
        for param in params:
            val = conn.execute(
                "SELECT value FROM analysis_v2 WHERE symbol=? AND start_date=? AND param=?",
                (w["symbol"], w["start_date"], param),
            ).fetchone()
            row[param] = val[0] if val else ""
        rows.append(row)

    if rows:
        with open(CSV_PATH, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        log.info(f"Exported {len(rows)} rows to {CSV_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_analysis():
    conn = sqlite3.connect(str(DB_PATH))
    winners = get_winners(conn)
    total = len(winners)

    log.info(f"V2 Deep Analysis for {total} winners...")
    log.info(f"FMP API key: {'set' if FMP_API_KEY else 'missing'}")

    for i, w in enumerate(winners, 1):
        symbol = w["symbol"]
        start_date = w["start_date"]
        end_date = w["end_date"]

        if is_analyzed(conn, symbol, start_date):
            log.info(f"[{i}/{total}] {symbol} ({start_date}) — already analyzed, skipping")
            continue

        log.info(f"[{i}/{total}] {symbol} | {start_date} -> {end_date} | +{w['return_pct']}%")

        try:
            ticker = yf.Ticker(symbol)

            # Save basic info
            info = ticker.info or {}
            save_param(conn, symbol, start_date, "sector", info.get("sector", ""))
            save_param(conn, symbol, start_date, "industry", info.get("industry", ""))
            save_param(conn, symbol, start_date, "short_pct_of_float", info.get("shortPercentOfFloat", "N/A"))
            save_param(conn, symbol, start_date, "institutional_pct", info.get("heldPercentInstitutions", "N/A"))

            # 1. Price action
            analyze_price_action(ticker, symbol, start_date, conn)
            time.sleep(REQUEST_DELAY)

            # 2. Historical fundamentals (FMP)
            if fmp_calls_used < FMP_DAILY_LIMIT:
                analyze_fundamentals_historical(symbol, start_date, conn)
                analyze_earnings_surprise(symbol, start_date, conn)

            # 3. Breakout detection
            detect_breakouts(ticker, symbol, start_date, end_date, conn)
            time.sleep(REQUEST_DELAY)

            # 4. Market context
            analyze_market_context(symbol, start_date, end_date, conn)

            conn.commit()
            log.info(f"[{i}/{total}] {symbol} — DONE")
            log.info("-" * 50)

        except Exception as e:
            log.error(f"[{i}/{total}] {symbol} — FAILED: {e}")
            conn.commit()

        time.sleep(REQUEST_DELAY)

    export_csv(conn)

    log.info("=" * 60)
    log.info(f"V2 ANALYSIS COMPLETE | FMP calls used: {fmp_calls_used}")
    log.info("=" * 60)

    # Summary of breakouts found
    breakouts = conn.execute(
        "SELECT symbol, start_date, breakout_date, breakout_type, description "
        "FROM breakouts WHERE breakout_type='buy_signal' ORDER BY breakout_date"
    ).fetchall()
    log.info(f"Buy signals found: {len(breakouts)}")
    for b in breakouts:
        log.info(f"  {b[0]}: {b[2]} — {b[4]}")

    conn.close()


if __name__ == "__main__":
    run_analysis()
