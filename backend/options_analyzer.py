"""
ALPHA HUNTER — Options Analyzer
Analyzes LEAPS options suitability for watchlist stocks.
Scores each stock and builds full recommendation.
"""

import json
import sys
import logging
import math
from datetime import datetime, date
from pathlib import Path

import yfinance as yf
import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FUND_PATH = DATA_DIR / "cache" / "fundamentals.json"
CACHE_DIR = BASE_DIR / "cache" / "prices"
OPTIONS_PATH = DATA_DIR / "options_analysis.json"
WATCHLIST_PATH = DATA_DIR / "watchlist.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("options")


# =========================================================================
# TASK 1 — Fetch real options data
# =========================================================================

def fetch_options_data(ticker: str) -> dict:
    """Fetch options chain, IV, and LEAPS for a ticker."""
    t = yf.Ticker(ticker)
    info = t.info or {}

    current_price = info.get("currentPrice") or info.get("regularMarketPrice", 0)
    if not current_price:
        # Fallback to cache
        price_path = CACHE_DIR / f"{ticker}.parquet"
        if price_path.exists():
            hist = pd.read_parquet(price_path)
            current_price = float(hist["Close"].iloc[-1]) if len(hist) > 0 else 0

    # Get all expirations
    try:
        expirations = t.options
    except:
        return {"ticker": ticker, "error": "No options available", "current_price": current_price}

    if not expirations:
        return {"ticker": ticker, "error": "No options expirations", "current_price": current_price}

    # Find LEAPS (expirations > 6 months out, prefer Jan 2027/2028)
    today = date.today()
    leaps_exps = []
    for exp_str in expirations:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        days_out = (exp_date - today).days
        if days_out >= 180:  # At least 6 months
            leaps_exps.append({"date": exp_str, "days_out": days_out})

    leaps_exps.sort(key=lambda x: x["days_out"])

    # Get IV from options chain (use nearest monthly for current IV)
    current_iv = None
    iv_history = []

    # Fetch chain for first LEAPS expiration
    chains = []
    for exp_info in leaps_exps[:3]:  # Analyze up to 3 expirations
        try:
            chain = t.option_chain(exp_info["date"])
            calls = chain.calls

            if calls.empty:
                continue

            # Find ATM call (closest to current price)
            calls["dist"] = abs(calls["strike"] - current_price)
            atm_idx = calls["dist"].idxmin()
            atm = calls.loc[atm_idx]

            # Find OTM call (~10-15% above)
            otm_target = current_price * 1.12
            calls["dist_otm"] = abs(calls["strike"] - otm_target)
            otm_idx = calls["dist_otm"].idxmin()
            otm = calls.loc[otm_idx]

            # Extract IV from ATM option
            atm_iv = float(atm.get("impliedVolatility", 0))
            if current_iv is None:
                current_iv = atm_iv

            chain_data = {
                "expiration": exp_info["date"],
                "days_out": exp_info["days_out"],
                "atm": {
                    "strike": float(atm["strike"]),
                    "premium": float(atm["lastPrice"]) if not pd.isna(atm["lastPrice"]) else float(atm.get("ask", 0)),
                    "bid": float(atm.get("bid", 0)),
                    "ask": float(atm.get("ask", 0)),
                    "iv": atm_iv,
                    "delta": estimate_delta(current_price, float(atm["strike"]), atm_iv, exp_info["days_out"] / 365),
                    "open_interest": int(atm.get("openInterest", 0)) if not pd.isna(atm.get("openInterest")) else 0,
                    "volume": int(atm.get("volume", 0)) if not pd.isna(atm.get("volume")) else 0,
                },
                "otm": {
                    "strike": float(otm["strike"]),
                    "premium": float(otm["lastPrice"]) if not pd.isna(otm["lastPrice"]) else float(otm.get("ask", 0)),
                    "bid": float(otm.get("bid", 0)),
                    "ask": float(otm.get("ask", 0)),
                    "iv": float(otm.get("impliedVolatility", 0)),
                    "delta": estimate_delta(current_price, float(otm["strike"]), float(otm.get("impliedVolatility", atm_iv)), exp_info["days_out"] / 365),
                    "open_interest": int(otm.get("openInterest", 0)) if not pd.isna(otm.get("openInterest")) else 0,
                    "volume": int(otm.get("volume", 0)) if not pd.isna(otm.get("volume")) else 0,
                },
            }
            chains.append(chain_data)
        except Exception as e:
            log.debug(f"  {ticker} chain {exp_info['date']}: {e}")

    # IV percentile (estimate from historical volatility)
    iv_percentile = estimate_iv_percentile(ticker, current_iv)

    return {
        "ticker": ticker,
        "current_price": round(current_price, 2),
        "current_iv": round(current_iv * 100, 1) if current_iv else None,
        "iv_percentile": iv_percentile,
        "leaps_available": len(leaps_exps),
        "leaps_expirations": [e["date"] for e in leaps_exps[:5]],
        "chains": chains,
    }


def estimate_delta(S, K, iv, T):
    """Simple Black-Scholes delta approximation."""
    if iv <= 0 or T <= 0 or S <= 0:
        return 0.5
    try:
        d1 = (math.log(S / K) + (0.05 + iv**2 / 2) * T) / (iv * math.sqrt(T))
        # Approximate normal CDF
        delta = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
        return round(delta, 3)
    except:
        return 0.5


def estimate_iv_percentile(ticker: str, current_iv: float) -> int:
    """Estimate IV percentile using historical price volatility as proxy."""
    if not current_iv:
        return 50

    price_path = CACHE_DIR / f"{ticker}.parquet"
    if not price_path.exists():
        return 50

    hist = pd.read_parquet(price_path)
    if len(hist) < 252:
        return 50

    # Calculate realized vol for rolling 30-day windows over last year
    returns = hist["Close"].pct_change().dropna()
    rolling_vol = returns.rolling(30).std() * math.sqrt(252)
    rolling_vol = rolling_vol.dropna()

    if len(rolling_vol) < 50:
        return 50

    # Where does current IV sit relative to historical vol?
    percentile = int((rolling_vol < current_iv).sum() / len(rolling_vol) * 100)
    return min(max(percentile, 0), 100)


# =========================================================================
# TASK 2 — Score each stock
# =========================================================================

def score_stock(ticker: str, options_data: dict, fundamentals: dict) -> dict:
    """Score a stock for options suitability."""
    fund = fundamentals.get(ticker, {})

    # IV SCORE (0-30) — lower IV = cheaper = better
    iv_pct = options_data.get("iv_percentile", 50)
    if iv_pct <= 25:
        iv_score = 30
        iv_reason = f"IV בתחתית 25% — אופציות זולות"
    elif iv_pct <= 50:
        iv_score = 20
        iv_reason = f"IV בחצי התחתון — מחיר סביר"
    elif iv_pct <= 75:
        iv_score = 10
        iv_reason = f"IV גבוה — אופציות יקרות"
    else:
        iv_score = 5
        iv_reason = f"IV בשיא — אופציות יקרות מאוד"

    # CATALYST SCORE (0-25) — revenue growth
    rev_growth = fund.get("rev_growth")
    rev_pct = round(rev_growth * 100, 1) if rev_growth else 0
    if rev_pct >= 50:
        catalyst_score = 25
        catalyst_reason = f"צמיחה +{rev_pct}% — קטליסט חזק"
    elif rev_pct >= 25:
        catalyst_score = 18
        catalyst_reason = f"צמיחה +{rev_pct}% — קטליסט טוב"
    elif rev_pct >= 15:
        catalyst_score = 12
        catalyst_reason = f"צמיחה +{rev_pct}% — קטליסט בינוני"
    else:
        catalyst_score = 5
        catalyst_reason = f"צמיחה +{rev_pct}% — קטליסט חלש"

    # TIMING SCORE (0-25) — distance from MA200
    price_path = CACHE_DIR / f"{ticker}.parquet"
    dist_ma200 = 0
    if price_path.exists():
        hist = pd.read_parquet(price_path)
        if len(hist) >= 200:
            current = float(hist["Close"].iloc[-1])
            ma200 = float(hist["Close"].tail(200).mean())
            dist_ma200 = (current - ma200) / ma200 * 100

    if dist_ma200 > 0:
        timing_score = 25
        timing_reason = f"כבר חצה MA200 (+{dist_ma200:.1f}%)"
    elif dist_ma200 > -5:
        timing_score = 18
        timing_reason = f"קרוב מאוד ל-MA200 ({dist_ma200:.1f}%)"
    elif dist_ma200 > -10:
        timing_score = 12
        timing_reason = f"מתקרב ל-MA200 ({dist_ma200:.1f}%)"
    else:
        timing_score = 5
        timing_reason = f"רחוק מ-MA200 ({dist_ma200:.1f}%)"

    # SHORT SQUEEZE SCORE (0-20)
    short_pct = fund.get("short_pct")
    short_val = round(short_pct * 100, 1) if short_pct else 0
    if short_val >= 15:
        short_score = 20
        short_reason = f"שורט {short_val}% — פוטנציאל סקוויז גבוה"
    elif short_val >= 10:
        short_score = 15
        short_reason = f"שורט {short_val}% — פוטנציאל סקוויז"
    elif short_val >= 7:
        short_score = 10
        short_reason = f"שורט {short_val}% — דלק מתון"
    else:
        short_score = 5
        short_reason = f"שורט {short_val}% — נמוך"

    total = iv_score + catalyst_score + timing_score + short_score

    return {
        "ticker": ticker,
        "total_score": total,
        "iv_score": iv_score,
        "catalyst_score": catalyst_score,
        "timing_score": timing_score,
        "short_score": short_score,
        "iv_reason": iv_reason,
        "catalyst_reason": catalyst_reason,
        "timing_reason": timing_reason,
        "short_reason": short_reason,
        "iv_percentile": iv_pct,
        "current_iv_pct": options_data.get("current_iv"),
        "rev_growth_pct": rev_pct,
        "dist_ma200_pct": round(dist_ma200, 1),
        "short_pct": short_val,
    }


# =========================================================================
# TASK 3 — Build full recommendation
# =========================================================================

def build_recommendation(winner: dict, options_data: dict, all_scores: list, capital: float = 10000) -> dict:
    """Build full LEAPS recommendation for the top stock."""
    ticker = winner["ticker"]
    price = options_data["current_price"]
    chains = options_data.get("chains", [])

    if not chains:
        return {"error": f"No options chains for {ticker}"}

    # Pick the best LEAPS chain (prefer 12-18 months out)
    best_chain = chains[0]
    for c in chains:
        if 300 <= c["days_out"] <= 600:
            best_chain = c
            break

    atm = best_chain["atm"]
    otm = best_chain["otm"]
    days_out = best_chain["days_out"]
    expiration = best_chain["expiration"]

    atm_premium = atm["premium"] or atm["ask"]
    otm_premium = otm["premium"] or otm["ask"]
    atm_strike = atm["strike"]
    otm_strike = otm["strike"]

    # Break-even
    atm_breakeven = atm_strike + atm_premium
    otm_breakeven = otm_strike + otm_premium

    # Cost per contract
    atm_contract_cost = round(atm_premium * 100, 2)
    otm_contract_cost = round(otm_premium * 100, 2)

    # Scenarios (intrinsic value at expiry, ignore remaining time value)
    scenarios = []
    for move_pct in [100, 300, 500, 0, -20]:
        future_price = price * (1 + move_pct / 100)

        atm_value = max(0, future_price - atm_strike)
        atm_return = (atm_value - atm_premium) / atm_premium * 100 if atm_premium > 0 else 0

        otm_value = max(0, future_price - otm_strike)
        otm_return = (otm_value - otm_premium) / otm_premium * 100 if otm_premium > 0 else 0

        scenarios.append({
            "stock_move_pct": move_pct,
            "future_price": round(future_price, 2),
            "atm_value": round(atm_value, 2),
            "atm_return_pct": round(atm_return, 1),
            "otm_value": round(otm_value, 2),
            "otm_return_pct": round(otm_return, 1),
        })

    # Portfolio split options
    # Get allocation weights from scores
    total_score_sum = sum(s["total_score"] for s in all_scores)
    allocations = {}
    for s in all_scores:
        allocations[s["ticker"]] = round(s["total_score"] / total_score_sum * 70, 1)  # 70% equity

    contracts_atm = max(1, int(capital * 0.2 / atm_contract_cost)) if atm_contract_cost > 0 else 0
    contracts_otm = max(1, int(capital * 0.2 / otm_contract_cost)) if otm_contract_cost > 0 else 0

    # Option A: Conservative (80% stock, 20% LEAPS)
    leaps_budget_a = capital * 0.20
    stock_budget_a = capital * 0.80
    contracts_a = max(1, int(leaps_budget_a / atm_contract_cost)) if atm_contract_cost > 0 else 0
    max_loss_a = leaps_budget_a  # Can lose all LEAPS premium
    # Best case: stock 500% + LEAPS ~1000%+
    best_stock_a = stock_budget_a * 5
    best_leaps_a = contracts_a * max(0, price * 6 - atm_strike) * 100
    best_total_a = best_stock_a + best_leaps_a

    # Option B: Aggressive (50% stock, 50% LEAPS)
    leaps_budget_b = capital * 0.50
    stock_budget_b = capital * 0.50
    contracts_b = max(1, int(leaps_budget_b / atm_contract_cost)) if atm_contract_cost > 0 else 0
    max_loss_b = leaps_budget_b
    best_stock_b = stock_budget_b * 5
    best_leaps_b = contracts_b * max(0, price * 6 - atm_strike) * 100
    best_total_b = best_stock_b + best_leaps_b

    # Option C: All LEAPS
    contracts_c = max(1, int(capital / atm_contract_cost)) if atm_contract_cost > 0 else 0
    best_leaps_c = contracts_c * max(0, price * 6 - atm_strike) * 100

    # Why this stock
    other_tickers = [s["ticker"] for s in all_scores if s["ticker"] != ticker]
    why_this = f"{ticker} מקבל את הציון הגבוה ביותר ({winner['total_score']}/100) "
    why_this += f"בזכות {winner['timing_reason'].split('—')[0].strip()} ו{winner['catalyst_reason'].split('—')[0].strip()}. "
    if other_tickers:
        why_this += f"עדיף על {'/'.join(other_tickers)} בגלל תזמון טוב יותר."

    return {
        "ticker": ticker,
        "current_price": price,
        "why_options": "LEAPS מאפשרות חשיפה ל-500%+ עם סיכון מוגבל. אם המניה לא זזה — מפסידים רק את הפרמיה, לא את כל ההשקעה.",
        "why_this_stock": why_this,
        "expiration": expiration,
        "days_to_expiry": days_out,
        "atm": {
            "strike": atm_strike,
            "premium_per_share": atm_premium,
            "cost_per_contract": atm_contract_cost,
            "breakeven": round(atm_breakeven, 2),
            "delta": atm["delta"],
            "iv": atm["iv"],
            "open_interest": atm["open_interest"],
        },
        "otm": {
            "strike": otm_strike,
            "premium_per_share": otm_premium,
            "cost_per_contract": otm_contract_cost,
            "breakeven": round(otm_breakeven, 2),
            "delta": otm["delta"],
            "iv": otm["iv"],
            "open_interest": otm["open_interest"],
        },
        "scenarios": scenarios,
        "portfolio_options": {
            "capital": capital,
            "option_a": {
                "name": "שמרני (80% מניה + 20% LEAPS)",
                "stock_pct": 80,
                "leaps_pct": 20,
                "stock_amount": round(stock_budget_a, 2),
                "leaps_amount": round(leaps_budget_a, 2),
                "contracts": contracts_a,
                "max_loss": round(max_loss_a, 2),
                "max_loss_pct": 20,
                "best_case": round(best_total_a, 2),
                "best_case_pct": round((best_total_a - capital) / capital * 100, 0),
            },
            "option_b": {
                "name": "אגרסיבי (50% מניה + 50% LEAPS)",
                "stock_pct": 50,
                "leaps_pct": 50,
                "stock_amount": round(stock_budget_b, 2),
                "leaps_amount": round(leaps_budget_b, 2),
                "contracts": contracts_b,
                "max_loss": round(max_loss_b, 2),
                "max_loss_pct": 50,
                "best_case": round(best_total_b, 2),
                "best_case_pct": round((best_total_b - capital) / capital * 100, 0),
            },
            "option_c": {
                "name": "LEAPS בלבד (100% — סיכון מקסימלי)",
                "stock_pct": 0,
                "leaps_pct": 100,
                "stock_amount": 0,
                "leaps_amount": capital,
                "contracts": contracts_c,
                "max_loss": capital,
                "max_loss_pct": 100,
                "best_case": round(best_leaps_c, 2),
                "best_case_pct": round((best_leaps_c - capital) / capital * 100, 0),
            },
        },
    }


# =========================================================================
# Main
# =========================================================================

def run():
    log.info("ALPHA HUNTER — Options Analyzer")
    log.info("=" * 60)

    # Load watchlist
    if not WATCHLIST_PATH.exists():
        log.error("No watchlist.json!")
        return

    with open(WATCHLIST_PATH) as f:
        wl = json.load(f)

    tickers = [item["ticker"] for item in wl["watchlist"]]
    log.info(f"Analyzing {len(tickers)} watchlist stocks: {tickers}")

    # Load fundamentals
    fundamentals = {}
    if FUND_PATH.exists():
        with open(FUND_PATH) as f:
            fundamentals = json.load(f)

    # Task 1: Fetch options data
    log.info("\n--- TASK 1: Fetching options data ---")
    all_options = {}
    for ticker in tickers:
        log.info(f"  Fetching {ticker}...")
        data = fetch_options_data(ticker)
        all_options[ticker] = data
        if data.get("chains"):
            log.info(f"    IV: {data.get('current_iv')}% | LEAPS: {data.get('leaps_available')} expirations | Chains: {len(data['chains'])}")
        else:
            log.info(f"    {data.get('error', 'No data')}")

    # Task 2: Score each stock
    log.info("\n--- TASK 2: Scoring ---")
    scores = []
    for ticker in tickers:
        score = score_stock(ticker, all_options[ticker], fundamentals)
        scores.append(score)
        log.info(f"  {ticker}: {score['total_score']}/100 "
                 f"(IV={score['iv_score']}, Cat={score['catalyst_score']}, "
                 f"Time={score['timing_score']}, Short={score['short_score']})")

    scores.sort(key=lambda x: x["total_score"], reverse=True)
    winner = scores[0]
    log.info(f"\n  🏆 הכי טוב לאופציות: {winner['ticker']} ({winner['total_score']}/100)")

    # Task 3: Build recommendation
    log.info("\n--- TASK 3: Full Recommendation ---")
    recommendation = build_recommendation(winner, all_options[winner["ticker"]], scores)

    if "error" in recommendation:
        log.error(f"  {recommendation['error']}")
    else:
        log.info(f"\n{'='*60}")
        log.info(f"  המלצה: {recommendation['ticker']} LEAPS")
        log.info(f"{'='*60}")
        log.info(f"  מחיר נוכחי: ${recommendation['current_price']}")
        log.info(f"  למה אופציות: {recommendation['why_options']}")
        log.info(f"  למה {recommendation['ticker']}: {recommendation['why_this_stock']}")
        log.info(f"\n  ATM Call:")
        atm = recommendation['atm']
        log.info(f"    סטרייק: ${atm['strike']} | פרמיה: ${atm['premium_per_share']}/share (${atm['cost_per_contract']}/contract)")
        log.info(f"    Break-even: ${atm['breakeven']} | Delta: {atm['delta']} | IV: {round(atm['iv']*100,1)}%")
        log.info(f"\n  OTM Call (~12% above):")
        otm = recommendation['otm']
        log.info(f"    סטרייק: ${otm['strike']} | פרמיה: ${otm['premium_per_share']}/share (${otm['cost_per_contract']}/contract)")
        log.info(f"    Break-even: ${otm['breakeven']} | Delta: {otm['delta']}")

        log.info(f"\n  תרחישים (ATM call):")
        log.info(f"  {'מהלך מניה':<15s} {'שווי אופציה':<15s} {'תשואה':<10s}")
        for s in recommendation['scenarios']:
            log.info(f"  {'+' if s['stock_move_pct']>=0 else ''}{s['stock_move_pct']}% (${s['future_price']})  "
                     f"${s['atm_value']:<10}  {'+' if s['atm_return_pct']>=0 else ''}{s['atm_return_pct']}%")

        log.info(f"\n  חלוקת תיק ($10,000):")
        for key in ["option_a", "option_b", "option_c"]:
            opt = recommendation['portfolio_options'][key]
            log.info(f"    {opt['name']}:")
            log.info(f"      מניה: ${opt['stock_amount']} | LEAPS: ${opt['leaps_amount']} ({opt['contracts']} contracts)")
            log.info(f"      הפסד מקסי: -${opt['max_loss']} (-{opt['max_loss_pct']}%)")
            log.info(f"      Best case: +${opt['best_case']-10000:,.0f} (+{opt['best_case_pct']}%)")

    # Save full report
    report = {
        "date": date.today().isoformat(),
        "watchlist": tickers,
        "options_data": all_options,
        "scores": scores,
        "winner": winner["ticker"],
        "recommendation": recommendation,
    }

    with open(OPTIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    log.info(f"\nReport saved to {OPTIONS_PATH}")

    return report


if __name__ == "__main__":
    run()
