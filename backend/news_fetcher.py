"""
ALPHA HUNTER — News Fetcher
Fetches recent news for a ticker from Yahoo Finance + Google News RSS.
No API key needed.
"""

import sys
import time
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("news")

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AlphaHunter/3.0"}


def fetch_yahoo_news(ticker: str) -> list[dict]:
    """Fetch news from yfinance .news property."""
    articles = []
    try:
        t = yf.Ticker(ticker)
        news = t.news
        if not news:
            return []

        cutoff = datetime.now() - timedelta(days=30)

        for item in news[:20]:
            pub_time = item.get("providerPublishTime", 0)
            if pub_time:
                dt = datetime.fromtimestamp(pub_time)
                if dt < cutoff:
                    continue
            else:
                dt = datetime.now()

            articles.append({
                "title": item.get("title", ""),
                "source": item.get("publisher", "Yahoo Finance"),
                "date": dt.strftime("%Y-%m-%d"),
                "url": item.get("link", ""),
            })
    except Exception as e:
        log.debug(f"Yahoo news failed for {ticker}: {e}")

    return articles


def fetch_google_news(ticker: str) -> list[dict]:
    """Fetch news from Google News RSS feed."""
    articles = []
    try:
        query = quote(f"{ticker} stock")
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.content)
        channel = root.find("channel")
        if channel is None:
            return []

        cutoff = datetime.now() - timedelta(days=30)

        for item in channel.findall("item")[:15]:
            title = item.find("title")
            pub_date = item.find("pubDate")
            link = item.find("link")
            source = item.find("source")

            title_text = title.text if title is not None else ""
            link_text = link.text if link is not None else ""
            source_text = source.text if source is not None else "Google News"

            # Parse date
            dt = datetime.now()
            if pub_date is not None and pub_date.text:
                try:
                    # RSS date format: "Mon, 06 May 2026 14:30:00 GMT"
                    dt = datetime.strptime(pub_date.text.strip(), "%a, %d %b %Y %H:%M:%S %Z")
                except:
                    try:
                        dt = datetime.strptime(pub_date.text.strip()[:25], "%a, %d %b %Y %H:%M:%S")
                    except:
                        pass

            if dt < cutoff:
                continue

            articles.append({
                "title": title_text,
                "source": source_text,
                "date": dt.strftime("%Y-%m-%d"),
                "url": link_text,
            })
    except Exception as e:
        log.debug(f"Google news failed for {ticker}: {e}")

    return articles


def deduplicate(articles: list[dict]) -> list[dict]:
    """Remove duplicate articles by title similarity."""
    seen = set()
    unique = []
    for a in articles:
        # Simple dedup: lowercase first 50 chars of title
        key = a["title"].lower()[:50]
        if key not in seen and a["title"]:
            seen.add(key)
            unique.append(a)
    return unique


def fetch_news(ticker: str, max_articles: int = 10) -> list[dict]:
    """Main function: fetch from all sources, deduplicate, return top N."""
    log.info(f"Fetching news for {ticker}...")

    yahoo = fetch_yahoo_news(ticker)
    time.sleep(0.5)
    google = fetch_google_news(ticker)

    all_articles = yahoo + google
    all_articles = deduplicate(all_articles)

    # Sort by date (most recent first)
    all_articles.sort(key=lambda x: x["date"], reverse=True)

    result = all_articles[:max_articles]
    log.info(f"  {ticker}: {len(yahoo)} Yahoo + {len(google)} Google -> {len(result)} unique articles")

    return result


if __name__ == "__main__":
    # Test on watchlist
    for ticker in ["MP", "ALGM", "OSCR"]:
        articles = fetch_news(ticker)
        print(f"\n{ticker} — {len(articles)} articles:")
        for a in articles[:5]:
            print(f"  [{a['date']}] {a['title'][:80]}")
            print(f"    Source: {a['source']}")
