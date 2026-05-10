"""
ALPHA HUNTER — Phase 3: Deep Analysis
For every winner, analyze the 12 months BEFORE the explosion year.
Collects price action, fundamentals, catalysts, and market context.
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
CSV_PATH = DATA_DIR / "winners_analysis.csv"

FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FMP_STABLE = "https://financialmodelingprep.com/stable"

REQUEST_DELAY = 0.5

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("analyzer")

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_winners(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM winners ORDER BY year, ytd_return DESC").fetchall()
    return [dict(r) for r in rows]


def save_param(conn: sqlite3.Connection, symbol: str, year: int, param: str, value):
    val = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    conn.execute(
        "INSERT OR REPLACE INTO analysis (symbol, year, param, value) VALUES (?,?,?,?)",
        (symbol, year, param, val),
    )


def is_analyzed(conn: sqlite3.Connection, symbol: str, year: int) -> bool:
    count = conn.execute(
        "SELECT COUNT(*) FROM analysis WHERE symbol=? AND year=?",
        (symbol, year),
    ).fetchone()[0]
    return count > 5  # At least a few params saved


# ---------------------------------------------------------------------------
# FMP helper (new stable API)
# ---------------------------------------------------------------------------

def fmp_get(endpoint: str, params: dict = None) -> dict | list | None:
    if not FMP_API_KEY or FMP_API_KEY == "your_key_here":
        return None
    params = params or {}
    params["apikey"] = FMP_API_KEY
    url = f"{FMP_STABLE}/{endpoint}"
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        log.debug(f"FMP request failed: {endpoint} — {e}")
    return None


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def analyze_price_action(ticker: yf.Ticker, symbol: str, year: int, conn: sqlite3.Connection):
    """Analyze price action in the 12 months BEFORE the explosion year."""
    prior_year = year - 1
    start = f"{prior_year}-01-01"
    end = f"{prior_year}-12-31"
    explosion_start = f"{year}-01-01"
    explosion_end = f"{year}-12-31"

    # Get prior year data
    hist_prior = ticker.history(start=start, end=end, auto_adjust=True)
    # Get explosion year data
    hist_explosion = ticker.history(start=explosion_start, end=explosion_end, auto_adjust=True)

    if hist_prior.empty:
        log.warning(f"  {symbol}/{year}: No prior year data")
        return

    # Monthly prices (prior year)
    monthly_prices = {}
    for month in range(1, 13):
        month_data = hist_prior[hist_prior.index.month == month]
        if not month_data.empty:
            monthly_prices[f"{prior_year}-{month:02d}"] = round(month_data["Close"].mean(), 4)
    save_param(conn, symbol, year, "monthly_prices_prior_year", monthly_prices)

    # 52-week low before explosion
    low_52w = round(hist_prior["Low"].min(), 4)
    save_param(conn, symbol, year, "52w_low_before", low_52w)

    # Entry price (first trading day of explosion year)
    if not hist_explosion.empty:
        entry_price = round(hist_explosion["Close"].iloc[0], 4)
        save_param(conn, symbol, year, "entry_price", entry_price)

        # How far from 52w low was entry?
        if low_52w > 0:
            pct_above_low = round((entry_price - low_52w) / low_52w * 100, 2)
            save_param(conn, symbol, year, "pct_above_52w_low_at_entry", pct_above_low)

    # Days trading below $10 in prior year
    days_below_10 = int((hist_prior["Close"] < 10).sum())
    save_param(conn, symbol, year, "days_below_10_prior_year", days_below_10)

    # Days trading below $20 in prior year
    days_below_20 = int((hist_prior["Close"] < 20).sum())
    save_param(conn, symbol, year, "days_below_20_prior_year", days_below_20)

    # Average daily volume: last 3 months vs 12-month avg
    avg_vol_12m = hist_prior["Volume"].mean()
    last_3m = hist_prior[hist_prior.index.month >= 10]
    avg_vol_3m = last_3m["Volume"].mean() if not last_3m.empty else avg_vol_12m

    if avg_vol_12m > 0:
        volume_ratio = round(avg_vol_3m / avg_vol_12m, 2)
    else:
        volume_ratio = 1.0
    save_param(conn, symbol, year, "volume_ratio_3m_vs_12m", volume_ratio)
    save_param(conn, symbol, year, "avg_daily_volume_prior_year", round(avg_vol_12m))
    save_param(conn, symbol, year, "avg_daily_volume_last_3m", round(avg_vol_3m))

    # Biggest single-day % move in 6 months before explosion
    last_6m = hist_prior[hist_prior.index.month >= 7]
    if not last_6m.empty and len(last_6m) > 1:
        daily_returns = last_6m["Close"].pct_change().dropna()
        if not daily_returns.empty:
            max_daily_move = round(daily_returns.abs().max() * 100, 2)
            save_param(conn, symbol, year, "max_daily_move_6m_before_pct", max_daily_move)

    # Price trend: was it rising or falling in prior year?
    if len(hist_prior) >= 20:
        first_month_avg = hist_prior["Close"].iloc[:20].mean()
        last_month_avg = hist_prior["Close"].iloc[-20:].mean()
        if first_month_avg > 0:
            prior_year_trend = round((last_month_avg - first_month_avg) / first_month_avg * 100, 2)
            save_param(conn, symbol, year, "prior_year_price_trend_pct", prior_year_trend)

    # Volatility (std of daily returns)
    if len(hist_prior) > 20:
        daily_ret = hist_prior["Close"].pct_change().dropna()
        volatility = round(daily_ret.std() * 100, 4)
        save_param(conn, symbol, year, "prior_year_daily_volatility_pct", volatility)

    log.info(f"  {symbol}/{year}: Price action done | 52wLow=${low_52w} | VolRatio={volume_ratio}")


def analyze_fundamentals(ticker: yf.Ticker, symbol: str, year: int, conn: sqlite3.Connection):
    """Analyze fundamentals at start of explosion year."""
    info = ticker.info or {}

    # Market cap (at start of year — already in winners table, but save to analysis too)
    market_cap = info.get("marketCap", 0)
    save_param(conn, symbol, year, "current_market_cap", market_cap)

    # Sector & Industry
    sector = info.get("sector", "Unknown")
    industry = info.get("industry", "Unknown")
    save_param(conn, symbol, year, "sector", sector)
    save_param(conn, symbol, year, "industry", industry)

    # P/E ratio
    pe = info.get("trailingPE") or info.get("forwardPE")
    save_param(conn, symbol, year, "pe_ratio", pe or "N/A")

    # Was company profitable?
    trailing_eps = info.get("trailingEps")
    save_param(conn, symbol, year, "trailing_eps", trailing_eps or "N/A")
    if trailing_eps is not None:
        save_param(conn, symbol, year, "was_profitable", "Yes" if trailing_eps > 0 else "No")
    else:
        save_param(conn, symbol, year, "was_profitable", "Unknown")

    # Debt/Equity
    debt_equity = info.get("debtToEquity")
    save_param(conn, symbol, year, "debt_to_equity", debt_equity or "N/A")

    # Revenue growth
    revenue_growth = info.get("revenueGrowth")
    if revenue_growth is not None:
        save_param(conn, symbol, year, "revenue_growth_yoy", round(revenue_growth * 100, 2))
    else:
        save_param(conn, symbol, year, "revenue_growth_yoy", "N/A")

    # Cash position
    total_cash = info.get("totalCash")
    save_param(conn, symbol, year, "total_cash", total_cash or "N/A")

    # Free cash flow
    fcf = info.get("freeCashflow")
    save_param(conn, symbol, year, "free_cash_flow", fcf or "N/A")

    # Shares outstanding
    shares = info.get("sharesOutstanding")
    save_param(conn, symbol, year, "shares_outstanding", shares or "N/A")

    # Float
    float_shares = info.get("floatShares")
    save_param(conn, symbol, year, "float_shares", float_shares or "N/A")

    # Short interest
    short_pct = info.get("shortPercentOfFloat")
    save_param(conn, symbol, year, "short_pct_of_float", short_pct or "N/A")

    # Insider ownership
    insider_pct = info.get("heldPercentInsiders")
    save_param(conn, symbol, year, "insider_ownership_pct", insider_pct or "N/A")

    # Institutional ownership
    inst_pct = info.get("heldPercentInstitutions")
    save_param(conn, symbol, year, "institutional_ownership_pct", inst_pct or "N/A")

    # Price to book
    pb = info.get("priceToBook")
    save_param(conn, symbol, year, "price_to_book", pb or "N/A")

    # IPO date / company age
    ipo_date = info.get("ipoDate") or ""
    if not ipo_date:
        # Try FMP
        fmp_profile = fmp_get("profile", {"symbol": symbol})
        if fmp_profile and isinstance(fmp_profile, list) and len(fmp_profile) > 0:
            ipo_date = fmp_profile[0].get("ipoDate", "")
            time.sleep(REQUEST_DELAY)

    save_param(conn, symbol, year, "ipo_date", ipo_date or "N/A")

    if ipo_date and ipo_date != "N/A":
        try:
            ipo = datetime.strptime(ipo_date, "%Y-%m-%d")
            explosion_start = datetime(year, 1, 1)
            age_years = round((explosion_start - ipo).days / 365.25, 1)
            save_param(conn, symbol, year, "years_since_ipo", age_years)
            save_param(conn, symbol, year, "recently_iped", "Yes" if age_years < 2 else "No")
        except ValueError:
            pass

    log.info(f"  {symbol}/{year}: Fundamentals done | PE={pe} | D/E={debt_equity} | Sector={sector}")


def analyze_fundamentals_historical(symbol: str, year: int, conn: sqlite3.Connection):
    """Try to get historical financials from FMP for the year before explosion."""
    prior_year = year - 1

    # Income statement
    data = fmp_get("income-statement", {"symbol": symbol, "period": "annual"})
    if data and isinstance(data, list):
        for stmt in data:
            fiscal_year = str(stmt.get("calendarYear", ""))
            if fiscal_year == str(prior_year):
                revenue = stmt.get("revenue", 0)
                net_income = stmt.get("netIncome", 0)
                eps = stmt.get("eps", 0)
                save_param(conn, symbol, year, f"revenue_{prior_year}", revenue)
                save_param(conn, symbol, year, f"net_income_{prior_year}", net_income)
                save_param(conn, symbol, year, f"eps_{prior_year}", eps)
                save_param(conn, symbol, year, "was_profitable_historical",
                           "Yes" if net_income > 0 else "No")
                log.info(f"  {symbol}/{year}: FMP financials — Rev=${revenue:,.0f} | NI=${net_income:,.0f}")
                break
        time.sleep(REQUEST_DELAY)

    # Quarterly EPS trend (last 4 quarters before explosion)
    data = fmp_get("income-statement", {"symbol": symbol, "period": "quarter"})
    if data and isinstance(data, list):
        eps_trend = []
        for stmt in data:
            stmt_date = stmt.get("date", "")
            if stmt_date and stmt_date < f"{year}-01-01" and len(eps_trend) < 4:
                eps_trend.append({
                    "date": stmt_date,
                    "eps": stmt.get("eps", 0),
                    "revenue": stmt.get("revenue", 0),
                })
        if eps_trend:
            save_param(conn, symbol, year, "quarterly_eps_trend", eps_trend)
            # Were EPS improving?
            if len(eps_trend) >= 2:
                eps_values = [e["eps"] for e in eps_trend if e["eps"] is not None]
                if len(eps_values) >= 2:
                    eps_improving = eps_values[0] > eps_values[-1]  # Most recent > oldest
                    save_param(conn, symbol, year, "eps_improving", "Yes" if eps_improving else "No")
        time.sleep(REQUEST_DELAY)


def analyze_catalysts(ticker: yf.Ticker, symbol: str, year: int, conn: sqlite3.Connection):
    """Look for catalysts during the explosion year."""
    # News from yfinance
    try:
        news = ticker.news
        if news:
            # Filter for explosion year news
            year_news = []
            for article in news[:20]:
                title = article.get("title", "")
                pub_date = article.get("providerPublishTime", 0)
                if pub_date:
                    dt = datetime.fromtimestamp(pub_date)
                    if dt.year == year:
                        year_news.append({
                            "date": dt.strftime("%Y-%m-%d"),
                            "title": title,
                        })

            if year_news:
                save_param(conn, symbol, year, "news_during_explosion", year_news[:10])
                save_param(conn, symbol, year, "first_big_news", year_news[0].get("title", ""))
            else:
                save_param(conn, symbol, year, "news_during_explosion", "None found (historical)")
    except Exception as e:
        log.debug(f"  {symbol}/{year}: News fetch failed: {e}")
        save_param(conn, symbol, year, "news_during_explosion", "Failed to fetch")

    # Classify the stock type / catalyst category
    info = ticker.info or {}
    sector = info.get("sector", "")
    industry = info.get("industry", "")
    name = (info.get("longName") or info.get("shortName") or symbol).lower()

    # Try to identify catalyst type
    catalyst_type = "Unknown"
    if year == 2020 and sector == "Healthcare":
        catalyst_type = "COVID/Biotech"
    elif year == 2020 and "electric" in industry.lower():
        catalyst_type = "EV Mania"
    elif year in (2020, 2021) and any(w in name for w in ["clean", "solar", "fuel", "hydrogen", "energy"]):
        catalyst_type = "Clean Energy"
    elif year == 2021 and symbol in ("GME", "AMC", "BBBY"):
        catalyst_type = "Meme/Short Squeeze"
    elif "crypto" in industry.lower() or "bitcoin" in name or "blockchain" in name:
        catalyst_type = "Crypto Rally"
    elif "software" in industry.lower() or "tech" in sector.lower():
        catalyst_type = "Tech/AI Momentum"

    save_param(conn, symbol, year, "catalyst_type", catalyst_type)
    log.info(f"  {symbol}/{year}: Catalysts done | Type={catalyst_type}")


def analyze_market_context(ticker: yf.Ticker, symbol: str, year: int, conn: sqlite3.Connection):
    """Analyze broader market context during explosion year."""
    # S&P 500 performance that year
    try:
        spy = yf.Ticker("SPY")
        spy_hist = spy.history(start=f"{year}-01-01", end=f"{year}-12-31", auto_adjust=True)
        if not spy_hist.empty:
            spy_return = round(
                (spy_hist["Close"].iloc[-1] - spy_hist["Close"].iloc[0]) / spy_hist["Close"].iloc[0] * 100, 2
            )
            save_param(conn, symbol, year, "spy_return_that_year", spy_return)
    except Exception:
        pass

    # Sector ETF performance (simplified mapping)
    sector_etfs = {
        "Technology": "XLK",
        "Healthcare": "XLV",
        "Consumer Cyclical": "XLY",
        "Consumer Defensive": "XLP",
        "Energy": "XLE",
        "Financial Services": "XLF",
        "Industrials": "XLI",
        "Basic Materials": "XLB",
        "Real Estate": "XLRE",
        "Communication Services": "XLC",
        "Utilities": "XLU",
    }

    info = ticker.info or {}
    sector = info.get("sector", "")
    sector_etf = sector_etfs.get(sector)

    if sector_etf:
        try:
            etf = yf.Ticker(sector_etf)
            etf_hist = etf.history(start=f"{year}-01-01", end=f"{year}-12-31", auto_adjust=True)
            if not etf_hist.empty:
                etf_return = round(
                    (etf_hist["Close"].iloc[-1] - etf_hist["Close"].iloc[0]) / etf_hist["Close"].iloc[0] * 100, 2
                )
                save_param(conn, symbol, year, "sector_etf_return", etf_return)
                save_param(conn, symbol, year, "sector_etf_used", sector_etf)

                # Did the stock massively outperform its sector?
                winner_row = conn.execute(
                    "SELECT ytd_return FROM winners WHERE symbol=? AND year=?",
                    (symbol, year)
                ).fetchone()
                if winner_row:
                    outperformance = round(winner_row[0] - etf_return, 2)
                    save_param(conn, symbol, year, "outperformance_vs_sector", outperformance)
        except Exception:
            pass

    # VIX average during the year (volatility environment)
    try:
        vix = yf.Ticker("^VIX")
        vix_hist = vix.history(start=f"{year}-01-01", end=f"{year}-12-31", auto_adjust=True)
        if not vix_hist.empty:
            avg_vix = round(vix_hist["Close"].mean(), 2)
            save_param(conn, symbol, year, "avg_vix_that_year", avg_vix)
    except Exception:
        pass

    log.info(f"  {symbol}/{year}: Market context done")


def analyze_explosion_timeline(ticker: yf.Ticker, symbol: str, year: int, conn: sqlite3.Connection):
    """Analyze the explosion year itself — when did the big move happen?"""
    hist = ticker.history(start=f"{year}-01-01", end=f"{year}-12-31", auto_adjust=True)
    if hist.empty:
        return

    # Monthly returns during explosion year
    monthly_returns = {}
    for month in range(1, 13):
        month_data = hist[hist.index.month == month]
        if len(month_data) >= 2:
            ret = round(
                (month_data["Close"].iloc[-1] - month_data["Close"].iloc[0]) / month_data["Close"].iloc[0] * 100, 2
            )
            monthly_returns[f"{year}-{month:02d}"] = ret
    save_param(conn, symbol, year, "monthly_returns_explosion_year", monthly_returns)

    # Which month had the biggest gain?
    if monthly_returns:
        best_month = max(monthly_returns, key=monthly_returns.get)
        save_param(conn, symbol, year, "best_month", best_month)
        save_param(conn, symbol, year, "best_month_return_pct", monthly_returns[best_month])

    # When did 50% of total gain happen? (early vs late breakout)
    if len(hist) > 10:
        start_price = hist["Close"].iloc[0]
        end_price = hist["Close"].iloc[-1]
        total_gain = end_price - start_price
        if total_gain > 0:
            halfway = start_price + total_gain * 0.5
            for i, (idx, row) in enumerate(hist.iterrows()):
                if row["Close"] >= halfway:
                    days_to_halfway = i
                    pct_of_year = round(i / len(hist) * 100, 1)
                    save_param(conn, symbol, year, "days_to_50pct_gain", days_to_halfway)
                    save_param(conn, symbol, year, "pct_of_year_to_50pct_gain", pct_of_year)
                    breakout_timing = "Early" if pct_of_year < 40 else "Mid" if pct_of_year < 70 else "Late"
                    save_param(conn, symbol, year, "breakout_timing", breakout_timing)
                    break

    # Max drawdown during explosion year
    running_max = hist["Close"].expanding().max()
    drawdown = ((hist["Close"] - running_max) / running_max * 100)
    max_drawdown = round(drawdown.min(), 2)
    save_param(conn, symbol, year, "max_drawdown_during_explosion_pct", max_drawdown)

    log.info(f"  {symbol}/{year}: Timeline done | Best month={monthly_returns.get(best_month, 'N/A') if monthly_returns else 'N/A'}%")


# ---------------------------------------------------------------------------
# Export to CSV
# ---------------------------------------------------------------------------

def export_csv(conn: sqlite3.Connection):
    """Export all analysis to a CSV file."""
    conn.row_factory = sqlite3.Row
    winners = conn.execute("SELECT * FROM winners ORDER BY year, ytd_return DESC").fetchall()

    # Get all unique params
    params = conn.execute("SELECT DISTINCT param FROM analysis ORDER BY param").fetchall()
    param_names = [p[0] for p in params]

    rows = []
    for w in winners:
        row = {
            "symbol": w["symbol"],
            "year": w["year"],
            "ytd_return": w["ytd_return"],
            "jan_price": w["jan_price"],
            "dec_price": w["dec_price"],
            "market_cap": w["market_cap"],
            "sector": w["sector"],
            "industry": w["industry"],
        }
        # Add all analysis params
        for param in param_names:
            val = conn.execute(
                "SELECT value FROM analysis WHERE symbol=? AND year=? AND param=?",
                (w["symbol"], w["year"], param),
            ).fetchone()
            row[param] = val[0] if val else ""
        rows.append(row)

    if rows:
        fieldnames = list(rows[0].keys())
        with open(CSV_PATH, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
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

    log.info(f"Starting deep analysis for {total} winners...")
    log.info("=" * 60)

    fmp_calls = 0
    MAX_FMP_CALLS = 200  # Stay under 250/day limit

    for i, w in enumerate(winners, 1):
        symbol = w["symbol"]
        year = w["year"]

        if is_analyzed(conn, symbol, year):
            log.info(f"[{i}/{total}] {symbol}/{year} — already analyzed, skipping")
            continue

        log.info(f"[{i}/{total}] Analyzing {symbol} ({year}) — +{w['ytd_return']}%")

        try:
            ticker = yf.Ticker(symbol)

            # 1. Price action (prior year)
            analyze_price_action(ticker, symbol, year, conn)
            time.sleep(REQUEST_DELAY)

            # 2. Fundamentals (from yfinance .info)
            analyze_fundamentals(ticker, symbol, year, conn)
            time.sleep(REQUEST_DELAY)

            # 3. Historical fundamentals from FMP (if budget allows)
            if fmp_calls < MAX_FMP_CALLS:
                analyze_fundamentals_historical(symbol, year, conn)
                fmp_calls += 2  # Uses ~2 FMP calls

            # 4. Catalysts
            analyze_catalysts(ticker, symbol, year, conn)
            time.sleep(REQUEST_DELAY)

            # 5. Market context
            analyze_market_context(ticker, symbol, year, conn)
            time.sleep(REQUEST_DELAY)

            # 6. Explosion timeline
            analyze_explosion_timeline(ticker, symbol, year, conn)

            conn.commit()
            log.info(f"[{i}/{total}] {symbol}/{year} — DONE")
            log.info("-" * 40)

        except Exception as e:
            log.error(f"[{i}/{total}] {symbol}/{year} — FAILED: {e}")
            conn.commit()

        time.sleep(REQUEST_DELAY)

    # Export to CSV
    export_csv(conn)

    # Print summary
    log.info("=" * 60)
    log.info("ANALYSIS COMPLETE")
    log.info("=" * 60)

    # Quick stats
    for w in winners:
        params = conn.execute(
            "SELECT COUNT(*) FROM analysis WHERE symbol=? AND year=?",
            (w["symbol"], w["year"]),
        ).fetchone()[0]
        log.info(f"  {w['symbol']}/{w['year']}: {params} data points collected")

    log.info(f"FMP API calls used: {fmp_calls}")
    log.info(f"CSV exported to: {CSV_PATH}")
    conn.close()


if __name__ == "__main__":
    log.info("Starting ALPHA HUNTER Analyzer")
    run_analysis()
