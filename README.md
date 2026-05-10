# ALPHA HUNTER

Quantitative stock screening system that identifies potential 500%+ runners before they happen. Built through reverse-engineering 153 historical runners (2019–2024) and backtesting entry signals.

## The Strategy

**Entry Signal — buy when ALL true simultaneously:**

| Condition | Threshold | Rationale |
|-----------|-----------|-----------|
| MA200 cross | Price crossed above from below, last 30 days | Recovery confirmed — stock in turnaround |
| Volume on cross day | > 2X the 90-day average | Institutional interest confirmed |
| Revenue growth | > 15% YoY | Real business momentum, not just price action |
| Short interest | > 7% of float | Squeeze fuel + contrarian indicator |
| Market cap | $500M – $15B | Midcap sweet spot — big enough to survive, small enough to move |
| Profitability | Not yet profitable (EPS ≤ 0) | Growth/turnaround stage — max upside |

**Expected Performance (backtested 2019–2024):**

| Metric | Value |
|--------|-------|
| Signal frequency | ~11 per year |
| Precision | 15% (1 in 7 becomes a 500%+ runner) |
| Median return if correct | +428% |
| Median return if "wrong" | +49% |
| % of signals that are positive | 97% |
| Move missed by waiting for MA200 | Only 5% |
| Hold period | 9–12 months |
| Max drawdown during run | -41% median (expect volatility) |

## How It Works

1. **Cache** — downloads full OHLCV history for 1,750 tickers (NYSE + NASDAQ)
2. **Scanner** — checks all tickers daily against entry criteria
3. **Watchlist** — tracks stocks approaching entry threshold
4. **Alert** — notifies on status changes and new signals

## Setup

```bash
cd backend
pip install -r requirements.txt

# First run: build cache (~2 min for prices, ~15 min for fundamentals)
python3 run_pipeline.py --task cache

# Discover runners and patterns (optional — research mode)
python3 run_pipeline.py --task all

# Production scanner — run daily
python3 scanner_final.py

# Daily alert with watchlist tracking
python3 daily_alert.py
```

## Automated Daily Alerts

```bash
# Add to crontab (Mon-Fri at 7:00 AM):
crontab -e

# Add this line:
0 7 * * 1-5 cd /Users/DekelK/Desktop/projects/100/alpha-hunter/backend && /opt/homebrew/bin/python3 daily_alert.py >> data/daily_logs/cron.log 2>&1
```

## Current Watchlist (2026-05-10)

| Ticker | Status | Distance to MA200 | Rev Growth | Short% | MCap |
|--------|--------|-------------------|-----------|--------|------|
| **MP** | Above MA200 (+1.8%) | Crossed, vol unconfirmed | +119% | 17% | $12B |
| **ALGM** | Approaching (-4.9%) | Close to cross | +26% | 13% | $9.1B |
| **OSCR** | Watching (-9.8%) | Further out | +53% | 11% | $5.7B |

## Architecture

```
alpha-hunter/
├── backend/
│   ├── cache/price_cache.py    — OHLCV data for 1,750 tickers
│   ├── runners.py              — Finds all 500%+ historical runners
│   ├── fingerprint.py          — Pre-run condition analysis (90d before)
│   ├── pattern_engine.py       — Statistical pattern extraction
│   ├── deep_analysis.py        — Full timeline for each runner
│   ├── scanner_final.py        — PRODUCTION: daily candidate scanner
│   ├── daily_alert.py          — Automated alert system + watchlist
│   ├── control_group.py        — False positive analysis
│   ├── recovery_entry.py       — MA200 entry strategy validation
│   ├── expand_universe.py      — Ticker universe expansion
│   ├── run_pipeline.py         — Pipeline orchestrator
│   ├── api.py                  — FastAPI server (port 8000)
│   └── data/
│       ├── watchlist.json      — Active watchlist
│       ├── daily_logs/         — Historical scan logs
│       ├── runners.csv         — 159 historical runners
│       ├── fingerprints.csv    — Pre-run conditions
│       └── db.sqlite           — All data
├── frontend/                   — React + Vite + Tailwind (Hebrew RTL)
└── README.md
```

## Key Research Findings

From analyzing 153 stocks that ran 500%+ in 12 months:

1. **Volume precedes the move by 108 days** — accumulation is visible months early
2. **71% started with a "quiet launch"** — no dramatic breakout on day 1
3. **76% had 2X+ volume spikes** in the 30 days before running
4. **64% were BELOW MA200** during accumulation (smart money buys while others panic)
5. **74% experienced -30%+ drawdowns** during the run — holding is hard
6. **Median 8-day entry window** — you have about a week to spot and enter
7. **Revenue growth** is the strongest fundamental separator (winners: +12% vs false: +8%)
8. **Short interest** differentiates (winners: 15% vs false: 5%)

## Frontend

```bash
cd frontend && npm install && npm run dev
# Open http://localhost:5174
```

Hebrew RTL dark interface showing winners, patterns, breakout timelines, and live scanner.
