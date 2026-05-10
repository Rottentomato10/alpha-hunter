"""
ALPHA HUNTER — Trade Journal
Manages open/closed trades with live price updates.
"""

import json
import uuid
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).resolve().parent / "data"
TRADES_PATH = DATA_DIR / "trades.json"


def load_trades() -> dict:
    if TRADES_PATH.exists():
        with open(TRADES_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"trades": []}


def save_trades(data: dict):
    with open(TRADES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_trade(ticker: str, entry_price: float, shares: int, entry_date: str, note: str = "") -> dict:
    data = load_trades()
    trade = {
        "id": str(uuid.uuid4())[:8],
        "ticker": ticker.upper(),
        "entry_price": entry_price,
        "shares": shares,
        "entry_date": entry_date,
        "note": note,
        "exit_price": None,
        "exit_date": None,
        "status": "open",
    }
    data["trades"].append(trade)
    save_trades(data)
    return trade


def close_trade(trade_id: str, exit_price: float, exit_date: str) -> dict | None:
    data = load_trades()
    for trade in data["trades"]:
        if trade["id"] == trade_id:
            trade["exit_price"] = exit_price
            trade["exit_date"] = exit_date
            trade["status"] = "closed"
            save_trades(data)
            return trade
    return None


def delete_trade(trade_id: str) -> bool:
    data = load_trades()
    before = len(data["trades"])
    data["trades"] = [t for t in data["trades"] if t["id"] != trade_id]
    if len(data["trades"]) < before:
        save_trades(data)
        return True
    return False


def get_live_price(ticker: str) -> float | None:
    """Get live price from cache."""
    import pandas as pd
    price_path = Path(__file__).resolve().parent / "cache" / "prices" / f"{ticker}.parquet"
    if price_path.exists():
        hist = pd.read_parquet(price_path)
        if len(hist) > 0:
            return round(float(hist["Close"].iloc[-1]), 2)
    return None


def enrich_trade(trade: dict) -> dict:
    """Add live calculations to a trade."""
    t = dict(trade)
    current_price = get_live_price(t["ticker"])
    t["current_price"] = current_price

    cost = t["entry_price"] * t["shares"]
    t["total_cost"] = round(cost, 2)

    if current_price and t["status"] == "open":
        current_value = current_price * t["shares"]
        pnl = current_value - cost
        pnl_pct = (current_price - t["entry_price"]) / t["entry_price"] * 100

        t["current_value"] = round(current_value, 2)
        t["pnl_dollar"] = round(pnl, 2)
        t["pnl_pct"] = round(pnl_pct, 2)

        # Days in trade
        try:
            entry = datetime.strptime(t["entry_date"], "%Y-%m-%d")
            t["days_held"] = (datetime.now() - entry).days
        except:
            t["days_held"] = 0

        # Status flags
        t["hit_target"] = pnl_pct >= 500
        t["hit_stop"] = pnl_pct <= -35

    elif t["status"] == "closed" and t["exit_price"]:
        exit_value = t["exit_price"] * t["shares"]
        pnl = exit_value - cost
        pnl_pct = (t["exit_price"] - t["entry_price"]) / t["entry_price"] * 100
        t["current_value"] = round(exit_value, 2)
        t["pnl_dollar"] = round(pnl, 2)
        t["pnl_pct"] = round(pnl_pct, 2)
        try:
            entry = datetime.strptime(t["entry_date"], "%Y-%m-%d")
            exit = datetime.strptime(t["exit_date"], "%Y-%m-%d")
            t["days_held"] = (exit - entry).days
        except:
            t["days_held"] = 0
        t["hit_target"] = pnl_pct >= 500
        t["hit_stop"] = False

    return t
