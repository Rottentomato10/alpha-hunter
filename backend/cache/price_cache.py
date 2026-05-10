"""
ALPHA HUNTER — Price Cache (Task 1)
Downloads full OHLCV history for all tickers, stores as parquet.
All downstream scripts read from cache — never re-download.
"""

import sqlite3
import time
import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta

import yfinance as yf
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "db.sqlite"
CACHE_DIR = Path(__file__).resolve().parent / "prices"
CHECKPOINT_PATH = Path(__file__).resolve().parent / "cache_checkpoint.txt"

CACHE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("price_cache")

BATCH_SIZE = 50
MAX_AGE_DAYS = 7
START_DATE = "2015-01-01"
END_DATE = "2025-12-31"


def get_symbols() -> list[str]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("SELECT symbol FROM tickers ORDER BY symbol").fetchall()
    conn.close()
    return [r[0] for r in rows]


def is_fresh(symbol: str) -> bool:
    path = CACHE_DIR / f"{symbol}.parquet"
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return (datetime.now() - mtime).days < MAX_AGE_DAYS


def load_checkpoint() -> int:
    if CHECKPOINT_PATH.exists():
        return int(CHECKPOINT_PATH.read_text().strip())
    return 0


def save_checkpoint(n: int):
    CHECKPOINT_PATH.write_text(str(n))


def download_batch(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Download a batch using yf.download for speed."""
    results = {}
    try:
        # yf.download can fetch multiple tickers at once
        data = yf.download(
            symbols,
            start=START_DATE,
            end=END_DATE,
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )

        if len(symbols) == 1:
            # Single ticker: data is a simple DataFrame
            sym = symbols[0]
            if not data.empty and len(data) > 50:
                data.index = data.index.tz_localize(None)
                results[sym] = data
        else:
            # Multiple tickers: multi-level columns
            for sym in symbols:
                try:
                    if sym in data.columns.get_level_values(0):
                        df = data[sym].dropna(how="all")
                        if not df.empty and len(df) > 50:
                            df.index = df.index.tz_localize(None)
                            results[sym] = df
                except Exception:
                    pass
    except Exception as e:
        log.warning(f"Batch download failed: {e}. Falling back to individual.")
        for sym in symbols:
            try:
                t = yf.Ticker(sym)
                h = t.history(start=START_DATE, end=END_DATE, auto_adjust=True)
                if not h.empty and len(h) > 50:
                    h.index = h.index.tz_localize(None)
                    results[sym] = h
            except:
                pass
            time.sleep(0.2)

    return results


def run():
    symbols = get_symbols()
    total = len(symbols)
    log.info(f"Price Cache: {total} tickers, batch size {BATCH_SIZE}")
    log.info(f"Cache dir: {CACHE_DIR}")

    # Filter out already-cached
    to_download = [s for s in symbols if not is_fresh(s)]
    cached = total - len(to_download)

    if not to_download:
        log.info(f"All {total} tickers already cached and fresh (<{MAX_AGE_DAYS} days). Done.")
        return

    log.info(f"Already cached: {cached} | Need download: {len(to_download)}")

    # Resume from checkpoint
    checkpoint = load_checkpoint()
    if checkpoint > 0 and checkpoint < len(to_download):
        log.info(f"Resuming from checkpoint: {checkpoint}")
        to_download = to_download[checkpoint:]

    downloaded = 0
    failed = 0
    start_time = time.time()

    for batch_start in range(0, len(to_download), BATCH_SIZE):
        batch = to_download[batch_start:batch_start + BATCH_SIZE]

        results = download_batch(batch)

        for sym, df in results.items():
            path = CACHE_DIR / f"{sym}.parquet"
            df.to_parquet(path)
            downloaded += 1

        failed += len(batch) - len(results)

        # Progress
        done = batch_start + len(batch)
        elapsed = time.time() - start_time
        rate = done / elapsed if elapsed > 0 else 0
        eta = (len(to_download) - done) / rate if rate > 0 else 0

        log.info(
            f"[{done}/{len(to_download)}] "
            f"Downloaded: {downloaded} | Failed: {failed} | "
            f"Rate: {rate:.1f} tickers/s | ETA: {eta:.0f}s"
        )

        # Checkpoint
        save_checkpoint(checkpoint + done)
        time.sleep(0.5)  # Small pause between batches

    # Clear checkpoint on completion
    if CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()

    elapsed = time.time() - start_time
    log.info(f"Cache complete: {downloaded} downloaded, {failed} failed in {elapsed:.0f}s")
    log.info(f"Total cached: {len(list(CACHE_DIR.glob('*.parquet')))} parquet files")


def load_all(min_rows: int = 200) -> dict[str, pd.DataFrame]:
    """Load all cached histories. Used by downstream scripts."""
    histories = {}
    for path in CACHE_DIR.glob("*.parquet"):
        try:
            df = pd.read_parquet(path)
            if len(df) >= min_rows:
                histories[path.stem] = df
        except:
            pass
    return histories


def load_one(symbol: str) -> pd.DataFrame | None:
    path = CACHE_DIR / f"{symbol}.parquet"
    if path.exists():
        try:
            return pd.read_parquet(path)
        except:
            pass
    return None


if __name__ == "__main__":
    run()
