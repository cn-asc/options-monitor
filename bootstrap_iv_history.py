"""
bootstrap_iv_history_massive.py
---------------------------------
Bootstraps iv_history.json with ~30 days of backfilled ATM IV data per ticker
using the free Massive (Polygon) API.

Run:
  python bootstrap_iv_history_massive.py
"""

import os
import time
import json
import datetime as dt
import math
import requests
from dotenv import load_dotenv
from options_dashboard import implied_vol, save_iv_history
import random

# --- Config ------------------------------------------------------------
load_dotenv()
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "").strip()
TICKERS = [t.strip().upper() for t in os.getenv("TICKERS", "AAPL,MSFT,GOOG,META").split(",") if t.strip()]
RISK_FREE = float(os.getenv("RISK_FREE_RATE", "0.0393"))
DAYS_BACK = int(os.getenv("BOOTSTRAP_DAYS", "30"))
RATE_PAUSE = 15  # seconds (5 calls/min free tier)
BASE_URL = "https://api.massive.com"  # Polygon rebrand

if not POLYGON_API_KEY:
    raise SystemExit("❌ POLYGON_API_KEY missing in .env")

print(f"Bootstrapping {len(TICKERS)} tickers with {DAYS_BACK} days of IV history...\n")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "IVBootstrap/2.0"})

def sleep(multiplier: float = 1.0):
    """Sleep to stay under Massive's 5 requests/min free limit."""
    time.sleep(RATE_PAUSE * multiplier)

# --- Helpers ------------------------------------------------------------

def get_json(url: str, params: dict, retries: int = 3):
    """GET helper with 429/backoff handling."""
    params["apiKey"] = POLYGON_API_KEY
    for attempt in range(retries):
        resp = SESSION.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            wait = RATE_PAUSE * (attempt + 1) * random.uniform(1.0, 1.5)
            print(f"⏳ Rate-limited: sleeping {wait:.1f}s...")
            time.sleep(wait)
            continue
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:100]}")
    raise RuntimeError("Max retries exceeded for " + url)

def get_stock_close_on_date(ticker: str, date: dt.date):
    url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{date}/{date}"
    data = get_json(url, {})
    results = data.get("results")
    if not results:
        return None
    return float(results[0]["c"])

_contract_cache = {}

def find_atm_option_contract(ticker: str, date: dt.date, spot: float):
    """Find option near 30D expiry and ATM strike."""
    url = f"{BASE_URL}/v3/reference/options/contracts"
    data = get_json(
        url,
        {
            "underlying_ticker": ticker,
            "as_of": str(date),
            "limit": 1000,
        },
    )

    contracts = data.get("results", [])
    if not contracts:
        return None

    target_exp = date + dt.timedelta(days=30)
    best, best_score = None, float("inf")

    for c in contracts:
        try:
            exp = dt.date.fromisoformat(c["expiration_date"])
            strike = float(c["strike_price"])
            opt_type = c.get("contract_type", "call").lower()
            symbol = c["ticker"]
        except Exception:
            continue

        dte = (exp - date).days
        if dte <= 0 or dte > 60:
            continue

        score = abs(strike - spot) + 0.4 * abs(dte - 30)
        if opt_type == "put":
            score += 0.2
        if score < best_score:
            best_score, best = score, c

    return best

def get_option_close(option_symbol: str, date: dt.date):
    url = f"{BASE_URL}/v2/aggs/ticker/{option_symbol}/range/1/day/{date}/{date}"
    data = get_json(url, {})
    results = data.get("results")
    if not results:
        return None
    return float(results[0]["c"])

# --- Bootstrap ----------------------------------------------------------

def bootstrap_ticker(ticker: str):
    today = dt.date.today()
    start = today - dt.timedelta(days=DAYS_BACK)
    iv_series = []
    print(f"→ Bootstrapping {ticker} ...")

    for i in range(DAYS_BACK):
        current_date = start + dt.timedelta(days=i)
        try:
            spot = get_stock_close_on_date(ticker, current_date)
            if not spot:
                continue

            contract = find_atm_option_contract(ticker, current_date, spot)
            if not contract:
                continue

            opt_symbol = contract["ticker"]
            strike = float(contract["strike_price"])
            opt_type = contract["contract_type"].lower()
            exp = dt.date.fromisoformat(contract["expiration_date"])
            T = max((exp - current_date).days, 1) / 365.0

            opt_price = get_option_close(opt_symbol, current_date)
            if not opt_price:
                continue

            iv = implied_vol(opt_price, spot, strike, T, RISK_FREE, option_type=opt_type)
            if iv:
                iv_series.append({"date": str(current_date), "iv": round(iv * 100, 2)})

        except Exception as e:
            print(f"  ⚠️ {ticker} {current_date}: {e}")

        sleep()

    print(f"  ✅ {ticker}: {len(iv_series)} days built.")
    return iv_series

# --- Main ---------------------------------------------------------------

def main():
    history = {}
    for t in TICKERS:
        ivs = bootstrap_ticker(t)
        if ivs:
            history[t] = ivs

    save_iv_history(history)
    print(f"\n✅ Completed bootstrapping — wrote {len(history)} tickers to iv_history.json\n")

if __name__ == "__main__":
    main()
