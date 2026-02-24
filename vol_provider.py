# vol_provider.py
from __future__ import annotations
import os, math, time, random, logging, statistics
from dataclasses import dataclass
import httpx
import numpy as np

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())
FINNHUB_KEY = os.getenv("FINNHUB_API_KEY")
BASE = "https://finnhub.io/api/v1"
HEADERS = {"User-Agent": "vol-brief/1.0 (+python)"}

@dataclass
class TickerRow:
    ticker: str
    close: float
    ret_pct: float
    realized_vol: float
    atr: float
    headline: str


def _get(url: str, params: dict, attempts: int = 4, base_delay: float = 0.5):
    """simple retry wrapper"""
    for i in range(attempts):
        try:
            with httpx.Client(timeout=10, headers=HEADERS) as c:
                r = c.get(url, params=params)
                if r.status_code >= 500 or r.status_code == 429:
                    raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            if i == attempts - 1:
                raise
            time.sleep(base_delay * (2**i) + random.random() * 0.2)


def fetch_volatility_metrics(ticker: str) -> TickerRow:
    if not FINNHUB_KEY:
        raise RuntimeError("Missing FINNHUB_API_KEY in .env")

    # ---- Quote ----
    try:
        q = _get(f"{BASE}/quote", {"symbol": ticker, "token": FINNHUB_KEY})
        last = float(q.get("c") or 0)
        prev = float(q.get("pc") or 0)
        if last <= 0 or prev <= 0:
            raise ValueError("bad quote")
        ret_pct = (last / prev - 1.0) * 100.0
    except Exception as e:
        logging.warning(f"{ticker}: quote fetch failed: {e}")
        return TickerRow(ticker, float("nan"), float("nan"), float("nan"), float("nan"), "Quote unavailable")

    # ---- 30-day realized volatility & ATR(14) ----
    now = int(time.time())
    days_back = 90 * 24 * 3600
    start = now - days_back
    try:
        candles = _get(
            f"{BASE}/stock/candle",
            {"symbol": ticker, "resolution": "D", "from": start, "to": now, "token": FINNHUB_KEY},
        )
        closes = candles.get("c", [])
        highs = candles.get("h", [])
        lows = candles.get("l", [])
    except Exception as e:
        logging.warning(f"{ticker}: candle fetch failed: {e}")
        closes, highs, lows = [], [], []

    realized_vol = float("nan")
    atr = float("nan")
    headline = ""

    if len(closes) > 30:
        rets = np.diff(np.log(closes))
        realized_vol = np.std(rets[-30:]) * math.sqrt(252) * 100  # annualized %
        # ATR(14)
        trs = [highs[i] - lows[i] for i in range(-15, -1)] if len(highs) > 15 else []
        atr = float(np.mean(trs)) if trs else float("nan")

    if realized_vol == realized_vol:  # not NaN
        if realized_vol >= 35 and ret_pct <= -1:
            headline = "Vol spike + drawdown — review risk or hedges."
        elif realized_vol <= 20:
            headline = "Low realized vol — calmer market conditions."
        elif realized_vol >= 30:
            headline = "Elevated vol — expect bigger daily swings."
        else:
            headline = "Vol midrange — normal activity."
    else:
        headline = "Vol data unavailable — insufficient history."

    return TickerRow(ticker, last, ret_pct, realized_vol, atr, headline)
