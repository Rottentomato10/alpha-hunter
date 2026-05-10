"""
ALPHA HUNTER — Daily Alert System
Run every morning before market open.
Refreshes cache, scans for signals, tracks watchlist status changes.

Cron: 0 7 * * 1-5 cd /Users/DekelK/Desktop/projects/100/alpha-hunter/backend && python3 daily_alert.py
"""

import sys
import json
import logging
import time
from pathlib import Path
from datetime import datetime, date

import yfinance as yf
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
WATCHLIST_PATH = DATA_DIR / "watchlist.json"
LOGS_DIR = DATA_DIR / "daily_logs"
FUND_PATH = DATA_DIR / "cache" / "fundamentals.json"
CACHE_DIR = BASE_DIR / "cache" / "prices"

LOGS_DIR.mkdir(parents=True, exist_ok=True)

today = date.today().isoformat()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("daily_alert")


def refresh_cache_incremental():
    """Refresh only new data for all tickers (last 5 days)."""
    log.info("Refreshing cache (incremental — last 5 days)...")

    # Load existing fundamentals
    if FUND_PATH.exists():
        with open(FUND_PATH) as f:
            fundamentals = json.load(f)
    else:
        fundamentals = {}

    # Get all tickers
    import sqlite3
    conn = sqlite3.connect(str(DATA_DIR / "db.sqlite"))
    symbols = [r[0] for r in conn.execute("SELECT symbol FROM tickers").fetchall()]
    conn.close()

    updated = 0
    failed = 0

    for i, sym in enumerate(symbols, 1):
        try:
            path = CACHE_DIR / f"{sym}.parquet"
            if not path.exists():
                continue

            # Download only last 10 days and append
            t = yf.Ticker(sym)
            recent = t.history(period="10d", auto_adjust=True)
            if recent.empty:
                continue

            recent.index = recent.index.tz_localize(None)

            # Load existing and merge
            existing = pd.read_parquet(path)
            combined = pd.concat([existing, recent])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined.sort_index(inplace=True)
            combined.to_parquet(path)
            updated += 1

        except Exception:
            failed += 1

        if i % 200 == 0:
            log.info(f"  Cache refresh: {i}/{len(symbols)} (updated: {updated})")
        time.sleep(0.1)

    log.info(f"Cache refreshed: {updated} updated, {failed} failed")
    return updated


def refresh_watchlist_tickers():
    """Full refresh for watchlist tickers + fundamentals."""
    if not WATCHLIST_PATH.exists():
        return

    with open(WATCHLIST_PATH) as f:
        watchlist = json.load(f)

    tickers = [w["ticker"] for w in watchlist["watchlist"]]
    log.info(f"Refreshing watchlist tickers: {tickers}")

    # Load fundamentals
    if FUND_PATH.exists():
        with open(FUND_PATH) as f:
            fundamentals = json.load(f)
    else:
        fundamentals = {}

    for sym in tickers:
        try:
            t = yf.Ticker(sym)
            h = t.history(start="2015-01-01", end="2025-12-31", auto_adjust=True)
            if not h.empty:
                h.index = h.index.tz_localize(None)
                h.to_parquet(CACHE_DIR / f"{sym}.parquet")

            # Update fundamentals
            info = t.info or {}
            fundamentals[sym] = {
                "mcap": info.get("marketCap", 0) or 0,
                "rev_growth": info.get("revenueGrowth"),
                "trailing_eps": info.get("trailingEps"),
                "forward_eps": info.get("forwardEps"),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "short_pct": info.get("shortPercentOfFloat"),
                "profitable": (info.get("trailingEps") or 0) > 0,
                "pe": info.get("trailingPE"),
                "debt_equity": info.get("debtToEquity"),
            }
            log.info(f"  {sym}: price ${h['Close'].iloc[-1]:.2f}, rev {(info.get('revenueGrowth') or 0)*100:.0f}%")
        except Exception as e:
            log.error(f"  {sym}: failed — {e}")
        time.sleep(0.5)

    with open(FUND_PATH, "w") as f:
        json.dump(fundamentals, f)


def check_watchlist_status():
    """Check each watchlist ticker and report status changes."""
    if not WATCHLIST_PATH.exists():
        return []

    with open(WATCHLIST_PATH) as f:
        watchlist = json.load(f)

    if FUND_PATH.exists():
        with open(FUND_PATH) as f:
            fundamentals = json.load(f)
    else:
        fundamentals = {}

    alerts = []

    for item in watchlist["watchlist"]:
        sym = item["ticker"]
        path = CACHE_DIR / f"{sym}.parquet"
        if not path.exists():
            continue

        hist = pd.read_parquet(path)
        if len(hist) < 200:
            continue

        closes = hist["Close"]
        volumes = hist["Volume"]
        ma200 = closes.rolling(200).mean()
        vol_90d = volumes.rolling(90).mean()

        current_price = closes.iloc[-1]
        ma200_val = ma200.iloc[-1]
        vol_today = volumes.iloc[-1]
        vol_avg = vol_90d.iloc[-1]

        if pd.isna(ma200_val) or pd.isna(vol_avg):
            continue

        dist_ma200 = (current_price - ma200_val) / ma200_val * 100
        vol_ratio = vol_today / vol_avg if vol_avg > 0 else 1

        # Determine new status
        prev_status = item["status"]

        if dist_ma200 > 0:
            # Above MA200
            if vol_ratio >= 2.0:
                new_status = "SIGNAL"
            else:
                new_status = "above_ma200"
        elif dist_ma200 > -5:
            new_status = "approaching"
        else:
            new_status = "watch"

        changed = new_status != prev_status

        alert = {
            "ticker": sym,
            "prev_status": prev_status,
            "new_status": new_status,
            "changed": changed,
            "current_price": round(current_price, 2),
            "ma200": round(ma200_val, 2),
            "dist_ma200_pct": round(dist_ma200, 1),
            "vol_ratio_today": round(vol_ratio, 2),
            "rev_growth": item.get("rev_growth"),
            "short_interest": item.get("short_interest"),
        }
        alerts.append(alert)

        if changed:
            log.info(f"  *** STATUS CHANGE: {sym} — {prev_status} -> {new_status}")
            log.info(f"      Price: ${current_price:.2f} | MA200: ${ma200_val:.2f} | Dist: {dist_ma200:+.1f}% | Vol: {vol_ratio:.1f}X")
        else:
            log.info(f"  {sym}: {new_status} (unchanged) | ${current_price:.2f} | MA200 dist: {dist_ma200:+.1f}%")

    return alerts


def run_scanner():
    """Run the production scanner and capture results."""
    from scanner_final import scan_ticker, load_fundamentals as load_fund
    from cache.price_cache import load_all

    histories = load_all(min_rows=250)
    fundamentals = load_fund()

    candidates = []
    for sym, hist in histories.items():
        fund = fundamentals.get(sym, {})
        result = scan_ticker(hist, fund)
        if result:
            result["symbol"] = sym
            candidates.append(result)

    candidates.sort(key=lambda x: x["days_since_cross"])
    return candidates


def run():
    log.info("=" * 70)
    log.info(f"ALPHA HUNTER — Daily Alert ({today})")
    log.info("=" * 70)

    # Step 1: Refresh watchlist tickers (full)
    refresh_watchlist_tickers()

    # Step 2: Check watchlist status
    log.info("\nWATCHLIST STATUS:")
    log.info("-" * 50)
    alerts = check_watchlist_status()

    # Step 3: Run scanner
    log.info("\nSCANNER RESULTS:")
    log.info("-" * 50)
    candidates = run_scanner()

    if candidates:
        log.info(f"  *** {len(candidates)} NEW SIGNAL(S) FOUND! ***")
        for c in candidates:
            log.info(f"  {c['symbol']}: crossed {c['days_since_cross']}d ago, "
                     f"vol {c['cross_vol_ratio']}X, rev +{c['rev_growth_pct']}%, "
                     f"short {c['short_pct']}%")
    else:
        log.info("  No new signals today.")

    # Step 3.5: AI Analysis for watchlist
    log.info("\nAI ANALYSIS:")
    log.info("-" * 50)
    ai_results = []
    try:
        from ai_analyst import analyze_watchlist
        ai_results = analyze_watchlist()
        for r in ai_results:
            log.info(f"  {r['ticker']}: סנטימנט={r['sentiment']} | חדשות={r['news_count']}")
    except Exception as e:
        log.error(f"  AI analysis failed: {e}")

    # Step 4: Save daily log
    daily_log = {
        "date": today,
        "timestamp": datetime.now().isoformat(),
        "watchlist_alerts": alerts,
        "new_signals": candidates,
        "signals_count": len(candidates),
        "status_changes": sum(1 for a in alerts if a["changed"]),
        "ai_analysis": ai_results,
    }

    log_path = LOGS_DIR / f"{today}.json"
    with open(log_path, "w") as f:
        json.dump(daily_log, f, indent=2, default=str)

    log.info(f"\nDaily log saved to {log_path}")

    # Summary
    log.info("\n" + "=" * 70)
    log.info("DAILY SUMMARY")
    log.info("=" * 70)
    log.info(f"  Date: {today}")
    log.info(f"  New signals: {len(candidates)}")
    log.info(f"  Status changes: {sum(1 for a in alerts if a['changed'])}")
    for a in alerts:
        status_icon = "🟢" if a["new_status"] == "SIGNAL" else "🟡" if a["new_status"] == "above_ma200" else "⚪"
        log.info(f"  {status_icon} {a['ticker']}: {a['new_status']} | "
                 f"${a['current_price']} | MA200 {a['dist_ma200_pct']:+.1f}% | "
                 f"Vol {a['vol_ratio_today']:.1f}X")


if __name__ == "__main__":
    run()
