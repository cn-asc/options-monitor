#!/usr/bin/env python3
"""
One-off debug: fetch one Polygon option snapshot and print raw JSON.
Use this to verify implied_volatility format (decimal vs percent) when IVs look wrong.

Usage:
  python3 debug_iv_snapshot.py              # uses TICKER from .env, first option found
  TICKER=AAPL python3 debug_iv_snapshot.py   # optional override

Requires: POLYGON_API_KEY and TICKERS (or TICKER) in .env. Network access.
"""

import os
import json
import sys
from dotenv import load_dotenv
import requests

load_dotenv()
API_KEY = os.getenv("POLYGON_API_KEY", "").strip()
TICKER = os.getenv("TICKER") or (os.getenv("TICKERS", "AAPL").split(",")[0].strip().upper())
BASE = "https://api.polygon.io"

if not API_KEY:
    print("POLYGON_API_KEY missing in .env", file=sys.stderr)
    sys.exit(1)

# Get one option contract for the ticker
url = f"{BASE}/v3/reference/options/contracts"
r = requests.get(url, params={"underlying_ticker": TICKER, "limit": 5, "apiKey": API_KEY}, timeout=15)
r.raise_for_status()
data = r.json()
contracts = data.get("results") or []
if not contracts:
    print(f"No option contracts for {TICKER}", file=sys.stderr)
    sys.exit(1)
opt = contracts[0]
opt_ticker = opt.get("ticker")
if not opt_ticker:
    print("Contract has no ticker", file=sys.stderr)
    sys.exit(1)

# Snapshot for that contract
snap_url = f"{BASE}/v3/snapshot/options/{TICKER}/{opt_ticker}"
r2 = requests.get(snap_url, params={"apiKey": API_KEY}, timeout=15)
r2.raise_for_status()
snap = r2.json()

print("--- Raw snapshot response ---")
print(json.dumps(snap, indent=2))
results = snap.get("results")
if isinstance(results, list):
    results = results[0] if results else None
if results:
    iv = results.get("implied_volatility")
    print("\n--- implied_volatility value ---")
    print(f"  raw: {iv!r}")
    if iv is not None:
        try:
            v = float(iv)
            if 0 < v <= 1.5:
                print(f"  interpreted as decimal -> {round(v * 100, 2)}%")
            elif 1.5 < v < 150:
                print(f"  interpreted as already percent -> {round(v, 2)}%")
            else:
                print(f"  out of range (rejected in dashboard)")
        except Exception as e:
            print(f"  float error: {e}")
