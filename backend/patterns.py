"""
ALPHA HUNTER — Phase 4: Pattern Engine
Analyzes all winners to find common pre-explosion signals and builds a scoring system.
"""

import sqlite3
import json
import logging
import sys
from pathlib import Path
from collections import Counter

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "db.sqlite"
LOG_PATH = DATA_DIR / "errors.log"
REPORT_PATH = DATA_DIR / "pattern_report.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("patterns")

# ---------------------------------------------------------------------------
# Signal definitions — each signal is a check function returning True/False
# ---------------------------------------------------------------------------

SIGNAL_DEFS = [
    {
        "name": "near_52w_low",
        "description": "Entry price was within 50% of 52-week low",
        "param": "pct_above_52w_low_at_entry",
        "check": lambda v: v != "" and v != "N/A" and float(v) <= 50,
    },
    {
        "name": "was_unprofitable",
        "description": "Company was NOT profitable before explosion",
        "param": "was_profitable",
        "check": lambda v: v == "No",
    },
    {
        "name": "negative_pe",
        "description": "P/E ratio was negative (losing money)",
        "param": "pe_ratio",
        "check": lambda v: v != "N/A" and v != "" and float(v) < 0,
    },
    {
        "name": "volume_spike_3m",
        "description": "Volume in last 3 months was 1.3x+ above 12-month average",
        "param": "volume_ratio_3m_vs_12m",
        "check": lambda v: v != "" and float(v) >= 1.3,
    },
    {
        "name": "low_price_stock",
        "description": "Stock traded below $10 for part of prior year",
        "param": "days_below_10_prior_year",
        "check": lambda v: v != "" and int(float(v)) > 0,
    },
    {
        "name": "very_low_price",
        "description": "Stock traded below $20 for most of prior year",
        "param": "days_below_20_prior_year",
        "check": lambda v: v != "" and int(float(v)) > 100,
    },
    {
        "name": "high_debt",
        "description": "Debt/Equity ratio > 50",
        "param": "debt_to_equity",
        "check": lambda v: v != "N/A" and v != "None" and v != "" and float(v) > 50,
    },
    {
        "name": "high_volatility",
        "description": "Daily volatility > 4% in prior year",
        "param": "prior_year_daily_volatility_pct",
        "check": lambda v: v != "" and float(v) > 4,
    },
    {
        "name": "big_daily_move_before",
        "description": "Had a 10%+ single-day move in 6 months before explosion",
        "param": "max_daily_move_6m_before_pct",
        "check": lambda v: v != "" and float(v) >= 10,
    },
    {
        "name": "prior_year_decline",
        "description": "Stock price declined in the year before explosion",
        "param": "prior_year_price_trend_pct",
        "check": lambda v: v != "" and float(v) < 0,
    },
    {
        "name": "prior_year_rally",
        "description": "Stock was already rising in year before (trend > +30%)",
        "param": "prior_year_price_trend_pct",
        "check": lambda v: v != "" and float(v) > 30,
    },
    {
        "name": "early_breakout",
        "description": "50% of gains happened in first 40% of the year",
        "param": "breakout_timing",
        "check": lambda v: v == "Early",
    },
    {
        "name": "late_breakout",
        "description": "50% of gains happened in last 30% of the year",
        "param": "breakout_timing",
        "check": lambda v: v == "Late",
    },
    {
        "name": "small_cap_entry",
        "description": "Market cap under $2B at start of explosion year",
        "check_winner": lambda w: w["market_cap"] < 2_000_000_000,
    },
    {
        "name": "recently_iped",
        "description": "Company IPO'd less than 2 years before explosion",
        "param": "recently_iped",
        "check": lambda v: v == "Yes",
    },
    {
        "name": "high_short_interest",
        "description": "Short interest > 10% of float",
        "param": "short_pct_of_float",
        "check": lambda v: v != "N/A" and v != "None" and v != "" and float(v) > 0.10,
    },
    {
        "name": "low_institutional",
        "description": "Institutional ownership < 40%",
        "param": "institutional_ownership_pct",
        "check": lambda v: v != "N/A" and v != "None" and v != "" and float(v) < 0.40,
    },
    {
        "name": "severe_drawdown_during",
        "description": "Had > 30% drawdown even during the explosion year",
        "param": "max_drawdown_during_explosion_pct",
        "check": lambda v: v != "" and float(v) < -30,
    },
    {
        "name": "high_vix_year",
        "description": "Average VIX was above 20 during explosion year",
        "param": "avg_vix_that_year",
        "check": lambda v: v != "" and float(v) > 20,
    },
    {
        "name": "outperformed_sector_10x",
        "description": "Outperformed sector ETF by 500%+",
        "param": "outperformance_vs_sector",
        "check": lambda v: v != "" and float(v) > 500,
    },
]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def get_param(conn, symbol, year, param):
    row = conn.execute(
        "SELECT value FROM analysis WHERE symbol=? AND year=? AND param=?",
        (symbol, year, param),
    ).fetchone()
    return row[0] if row else ""


def run_patterns():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    winners = [dict(r) for r in conn.execute(
        "SELECT * FROM winners ORDER BY year, ytd_return DESC"
    ).fetchall()]

    total = len(winners)
    log.info(f"Analyzing patterns across {total} winners...")

    # Check each signal for each winner
    signal_results = {s["name"]: [] for s in SIGNAL_DEFS}

    for w in winners:
        sym, yr = w["symbol"], w["year"]
        for sig in SIGNAL_DEFS:
            triggered = False
            try:
                if "check_winner" in sig:
                    triggered = sig["check_winner"](w)
                elif "param" in sig:
                    val = get_param(conn, sym, yr, sig["param"])
                    if val:
                        triggered = sig["check"](val)
            except (ValueError, TypeError):
                pass

            if triggered:
                signal_results[sig["name"]].append({"symbol": sym, "year": yr})

    # Calculate frequencies
    signals_ranked = []
    for sig in SIGNAL_DEFS:
        name = sig["name"]
        count = len(signal_results[name])
        pct = round(count / total * 100, 1) if total > 0 else 0
        signals_ranked.append({
            "signal": name,
            "description": sig["description"],
            "count": count,
            "total_winners": total,
            "frequency_pct": pct,
            "triggered_by": signal_results[name],
        })

    signals_ranked.sort(key=lambda x: x["frequency_pct"], reverse=True)

    # Top 10
    top_10 = signals_ranked[:10]
    log.info("=" * 60)
    log.info("TOP 10 MOST COMMON PRE-EXPLOSION SIGNALS:")
    log.info("=" * 60)
    for i, s in enumerate(top_10, 1):
        log.info(f"  {i}. {s['signal']}: {s['frequency_pct']}% ({s['count']}/{total})")
        log.info(f"     {s['description']}")
        tickers = [f"{t['symbol']}/{t['year']}" for t in s["triggered_by"]]
        log.info(f"     Stocks: {', '.join(tickers)}")

    # Build scoring weights (normalized to 100)
    max_pct = top_10[0]["frequency_pct"] if top_10 else 1
    scoring_weights = {}
    for s in signals_ranked:
        if s["frequency_pct"] > 0:
            weight = round(s["frequency_pct"] / max_pct * 15, 1)  # Max ~15 points per signal
            scoring_weights[s["signal"]] = weight

    # By year
    by_year = {}
    for w in winners:
        yr = str(w["year"])
        if yr not in by_year:
            by_year[yr] = []
        by_year[yr].append({
            "symbol": w["symbol"],
            "ytd_return": w["ytd_return"],
            "market_cap": w["market_cap"],
            "sector": w.get("sector", ""),
        })

    # By sector
    sector_counts = Counter()
    for w in winners:
        sector = w.get("sector") or get_param(conn, w["symbol"], w["year"], "sector") or "Unknown"
        sector_counts[sector] += 1
    by_sector = dict(sector_counts.most_common())

    # By catalyst type
    catalyst_counts = Counter()
    for w in winners:
        cat = get_param(conn, w["symbol"], w["year"], "catalyst_type") or "Unknown"
        catalyst_counts[cat] += 1

    # Compute average score for all winners
    winner_scores = []
    for w in winners:
        score = compute_score(conn, w["symbol"], w["year"], scoring_weights)
        winner_scores.append({
            "symbol": w["symbol"],
            "year": w["year"],
            "ytd_return": w["ytd_return"],
            "score": score,
        })

    avg_score = round(sum(ws["score"] for ws in winner_scores) / len(winner_scores), 1) if winner_scores else 0

    # Build report
    report = {
        "generated_at": str(__import__("datetime").datetime.now()),
        "total_winners_analyzed": total,
        "average_winner_score": avg_score,
        "by_year": by_year,
        "by_sector": by_sector,
        "by_catalyst": dict(catalyst_counts.most_common()),
        "top_signals": [
            {
                "rank": i + 1,
                "signal": s["signal"],
                "description": s["description"],
                "frequency_pct": s["frequency_pct"],
                "count": s["count"],
                "triggered_by": [f"{t['symbol']}/{t['year']}" for t in s["triggered_by"]],
            }
            for i, s in enumerate(signals_ranked)
            if s["frequency_pct"] > 0
        ],
        "scoring_weights": scoring_weights,
        "winner_scores": winner_scores,
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    log.info(f"\nPattern report saved to {REPORT_PATH}")
    log.info(f"Average winner score: {avg_score}/100")

    conn.close()
    return report


def compute_score(conn, symbol: str, year: int, weights: dict) -> int:
    """Compute a 0-100 score for a given stock based on how many signals it triggers."""
    score = 0
    winner = conn.execute(
        "SELECT * FROM winners WHERE symbol=? AND year=?", (symbol, year)
    ).fetchone()
    if not winner:
        return 0
    winner = dict(winner)

    for sig in SIGNAL_DEFS:
        name = sig["name"]
        if name not in weights:
            continue
        triggered = False
        try:
            if "check_winner" in sig:
                triggered = sig["check_winner"](winner)
            elif "param" in sig:
                val = get_param(conn, symbol, year, sig["param"])
                if val:
                    triggered = sig["check"](val)
        except (ValueError, TypeError):
            pass

        if triggered:
            score += weights[name]

    return min(round(score), 100)


if __name__ == "__main__":
    log.info("Starting ALPHA HUNTER Pattern Engine")
    run_patterns()
