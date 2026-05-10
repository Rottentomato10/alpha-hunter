"""
ALPHA HUNTER v2 — FastAPI Server
Serves winners_v2, analysis_v2, breakouts, patterns, and live scanner.
"""

import sqlite3
import json
import logging
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "db.sqlite"
REPORT_PATH = DATA_DIR / "pattern_report.json"

app = FastAPI(title="ALPHA HUNTER v2 API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("api")

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    conn = get_db()
    # Try v2 tables first, fall back to v1
    try:
        winners = conn.execute("SELECT COUNT(*) FROM winners_v2").fetchone()[0]
        analysis = conn.execute("SELECT COUNT(*) FROM analysis_v2").fetchone()[0]
        breakouts = conn.execute("SELECT COUNT(*) FROM breakouts WHERE breakout_type='buy_signal'").fetchone()[0]
        version = "v2"
    except:
        winners = conn.execute("SELECT COUNT(*) FROM winners").fetchone()[0]
        analysis = conn.execute("SELECT COUNT(*) FROM analysis").fetchone()[0]
        breakouts = 0
        version = "v1"
    conn.close()
    return {"status": "ok", "version": version, "winners": winners, "analysis_params": analysis, "buy_signals": breakouts}


@app.get("/winners")
def get_winners(sector: str = None, min_return: float = 500):
    conn = get_db()
    try:
        query = "SELECT * FROM winners_v2 WHERE return_pct >= ?"
        params = [min_return]
        if sector:
            query += " AND sector LIKE ?"
            params.append(f"%{sector}%")
        query += " ORDER BY return_pct DESC"
        rows = conn.execute(query, params).fetchall()
        result = [dict(r) for r in rows]
    except:
        query = "SELECT * FROM winners WHERE ytd_return >= ? ORDER BY ytd_return DESC"
        rows = conn.execute(query, [min_return]).fetchall()
        result = [dict(r) for r in rows]
    conn.close()
    return result


@app.get("/winner/{symbol}/{start_date}")
def get_winner_detail(symbol: str, start_date: str):
    conn = get_db()
    symbol = symbol.upper()

    # Try v2
    winner = conn.execute(
        "SELECT * FROM winners_v2 WHERE symbol=? AND start_date=?", (symbol, start_date)
    ).fetchone()

    if not winner:
        # Try v1 (start_date might be a year)
        try:
            year = int(start_date)
            winner = conn.execute("SELECT * FROM winners WHERE symbol=? AND year=?", (symbol, year)).fetchone()
            if winner:
                analysis_rows = conn.execute(
                    "SELECT param, value FROM analysis WHERE symbol=? AND year=? ORDER BY param", (symbol, year)
                ).fetchall()
                analysis = {}
                for row in analysis_rows:
                    try: analysis[row["param"]] = json.loads(row["value"])
                    except: analysis[row["param"]] = row["value"]
                conn.close()
                return {"winner": dict(winner), "analysis": analysis, "breakouts": []}
        except: pass
        conn.close()
        raise HTTPException(status_code=404, detail=f"{symbol}/{start_date} not found")

    # Get analysis_v2
    analysis_rows = conn.execute(
        "SELECT param, value FROM analysis_v2 WHERE symbol=? AND start_date=? ORDER BY param",
        (symbol, start_date)
    ).fetchall()
    analysis = {}
    for row in analysis_rows:
        try: analysis[row["param"]] = json.loads(row["value"])
        except: analysis[row["param"]] = row["value"]

    # Get breakouts
    breakout_rows = conn.execute(
        "SELECT * FROM breakouts WHERE symbol=? AND start_date=? ORDER BY breakout_date",
        (symbol, start_date)
    ).fetchall()
    breakouts = [dict(b) for b in breakout_rows]

    conn.close()
    return {"winner": dict(winner), "analysis": analysis, "breakouts": breakouts}


@app.get("/patterns")
def get_patterns():
    if REPORT_PATH.exists():
        with open(REPORT_PATH) as f:
            return json.load(f)

    # Build patterns from v2 data on the fly
    conn = get_db()
    try:
        winners = [dict(r) for r in conn.execute("SELECT * FROM winners_v2 ORDER BY return_pct DESC").fetchall()]
    except:
        winners = [dict(r) for r in conn.execute("SELECT * FROM winners ORDER BY ytd_return DESC").fetchall()]

    # Group by sector
    by_sector = {}
    for w in winners:
        s = w.get("sector") or "Unknown"
        by_sector[s] = by_sector.get(s, 0) + 1

    # Buy signals stats
    try:
        buy_signals = conn.execute(
            "SELECT b.*, w.return_pct, w.peak_return_pct FROM breakouts b "
            "JOIN winners_v2 w ON b.symbol=w.symbol AND b.start_date=w.start_date "
            "WHERE b.breakout_type='buy_signal' ORDER BY b.breakout_date"
        ).fetchall()
        buy_signal_list = [dict(b) for b in buy_signals]
    except:
        buy_signal_list = []

    # Analysis stats
    signal_stats = {}
    for w in winners:
        sym = w.get("symbol", "")
        sd = w.get("start_date", "")
        if not sd:
            continue
        for param in ["was_profitable", "eps_growth_3q_consecutive", "revenue_accelerating",
                      "gross_margin_expanding", "fcf_turned_positive", "accumulation_signal"]:
            val = conn.execute(
                "SELECT value FROM analysis_v2 WHERE symbol=? AND start_date=? AND param=?",
                (sym, sd, param)
            ).fetchone()
            if val:
                if param not in signal_stats:
                    signal_stats[param] = {"yes": 0, "no": 0, "total": 0}
                signal_stats[param]["total"] += 1
                if val[0] == "Yes":
                    signal_stats[param]["yes"] += 1
                elif val[0] == "No":
                    signal_stats[param]["no"] += 1

    conn.close()

    return {
        "total_winners_analyzed": len(winners),
        "by_sector": by_sector,
        "buy_signals": buy_signal_list,
        "buy_signals_found": len(buy_signal_list),
        "buy_signals_rate": round(len(buy_signal_list) / len(winners) * 100, 1) if winners else 0,
        "fundamental_signals": signal_stats,
    }


@app.get("/breakouts")
def get_breakouts():
    """All buy signals found."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT b.symbol, b.start_date, b.breakout_date, b.breakout_type,
                   b.breakout_price, b.volume_ratio, b.description,
                   w.return_pct, w.peak_return_pct, w.peak_date, w.sector,
                   a.value as return_from_signal
            FROM breakouts b
            JOIN winners_v2 w ON b.symbol=w.symbol AND b.start_date=w.start_date
            LEFT JOIN analysis_v2 a ON b.symbol=a.symbol AND b.start_date=a.start_date
                AND a.param='return_from_buy_signal_to_peak_pct'
            WHERE b.breakout_type='buy_signal'
            ORDER BY b.breakout_date DESC
        """).fetchall()
        result = [dict(r) for r in rows]
    except:
        result = []
    conn.close()
    return result


@app.get("/sectors")
def get_sectors():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT sector, COUNT(*) as count FROM winners_v2 WHERE sector != '' GROUP BY sector ORDER BY count DESC"
        ).fetchall()
    except:
        rows = conn.execute(
            "SELECT sector, COUNT(*) as count FROM winners WHERE sector != '' GROUP BY sector ORDER BY count DESC"
        ).fetchall()
    conn.close()
    return [{"sector": r["sector"], "count": r["count"]} for r in rows]


@app.get("/scan/live")
def scan_live(limit: int = Query(default=20, le=50)):
    """Score current stocks against the pattern model."""
    watchlist = [
        "SMCI", "IONQ", "RGTI", "QBTS", "RKLB", "LUNR", "SOUN", "APLD",
        "MSTR", "MARA", "RIOT", "CLSK", "COIN", "HOOD",
        "PLTR", "APP", "AI", "BBAI", "UPST",
        "CVNA", "HIMS", "CELH", "DUOL", "TOST",
        "NVDA", "AMD", "TSLA", "NIO", "RIVN", "LCID",
        "GME", "AMC", "SOFI", "AFRM", "NU",
        "ENPH", "FSLR", "BE", "PLUG",
        "MRNA", "BNTX",
    ]

    results = []
    for symbol in watchlist[:limit]:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
            hist = ticker.history(period="1y", auto_adjust=True)
            if hist.empty or len(hist) < 50:
                continue

            hist.index = hist.index.tz_localize(None)
            score = 0
            signals = []

            price = hist["Close"].iloc[-1]
            low_52w = hist["Low"].min()
            mcap = info.get("marketCap", 0)
            pe = info.get("trailingPE")
            eps = info.get("trailingEps")
            short_pct = info.get("shortPercentOfFloat")
            vol_avg = hist["Volume"].mean()
            vol_recent = hist["Volume"].tail(20).mean()
            daily_ret = hist["Close"].pct_change().dropna()
            volatility = daily_ret.std() * 100

            # Volume breakout today/recently?
            vol_ratio_20 = vol_recent / vol_avg if vol_avg > 0 else 1
            last_5_vol = hist["Volume"].tail(5)
            had_vol_spike = any(v > vol_avg * 3 for v in last_5_vol)

            # Near 52w high?
            high_52w = hist["High"].max()
            near_high = price >= high_52w * 0.95

            # Signals
            if pe and pe < 0:
                score += 12; signals.append("מכפיל שלילי")
            if eps and eps < 0:
                score += 10; signals.append("לא רווחית")
            if volatility > 4:
                score += 10; signals.append("תנודתיות גבוהה")
            if daily_ret.abs().max() * 100 >= 10:
                score += 12; signals.append("מהלך יומי חד")
            if short_pct and short_pct > 0.10:
                score += 10; signals.append("שורט גבוה")
            if price < 20:
                score += 8; signals.append("מתחת ל-$20")
            if mcap and mcap < 2e9:
                score += 8; signals.append("שווי קטן")
            if had_vol_spike:
                score += 15; signals.append("זינוק נפח אחרון")
            if near_high:
                score += 15; signals.append("קרוב לשיא 52ש")
            if had_vol_spike and near_high:
                score += 10; signals.append("BUY SIGNAL!")

            de = info.get("debtToEquity")
            if de and de > 50:
                score += 5; signals.append("חוב גבוה")

            score = min(score, 100)

            ytd_hist = ticker.history(period="ytd", auto_adjust=True)
            ytd_ret = 0
            if not ytd_hist.empty and len(ytd_hist) > 1:
                ytd_ret = round((ytd_hist["Close"].iloc[-1] - ytd_hist["Close"].iloc[0]) / ytd_hist["Close"].iloc[0] * 100, 2)

            results.append({
                "symbol": symbol, "price": round(price, 2), "market_cap": mcap,
                "pe_ratio": pe, "ytd_return": ytd_ret, "score": score,
                "signals_triggered": signals, "sector": info.get("sector", ""),
                "near_52w_high": near_high, "volume_spike": had_vol_spike,
            })
            time.sleep(0.3)
        except Exception as e:
            log.error(f"Live scan {symbol}: {e}")

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


@app.get("/dashboard")
def dashboard():
    """Main dashboard data: watchlist status, system stats."""
    import pandas as pd
    from datetime import date

    today = date.today().isoformat()

    # Load watchlist
    wl_path = DATA_DIR / "watchlist.json"
    watchlist_items = []
    if wl_path.exists():
        with open(wl_path) as f:
            wl = json.load(f)
        for item in wl.get("watchlist", []):
            sym = item["ticker"]
            # Get live price vs MA200 from cache
            price_path = BASE_DIR / "cache" / "prices" / f"{sym}.parquet"
            if price_path.exists():
                hist = pd.read_parquet(price_path)
                if len(hist) >= 200:
                    current = hist["Close"].iloc[-1]
                    ma200 = hist["Close"].tail(200).mean()
                    dist = (current - ma200) / ma200 * 100
                    item["current_price"] = round(current, 2)
                    item["ma200"] = round(ma200, 2)
                    item["dist_ma200_pct"] = round(dist, 1)
                    if dist > 0:
                        item["live_status"] = "above_ma200"
                    elif dist > -5:
                        item["live_status"] = "approaching"
                    else:
                        item["live_status"] = "watching"

            # EPS acceleration from fundamentals cache
            fund_path = DATA_DIR / "cache" / "fundamentals.json"
            if fund_path.exists():
                with open(fund_path) as ff:
                    all_fund = json.load(ff)
                fund = all_fund.get(sym, {})
                te = fund.get("trailing_eps")
                fe = fund.get("forward_eps")
                item["eps_improving"] = fe is not None and te is not None and fe > te
                item["trailing_eps"] = te
                item["forward_eps"] = fe

            watchlist_items.append(item)

    # Count signals YTD
    logs_dir = DATA_DIR / "daily_logs"
    signals_ytd = 0
    last_signal_date = None
    if logs_dir.exists():
        for f in sorted(logs_dir.glob("*.json")):
            try:
                with open(f) as fh:
                    log_data = json.load(fh)
                if log_data.get("signals_count", 0) > 0:
                    signals_ytd += log_data["signals_count"]
                    last_signal_date = log_data.get("date")
            except:
                pass

    # Runners count
    conn = get_db()
    try:
        total_runners = conn.execute("SELECT COUNT(*) FROM runners").fetchone()[0]
    except:
        total_runners = 0
    conn.close()

    return {
        "today": today,
        "signal_fired": False,
        "watchlist": watchlist_items,
        "last_scan": today + " 07:00",
        "total_runners": total_runners,
        "total_signals_ytd": signals_ytd,
        "last_signal_date": last_signal_date,
    }


@app.get("/runners/full")
def runners_full():
    """Runners joined with deep_analysis and fingerprints."""
    import pandas as pd

    runners_path = DATA_DIR / "runners.csv"
    deep_path = DATA_DIR / "deep_analysis.csv"
    fp_path = DATA_DIR / "fingerprints.csv"

    if not runners_path.exists():
        return []

    runners = pd.read_csv(runners_path)

    if deep_path.exists():
        deep = pd.read_csv(deep_path)
        runners = runners.merge(deep, on=["symbol", "run_start_date"], how="left", suffixes=("", "_da"))

    if fp_path.exists():
        fp = pd.read_csv(fp_path)
        runners = runners.merge(fp, on=["symbol", "run_start_date"], how="left", suffixes=("", "_fp"))

    runners = runners.fillna("")
    return runners.to_dict(orient="records")


@app.get("/daily-logs")
def daily_logs():
    """List all daily log files with summaries."""
    logs_dir = DATA_DIR / "daily_logs"
    if not logs_dir.exists():
        return []

    entries = []
    for f in sorted(logs_dir.glob("*.json"), reverse=True):
        try:
            with open(f) as fh:
                data = json.load(fh)
            entries.append({
                "date": data.get("date", f.stem),
                "signals_count": data.get("signals_count", 0),
                "status_changes": data.get("status_changes", 0),
                "watchlist_alerts": data.get("watchlist_alerts", []),
                "new_signals": data.get("new_signals", []),
            })
        except:
            pass

    return entries


@app.get("/control-group")
def control_group_data():
    """Control group comparison data."""
    path = DATA_DIR / "control_group_report.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


@app.get("/pattern-final")
def pattern_final():
    """Final pattern report."""
    path = DATA_DIR / "pattern_final.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


@app.get("/radar")
def radar():
    """All stocks passing fundamentals — extended watchlist."""
    import pandas as pd
    path = DATA_DIR / "live_scan_v3.csv"
    if not path.exists():
        path = DATA_DIR / "live_candidates.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path)
    df = df.where(pd.notnull(df), None)
    return df.head(100).to_dict(orient="records")


@app.get("/position-sizing")
def position_sizing():
    """Position sizing calculator for watchlist stocks."""
    import pandas as pd

    wl_path = DATA_DIR / "watchlist.json"
    if not wl_path.exists():
        return {"error": "No watchlist"}

    with open(wl_path) as f:
        wl = json.load(f)

    fund_path = DATA_DIR / "cache" / "fundamentals.json"
    fundamentals = {}
    if fund_path.exists():
        with open(fund_path) as f:
            fundamentals = json.load(f)

    # Load AI analysis
    ai_dir = DATA_DIR / "ai_analysis"
    ai_data = {}
    if ai_dir.exists():
        for fp in sorted(ai_dir.glob("*.json"), reverse=True):
            try:
                with open(fp, encoding="utf-8") as fh:
                    d = json.load(fh)
                t = d.get("ticker")
                if t and t not in ai_data:
                    ai_data[t] = d
            except:
                pass

    positions = []
    for item in wl.get("watchlist", []):
        sym = item["ticker"]
        fund = fundamentals.get(sym, {})

        # 1. Distance from MA200 (closer/crossed = higher)
        price_path = Path(__file__).resolve().parent / "cache" / "prices" / f"{sym}.parquet"
        dist_score = 0
        dist_pct = None
        if price_path.exists():
            hist = pd.read_parquet(price_path)
            if len(hist) >= 200:
                current = hist["Close"].iloc[-1]
                ma200 = hist["Close"].tail(200).mean()
                dist_pct = round((current - ma200) / ma200 * 100, 1)
                if dist_pct >= 0:
                    dist_score = 20  # Already crossed
                elif dist_pct > -5:
                    dist_score = 15  # Very close
                elif dist_pct > -10:
                    dist_score = 10  # Approaching
                else:
                    dist_score = 5   # Far

        # 2. Revenue growth
        rev = fund.get("rev_growth")
        rev_pct = round(rev * 100, 1) if rev else 0
        if rev_pct >= 100:
            rev_score = 20
        elif rev_pct >= 50:
            rev_score = 16
        elif rev_pct >= 25:
            rev_score = 12
        elif rev_pct >= 15:
            rev_score = 10
        else:
            rev_score = 5

        # 3. Short interest
        short = fund.get("short_pct")
        short_pct = round(short * 100, 1) if short else 0
        if short_pct >= 15:
            short_score = 20
        elif short_pct >= 12:
            short_score = 16
        elif short_pct >= 10:
            short_score = 14
        elif short_pct >= 7:
            short_score = 10
        else:
            short_score = 5

        # 4. AI sentiment
        ai = ai_data.get(sym, {})
        sentiment = ai.get("sentiment", "ניטרלי")
        if sentiment == "חיובי מאוד":
            ai_score = 20
        elif sentiment == "חיובי":
            ai_score = 15
        elif sentiment == "ניטרלי":
            ai_score = 8
        else:
            ai_score = 0

        # 5. EPS trajectory
        te = fund.get("trailing_eps")
        fe = fund.get("forward_eps")
        if te is not None and fe is not None:
            if fe > 0 and te < 0:
                eps_score = 20  # Flipping to profit
            elif fe > te:
                improvement = abs(fe - te)
                eps_score = 16 if improvement > 1 else 12
            else:
                eps_score = 5
        else:
            eps_score = 8

        total = dist_score + rev_score + short_score + ai_score + eps_score

        positions.append({
            "ticker": sym,
            "total_score": total,
            "breakdown": {
                "ma200_proximity": dist_score,
                "revenue_growth": rev_score,
                "short_interest": short_score,
                "ai_sentiment": ai_score,
                "eps_trajectory": eps_score,
            },
            "details": {
                "dist_ma200_pct": dist_pct,
                "rev_growth_pct": rev_pct,
                "short_pct": short_pct,
                "sentiment": sentiment,
                "trailing_eps": te,
                "forward_eps": fe,
            },
        })

    # Convert scores to allocation %
    total_score_sum = sum(p["total_score"] for p in positions)
    cash_pct = 30  # Always keep 30% cash minimum
    equity_pct = 70  # Allocate 70% across positions

    for p in positions:
        raw_alloc = (p["total_score"] / total_score_sum * equity_pct) if total_score_sum > 0 else 0
        # Cap at 25% per position (quarter Kelly)
        p["allocation_pct"] = round(min(raw_alloc, 25), 1)

    # Adjust if total exceeds equity_pct
    total_alloc = sum(p["allocation_pct"] for p in positions)
    actual_cash = round(100 - total_alloc, 1)

    positions.sort(key=lambda x: x["total_score"], reverse=True)

    return {
        "positions": positions,
        "cash_pct": actual_cash,
        "equity_pct": round(total_alloc, 1),
        "strategy_params": {
            "target_return_pct": 500,
            "expected_hold_months": "8-12",
            "stop_loss_pct": -35,
            "max_single_position_pct": 25,
            "min_cash_pct": 30,
        },
    }


@app.get("/ai-analysis/{ticker}")
def get_ai_analysis(ticker: str):
    """Get latest AI analysis for a ticker."""
    ticker = ticker.upper()
    ai_dir = DATA_DIR / "ai_analysis"
    if not ai_dir.exists():
        return {"error": "No AI analysis available"}

    # Find latest analysis file for this ticker
    files = sorted(ai_dir.glob(f"{ticker}_*.json"), reverse=True)
    if not files:
        return {"error": f"No analysis for {ticker}"}

    with open(files[0], encoding="utf-8") as f:
        return json.load(f)


@app.get("/ai-analysis-all")
def get_all_ai_analysis():
    """Get latest AI analysis for all watchlist stocks."""
    ai_dir = DATA_DIR / "ai_analysis"
    if not ai_dir.exists():
        return {}

    results = {}
    # Group by ticker, take latest
    for f in sorted(ai_dir.glob("*.json"), reverse=True):
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            ticker = data.get("ticker")
            if ticker and ticker not in results:
                results[ticker] = data
        except:
            pass
    return results


@app.post("/ai-analysis/{ticker}")
def run_ai_analysis(ticker: str):
    """Run fresh AI analysis for a ticker."""
    ticker = ticker.upper()
    try:
        from ai_analyst import analyze_ticker
        result = analyze_ticker(ticker)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Trade Journal Endpoints ---

@app.get("/trades")
def get_trades():
    from trades import load_trades, enrich_trade
    data = load_trades()
    return [enrich_trade(t) for t in data["trades"]]


@app.post("/trades")
def create_trade(body: dict):
    from trades import add_trade
    t = add_trade(
        ticker=body["ticker"],
        entry_price=body["entry_price"],
        shares=body["shares"],
        entry_date=body["entry_date"],
        note=body.get("note", ""),
    )
    return t


@app.put("/trades/{trade_id}")
def update_trade(trade_id: str, body: dict):
    from trades import close_trade
    t = close_trade(trade_id, body["exit_price"], body["exit_date"])
    if not t:
        raise HTTPException(404, "Trade not found")
    return t


@app.delete("/trades/{trade_id}")
def remove_trade(trade_id: str):
    from trades import delete_trade
    if not delete_trade(trade_id):
        raise HTTPException(404, "Trade not found")
    return {"ok": True}


@app.get("/trades/summary")
def trades_summary():
    from trades import load_trades, enrich_trade
    data = load_trades()
    enriched = [enrich_trade(t) for t in data["trades"]]

    open_trades = [t for t in enriched if t["status"] == "open"]
    closed_trades = [t for t in enriched if t["status"] == "closed"]

    total_invested = sum(t.get("total_cost", 0) for t in open_trades)
    current_value = sum(t.get("current_value", 0) for t in open_trades)
    total_pnl = sum(t.get("pnl_dollar", 0) for t in open_trades)
    total_pnl_pct = ((current_value - total_invested) / total_invested * 100) if total_invested > 0 else 0

    closed_pnl = [t.get("pnl_pct", 0) for t in closed_trades if t.get("pnl_pct") is not None]
    win_rate = sum(1 for p in closed_pnl if p > 0) / len(closed_pnl) * 100 if closed_pnl else 0

    return {
        "open_count": len(open_trades),
        "closed_count": len(closed_trades),
        "total_invested": round(total_invested, 2),
        "current_value": round(current_value, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "closed_win_rate": round(win_rate, 1),
        "closed_avg_return": round(sum(closed_pnl) / len(closed_pnl), 1) if closed_pnl else 0,
        "best_trade": max(closed_pnl) if closed_pnl else 0,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
