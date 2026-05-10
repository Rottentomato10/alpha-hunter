"""
ALPHA HUNTER — Local Data Cache
Downloads ticker histories + fundamentals once, stores locally as parquet + JSON.
All analysis scripts import from here instead of hitting yfinance directly.

Usage:
    from cache import Cache
    c = Cache()
    c.refresh()                    # Download/update everything (run once)
    histories = c.load_histories() # dict[str, pd.DataFrame] — instant
    fundamentals = c.load_fundamentals()  # dict[str, dict] — instant
    spy, vix = c.load_market()     # SPY + VIX DataFrames — instant
"""

import sqlite3
import json
import time
import logging
import sys
from pathlib import Path
from datetime import datetime, timedelta

import yfinance as yf
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "db.sqlite"
CACHE_DIR = DATA_DIR / "cache"
HIST_DIR = CACHE_DIR / "histories"
FUND_PATH = CACHE_DIR / "fundamentals.json"
MARKET_DIR = CACHE_DIR / "market"
META_PATH = CACHE_DIR / "meta.json"

REQUEST_DELAY = 0.2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("cache")


class Cache:
    def __init__(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        HIST_DIR.mkdir(parents=True, exist_ok=True)
        MARKET_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def _read_meta(self) -> dict:
        if META_PATH.exists():
            with open(META_PATH) as f:
                return json.load(f)
        return {}

    def _write_meta(self, meta: dict):
        with open(META_PATH, "w") as f:
            json.dump(meta, f, indent=2)

    def is_stale(self, max_age_hours: int = 24) -> bool:
        meta = self._read_meta()
        last = meta.get("last_refresh")
        if not last:
            return True
        last_dt = datetime.fromisoformat(last)
        return (datetime.now() - last_dt).total_seconds() > max_age_hours * 3600

    # ------------------------------------------------------------------
    # Symbols
    # ------------------------------------------------------------------

    def get_symbols(self) -> list[str]:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute("SELECT symbol FROM tickers").fetchall()
        conn.close()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # Histories — one parquet per ticker
    # ------------------------------------------------------------------

    def refresh_histories(self, symbols: list[str] = None, start: str = "2016-01-01", end: str = "2025-12-31"):
        """Download and cache price history for all tickers."""
        if symbols is None:
            symbols = self.get_symbols()

        total = len(symbols)
        cached = 0
        downloaded = 0
        failed = 0

        for i, sym in enumerate(symbols, 1):
            path = HIST_DIR / f"{sym}.parquet"

            # Skip if already cached and not stale
            if path.exists() and not self.is_stale(48):
                cached += 1
                continue

            try:
                t = yf.Ticker(sym)
                h = t.history(start=start, end=end, auto_adjust=True)
                if h.empty or len(h) < 50:
                    failed += 1
                    continue
                h.index = h.index.tz_localize(None)
                h.to_parquet(path)
                downloaded += 1
            except Exception as e:
                failed += 1
                log.debug(f"  {sym}: {e}")

            if i % 100 == 0:
                log.info(f"  Histories: {i}/{total} (cached={cached}, new={downloaded}, failed={failed})")
            time.sleep(REQUEST_DELAY)

        log.info(f"Histories done: {total} total, {cached} cached, {downloaded} downloaded, {failed} failed")

    def load_histories(self, min_rows: int = 200) -> dict[str, pd.DataFrame]:
        """Load all cached histories from parquet. Instant."""
        histories = {}
        for path in HIST_DIR.glob("*.parquet"):
            sym = path.stem
            try:
                df = pd.read_parquet(path)
                if len(df) >= min_rows:
                    histories[sym] = df
            except Exception:
                pass
        return histories

    def load_history(self, symbol: str) -> pd.DataFrame | None:
        """Load single ticker history."""
        path = HIST_DIR / f"{symbol}.parquet"
        if path.exists():
            try:
                return pd.read_parquet(path)
            except:
                pass
        return None

    # ------------------------------------------------------------------
    # Fundamentals — single JSON file
    # ------------------------------------------------------------------

    def refresh_fundamentals(self, symbols: list[str] = None):
        """Download and cache fundamental data for all tickers."""
        if symbols is None:
            symbols = self.get_symbols()

        # Load existing
        existing = self.load_fundamentals()
        if existing and not self.is_stale(48):
            log.info(f"Fundamentals already cached ({len(existing)} tickers)")
            return

        total = len(symbols)
        fund = {}

        for i, sym in enumerate(symbols, 1):
            try:
                info = yf.Ticker(sym).info or {}
                fund[sym] = {
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
                    "held_insiders": info.get("heldPercentInsiders"),
                    "held_institutions": info.get("heldPercentInstitutions"),
                }
            except:
                fund[sym] = {"mcap": 0}

            if i % 100 == 0:
                log.info(f"  Fundamentals: {i}/{total}")
            time.sleep(0.1)

        with open(FUND_PATH, "w") as f:
            json.dump(fund, f)

        log.info(f"Fundamentals cached: {len(fund)} tickers")

    def load_fundamentals(self) -> dict[str, dict]:
        """Load cached fundamentals. Instant."""
        if FUND_PATH.exists():
            with open(FUND_PATH) as f:
                return json.load(f)
        return {}

    # ------------------------------------------------------------------
    # Market data — SPY + VIX
    # ------------------------------------------------------------------

    def refresh_market(self):
        """Download and cache SPY + VIX."""
        for sym, fname in [("SPY", "spy.parquet"), ("^VIX", "vix.parquet")]:
            path = MARKET_DIR / fname
            if path.exists() and not self.is_stale(48):
                continue
            try:
                h = yf.Ticker(sym).history(start="2016-01-01", end="2025-12-31", auto_adjust=True)
                if not h.empty:
                    h.index = h.index.tz_localize(None)
                    h.to_parquet(path)
                    log.info(f"  {sym}: {len(h)} rows cached")
            except Exception as e:
                log.error(f"  {sym}: {e}")

    def load_market(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Load SPY + VIX from cache. Instant."""
        spy_path = MARKET_DIR / "spy.parquet"
        vix_path = MARKET_DIR / "vix.parquet"
        spy = pd.read_parquet(spy_path) if spy_path.exists() else pd.DataFrame()
        vix = pd.read_parquet(vix_path) if vix_path.exists() else pd.DataFrame()
        return spy, vix

    # ------------------------------------------------------------------
    # Full refresh
    # ------------------------------------------------------------------

    def refresh(self, force: bool = False):
        """Download everything. Run once, then all scripts use cache."""
        if force:
            # Reset staleness
            self._write_meta({})

        log.info("=" * 60)
        log.info("CACHE REFRESH")
        log.info("=" * 60)

        symbols = self.get_symbols()
        log.info(f"Tickers in DB: {len(symbols)}")

        log.info("\n--- Market Data (SPY + VIX) ---")
        self.refresh_market()

        log.info("\n--- Ticker Histories ---")
        self.refresh_histories(symbols)

        log.info("\n--- Fundamentals ---")
        self.refresh_fundamentals(symbols)

        # Update meta
        self._write_meta({
            "last_refresh": datetime.now().isoformat(),
            "tickers": len(symbols),
            "histories": len(list(HIST_DIR.glob("*.parquet"))),
            "has_fundamentals": FUND_PATH.exists(),
        })

        log.info("\n--- Cache Status ---")
        self.status()

    def status(self):
        """Print cache status."""
        meta = self._read_meta()
        hist_count = len(list(HIST_DIR.glob("*.parquet")))
        fund_count = len(self.load_fundamentals()) if FUND_PATH.exists() else 0
        spy_ok = (MARKET_DIR / "spy.parquet").exists()
        vix_ok = (MARKET_DIR / "vix.parquet").exists()

        log.info(f"  Last refresh: {meta.get('last_refresh', 'never')}")
        log.info(f"  Histories cached: {hist_count}")
        log.info(f"  Fundamentals cached: {fund_count}")
        log.info(f"  SPY cached: {spy_ok}")
        log.info(f"  VIX cached: {vix_ok}")
        log.info(f"  Stale (>48h): {self.is_stale(48)}")

        # Total disk size
        total_bytes = sum(f.stat().st_size for f in CACHE_DIR.rglob("*") if f.is_file())
        log.info(f"  Total cache size: {total_bytes / 1024 / 1024:.1f} MB")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ALPHA HUNTER Cache Manager")
    parser.add_argument("action", choices=["refresh", "status", "force-refresh"],
                        help="refresh=update stale, force-refresh=re-download all, status=show info")
    args = parser.parse_args()

    c = Cache()
    if args.action == "refresh":
        c.refresh()
    elif args.action == "force-refresh":
        c.refresh(force=True)
    elif args.action == "status":
        c.status()
