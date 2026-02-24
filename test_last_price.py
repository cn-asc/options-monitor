#!/usr/bin/env python3
"""
Test script: see which source (Polygon snapshot, Finnhub, yfinance) is used for Last Px
and what values each returns. Run during market hours to verify today's price.

Usage (from project root with .env loaded):
  python test_last_price.py
  python test_last_price.py GOOG NVDA
"""
import os
import sys
from dotenv import load_dotenv
load_dotenv()

# Use a couple of tickers for quick test
TICKERS = sys.argv[1:] if len(sys.argv) > 1 else ["GOOG", "NVDA", "META"]

# Import after env is loaded
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from options_dashboard_v2 import (
    get_equity_snapshot,
    _last_px_and_1d_from_snapshot,
    get_last_px_and_1d_finnhub,
    get_last_px_and_1d_yfinance,
    get_equity_window,
    equity_metrics_from_aggs,
)

def main():
    print("Testing Last Px / 1D % sources (run during market hours for today's price)")
    print("Tickers:", TICKERS)
    print()

    for ticker in TICKERS:
        print(f"--- {ticker} ---")

        # 1) Polygon snapshot
        snap = get_equity_snapshot(ticker)
        if snap:
            vals = _last_px_and_1d_from_snapshot(snap)
            if vals:
                print(f"  Polygon snapshot: Last Px = {vals[0]}, 1D % = {vals[1]}")
            else:
                print("  Polygon snapshot: got data but _last_px_and_1d_from_snapshot returned None")
        else:
            print("  Polygon snapshot: not available (e.g. 403)")

        # 2) Finnhub
        fh = get_last_px_and_1d_finnhub(ticker)
        if fh:
            print(f"  Finnhub quote:    Last Px = {fh[0]}, 1D % = {fh[1]}")
        else:
            print("  Finnhub quote:    not available")

        # 3) yfinance
        yf = get_last_px_and_1d_yfinance(ticker)
        if yf:
            print(f"  yfinance:         Last Px = {yf[0]}, 1D % = {yf[1]}")
        else:
            print("  yfinance:         not available")

        # 4) Polygon daily aggs (what we'd use if all above failed)
        try:
            aggs = get_equity_window(ticker)
            eq = equity_metrics_from_aggs(aggs)
            if eq:
                print(f"  Polygon daily:    Last Px = {eq['Last Px']}, 1D % = {eq['1D %']} (last close)")
        except Exception as e:
            print(f"  Polygon daily:    error {e}")

        # 5) What the dashboard would actually use (same order as process_ticker)
        snap_vals = None
        if snap:
            snap_vals = _last_px_and_1d_from_snapshot(snap)
        if snap_vals is None:
            snap_vals = get_last_px_and_1d_finnhub(ticker)
        if snap_vals is None:
            snap_vals = get_last_px_and_1d_yfinance(ticker)
        if snap_vals:
            print(f"  >>> USED:         Last Px = {snap_vals[0]}, 1D % = {snap_vals[1]}")
        else:
            print("  >>> USED:         (fallback to Polygon daily = last close)")

        print()

if __name__ == "__main__":
    main()
