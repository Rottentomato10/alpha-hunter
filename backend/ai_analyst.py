"""
ALPHA HUNTER — AI Analyst
Uses Claude API to analyze news + volume data and assess if
accumulation is backed by real catalyst.
"""

import os
import sys
import json
import logging
from datetime import datetime, date
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from news_fetcher import fetch_news

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
AI_DIR = DATA_DIR / "ai_analysis"
FUND_PATH = DATA_DIR / "cache" / "fundamentals.json"
CACHE_DIR = BASE_DIR / "cache" / "prices"

AI_DIR.mkdir(parents=True, exist_ok=True)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ai_analyst")


def get_ticker_context(ticker: str) -> dict:
    """Get fundamental and price context for a ticker."""
    import pandas as pd

    context = {"ticker": ticker}

    # Fundamentals
    if FUND_PATH.exists():
        with open(FUND_PATH) as f:
            fund = json.load(f)
        f_data = fund.get(ticker, {})
        context["sector"] = f_data.get("sector", "Unknown")
        context["rev_growth"] = round((f_data.get("rev_growth") or 0) * 100, 1)
        context["short_pct"] = round((f_data.get("short_pct") or 0) * 100, 1)
        context["mcap"] = f_data.get("mcap", 0)
        context["trailing_eps"] = f_data.get("trailing_eps")
        context["forward_eps"] = f_data.get("forward_eps")
    else:
        context["sector"] = "Unknown"

    # Price data from cache
    price_path = CACHE_DIR / f"{ticker}.parquet"
    if price_path.exists():
        hist = pd.read_parquet(price_path)
        if len(hist) >= 90:
            last_30 = hist.tail(30)
            last_90 = hist.tail(90)
            vol_90_avg = last_90["Volume"].mean()
            vol_30_avg = last_30["Volume"].mean()
            price_30d_ret = (last_30["Close"].iloc[-1] - last_30["Close"].iloc[0]) / last_30["Close"].iloc[0] * 100
            max_vol_spike = last_30["Volume"].max() / vol_90_avg if vol_90_avg > 0 else 1

            context["vol_spike_max"] = round(max_vol_spike, 1)
            context["price_30d_change"] = round(price_30d_ret, 1)
            context["current_price"] = round(hist["Close"].iloc[-1], 2)

    return context


def build_prompt(ticker: str, news: list[dict], context: dict) -> str:
    """Build the analysis prompt."""
    headlines = "\n".join([f"- [{a['date']}] {a['title']} ({a['source']})" for a in news[:10]])

    if not headlines:
        headlines = "לא נמצאו חדשות אחרונות עבור מניה זו."

    prompt = f"""מניה: {ticker}
סקטור: {context.get('sector', 'Unknown')}
נתונים:
- ווליום גבוה ב-{context.get('vol_spike_max', '?')}X מהממוצע ב-30 יום אחרונים
- מחיר {'ירד' if context.get('price_30d_change', 0) < 0 else 'עלה'} {abs(context.get('price_30d_change', 0))}% ב-30 יום
- שורט {context.get('short_pct', '?')}%
- צמיחת הכנסות +{context.get('rev_growth', '?')}%
- EPS: trailing={context.get('trailing_eps', '?')}, forward={context.get('forward_eps', '?')}
- שווי שוק: ${context.get('mcap', 0)/1e9:.1f}B

כותרות חדשות אחרונות:
{headlines}

ענה על 3 שאלות:
1. האם יש קטליסט ברור שמסביר את הצבירה (ווליום + ירידת מחיר)?
2. האם החדשות חיוביות, שליליות או מעורבות?
3. האם זה נראה כמו הזדמנות אמיתית או מלכודת?

תן ציון סנטימנט: חיובי מאוד / חיובי / ניטרלי / שלילי

ענה בקצרה ובעברית. מקסימום 5 משפטים."""

    return prompt


def analyze_with_claude(ticker: str, news: list[dict], context: dict) -> dict:
    """Call Claude API for analysis."""
    if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY == "your_anthropic_key_here":
        log.warning("No ANTHROPIC_API_KEY set. Using mock analysis.")
        return mock_analysis(ticker, news, context)

    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = build_prompt(ticker, news, context)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system="You are a stock analyst. Analyze if news headlines explain unusual volume accumulation. Be concise and direct. Respond in Hebrew.",
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = message.content[0].text

        # Parse sentiment from response
        sentiment = "ניטרלי"
        for s in ["חיובי מאוד", "חיובי", "שלילי", "ניטרלי"]:
            if s in response_text:
                sentiment = s
                break

        return {
            "ticker": ticker,
            "date": date.today().isoformat(),
            "analysis": response_text,
            "sentiment": sentiment,
            "news_count": len(news),
            "headlines": [a["title"] for a in news[:5]],
        }
    except Exception as e:
        log.error(f"Claude API error for {ticker}: {e}")
        return mock_analysis(ticker, news, context)


def mock_analysis(ticker: str, news: list[dict], context: dict) -> dict:
    """Fallback analysis when API key not available."""
    rev = context.get("rev_growth", 0)
    short = context.get("short_pct", 0)
    price_change = context.get("price_30d_change", 0)
    vol_spike = context.get("vol_spike_max", 1)

    # Simple rule-based analysis
    if rev > 20 and short > 10:
        sentiment = "חיובי"
        analysis = (
            f"1. צמיחת הכנסות של +{rev}% עם שורט גבוה ({short}%) יוצרים פוטנציאל לסקוויז. "
            f"הווליום הגבוה ({vol_spike}X) מרמז על צבירה מוסדית.\n"
            f"2. {'החדשות מעורבות — נדרש ניתוח נוסף.' if news else 'אין מספיק חדשות לניתוח.'}\n"
            f"3. שילוב של צמיחה + שורט + ווליום — נראה כמו הזדמנות אמיתית, אבל נדרשת סבלנות.\n"
            f"סנטימנט: חיובי"
        )
    elif rev > 0 and price_change < -10:
        sentiment = "ניטרלי"
        analysis = (
            f"1. הירידה של {abs(price_change):.0f}% לא בהכרח שלילית — יכולה להיות קפיטולציה לפני מהפך.\n"
            f"2. {'החדשות מעורבות.' if news else 'חסר מידע מחדשות.'}\n"
            f"3. צריך לחכות לאישור — חצייה מעל MA200 עם ווליום תהיה הסימן.\n"
            f"סנטימנט: ניטרלי"
        )
    else:
        sentiment = "ניטרלי"
        analysis = (
            f"1. אין קטליסט ברור מהחדשות.\n"
            f"2. ניטרלי.\n"
            f"3. לא מספיק מידע להחלטה — המשך מעקב.\n"
            f"סנטימנט: ניטרלי"
        )

    return {
        "ticker": ticker,
        "date": date.today().isoformat(),
        "analysis": analysis,
        "sentiment": sentiment,
        "news_count": len(news),
        "headlines": [a["title"] for a in news[:5]],
        "mock": True,
    }


def analyze_ticker(ticker: str) -> dict:
    """Full pipeline: fetch news + context + AI analysis."""
    log.info(f"\nAnalyzing {ticker}...")

    # Fetch news
    news = fetch_news(ticker)

    # Get context
    context = get_ticker_context(ticker)

    # Run AI analysis
    result = analyze_with_claude(ticker, news, context)

    # Save
    save_path = AI_DIR / f"{ticker}_{date.today().isoformat()}.json"
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info(f"  Saved to {save_path}")

    return result


def analyze_watchlist() -> list[dict]:
    """Analyze all stocks in watchlist.json."""
    wl_path = DATA_DIR / "watchlist.json"
    if not wl_path.exists():
        log.error("No watchlist.json found")
        return []

    with open(wl_path) as f:
        wl = json.load(f)

    results = []
    for item in wl.get("watchlist", []):
        ticker = item["ticker"]
        result = analyze_ticker(ticker)
        results.append(result)

    return results


def run():
    log.info("ALPHA HUNTER — AI Analyst")
    log.info("=" * 60)

    results = analyze_watchlist()

    # Print results
    for r in results:
        print(f"\n{'='*60}")
        print(f"  {r['ticker']} — סנטימנט: {r['sentiment']}")
        print(f"  תאריך: {r['date']} | חדשות: {r['news_count']}")
        print(f"{'='*60}")
        print(f"  {r['analysis']}")
        if r.get("headlines"):
            print(f"\n  כותרות:")
            for h in r["headlines"]:
                print(f"    • {h}")
        print()


if __name__ == "__main__":
    run()
