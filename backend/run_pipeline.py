"""
ALPHA HUNTER — Pipeline Runner
Single entry point for the entire analysis pipeline.

Usage:
    python run_pipeline.py --task cache
    python run_pipeline.py --task runners
    python run_pipeline.py --task fingerprint
    python run_pipeline.py --task patterns
    python run_pipeline.py --task all
"""

import argparse
import sys
import time
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("pipeline")

# Ensure we can import from this directory
sys.path.insert(0, str(Path(__file__).resolve().parent))


def run_cache():
    log.info("=" * 60)
    log.info("TASK 1: PRICE CACHE")
    log.info("=" * 60)
    from cache.price_cache import run
    run()


def run_runners():
    log.info("=" * 60)
    log.info("TASK 2: RUNNER UNIVERSE")
    log.info("=" * 60)
    from runners import run
    run()


def run_fingerprint():
    log.info("=" * 60)
    log.info("TASK 3: PRE-RUN FINGERPRINT")
    log.info("=" * 60)
    from fingerprint import run
    run()


def run_patterns():
    log.info("=" * 60)
    log.info("TASK 4: PATTERN EXTRACTION")
    log.info("=" * 60)
    from pattern_engine import run
    run()


TASKS = {
    "cache": run_cache,
    "runners": run_runners,
    "fingerprint": run_fingerprint,
    "patterns": run_patterns,
}


def main():
    parser = argparse.ArgumentParser(description="ALPHA HUNTER Pipeline")
    parser.add_argument("--task", choices=list(TASKS.keys()) + ["all"], required=True,
                        help="Which task to run")
    args = parser.parse_args()

    start = time.time()

    if args.task == "all":
        for name, fn in TASKS.items():
            log.info(f"\n{'#' * 60}")
            log.info(f"# Starting: {name}")
            log.info(f"{'#' * 60}\n")
            task_start = time.time()
            fn()
            log.info(f"\n  [{name}] completed in {time.time() - task_start:.1f}s\n")
    else:
        TASKS[args.task]()

    log.info(f"\nPipeline done. Total time: {time.time() - start:.1f}s")


if __name__ == "__main__":
    main()
