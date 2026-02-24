#!/usr/bin/env python3
"""
Fetch shares outstanding data from Polygon API for specified tickers and dates.

Usage:
    python fetch_shares_outstanding.py

Output:
    - Prints results to stdout (CSV format)
    - Saves results to shares_outstanding.json
"""

import os
import json
import time
import sys
import requests
from typing import Dict, Any, Optional, List
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "").strip()

if not POLYGON_API_KEY:
    raise SystemExit("POLYGON_API_KEY missing in .env")

BASE_URL = "https://api.polygon.io"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "SharesOutstandingFetcher/1.0"})

# Tickers to fetch
TICKERS = [
    "APLD", "BTDR", "BE", "AVGO", "CIFR", "COHR", "CEG", "CORZ", "CRWV",
    "EQT", "GLXY", "HUT", "IREN", "LITE", "MRVL", "MOD", "ONTO", "RIOT",
    "SNDK", "STX", "SEI", "TLN", "TSEM", "VRT", "VST", "WDC"
]

# Dates to fetch (YYYY-MM-DD format)
DATES = [
    "2024-12-31",
    "2025-03-31",
    "2025-06-30",
    "2025-09-30"
]

OUTPUT_JSON_FILE = "shares_outstanding.json"


def get_json(url: str, params: Optional[Dict[str, Any]] = None, max_retries: int = 8) -> Dict[str, Any]:
    """
    Fetch JSON from Polygon API with retry logic and rate limit handling.
    """
    if params is None:
        params = {}
    params["apiKey"] = POLYGON_API_KEY

    last_err = None
    backoff = 2.0  # Start with 2 second backoff for 429

    for attempt in range(max_retries):
        try:
            resp = SESSION.get(url, params=params, timeout=30)
            status = resp.status_code

            # Handle rate limiting - exponential backoff for 429
            if status == 429:
                last_err = RuntimeError(f"Polygon HTTP {status} on {url}")
                if attempt < max_retries - 1:
                    print(f"  Rate limited, waiting {backoff:.1f}s...", file=sys.stderr)
                    time.sleep(backoff)
                    backoff = min(backoff * 1.5, 15)  # Cap at 15 seconds
                    continue
                else:
                    raise RuntimeError(f"Rate limited after {max_retries} attempts: {url}")
            elif 500 <= status < 600:
                last_err = RuntimeError(f"Polygon HTTP {status} on {url}")
                if attempt < max_retries - 1:
                    time.sleep(2.0)  # Longer delay for server errors
                continue

            resp.raise_for_status()
            data = resp.json()
            return data

        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                if "429" in str(e) or "Rate limited" in str(e):
                    time.sleep(backoff)
                    backoff = min(backoff * 1.5, 15)
                else:
                    time.sleep(1.0)  # Brief delay for other errors

    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def fetch_shares_outstanding(ticker: str, date: str, prefer_class_a: bool = True) -> Optional[Dict[str, Any]]:
    """
    Fetch shares outstanding data for a specific ticker and date.
    If prefer_class_a is True, tries to find Class A shares first, falls back to Common Stock.
    
    Returns:
        Dict with 'share_class_shares_outstanding' and 'weighted_shares_outstanding',
        or None if not available/error.
    """
    # First try the original ticker
    url = f"{BASE_URL}/v3/reference/tickers/{ticker}"
    params = {"date": date}
    
    try:
        data = get_json(url, params=params)
        result = data.get("results")
        
        if not result:
            return None
        
        name = result.get("name", "")
        is_class_a = "Class A" in name.upper()
        
        # If prefer_class_a and this is not Class A, try to find Class A variant
        if prefer_class_a and not is_class_a:
            alt_tickers = [f"{ticker}.A", f"{ticker}A"]
            for alt_ticker in alt_tickers:
                try:
                    alt_url = f"{BASE_URL}/v3/reference/tickers/{alt_ticker}"
                    alt_data = get_json(alt_url, params=params)
                    alt_result = alt_data.get("results")
                    if alt_result and "Class A" in alt_result.get("name", "").upper():
                        # Found Class A variant - use it
                        result = alt_result
                        name = alt_result.get("name", "")
                        ticker = alt_ticker  # Update ticker to reflect Class A variant
                        is_class_a = True
                        break
                except:
                    continue
        
        return {
            "ticker": ticker,
            "date": date,
            "share_class_shares_outstanding": result.get("share_class_shares_outstanding"),
            "weighted_shares_outstanding": result.get("weighted_shares_outstanding"),
            "market_cap": result.get("market_cap"),
            "name": result.get("name"),
            "is_class_a": is_class_a,
            "share_class_type": "Class A" if is_class_a else "Common Stock",
        }
    except Exception as e:
        print(f"  Error fetching {ticker} on {date}: {e}", file=sys.stderr)
        return {
            "ticker": ticker,
            "date": date,
            "share_class_shares_outstanding": None,
            "weighted_shares_outstanding": None,
            "market_cap": None,
            "name": None,
            "error": str(e)
        }


def format_number(num: Optional[float]) -> str:
    """Format large numbers with commas."""
    if num is None:
        return "N/A"
    return f"{num:,.0f}"


def main():
    """Main execution function."""
    print("Fetching shares outstanding data from Polygon API (Class A shares only)...", file=sys.stderr)
    print(f"Tickers: {len(TICKERS)}, Dates: {len(DATES)}", file=sys.stderr)
    print("", file=sys.stderr)
    
    results = {}
    all_data = []
    
    total_requests = len(TICKERS) * len(DATES)
    completed = 0
    
    # Fetch data for each ticker/date combination
    for ticker in TICKERS:
        results[ticker] = {}
        has_data = False
        
        for date in DATES:
            print(f"Fetching {ticker} on {date}...", file=sys.stderr, end=" ")
            data = fetch_shares_outstanding(ticker, date, prefer_class_a=True)
            
            if data:
                results[ticker][date] = data
                all_data.append(data)
                has_data = True
                share_type = data.get("share_class_type", "Unknown")
                print(f"✓ ({share_type})", file=sys.stderr)
            else:
                print(f"✗ (no data)", file=sys.stderr)
            
            completed += 1
            
            # Small delay between requests to avoid rate limits
            if completed < total_requests:
                time.sleep(0.5)
        
        # Remove tickers that don't have any data
        if not has_data:
            if ticker in results:
                del results[ticker]
    
    # Get valid tickers (those with data)
    valid_tickers = [t for t in TICKERS if t in results]
    
    # Save to JSON file
    output_data = {
        "fetched_at": datetime.now().isoformat(),
        "tickers": valid_tickers,
        "dates": DATES,
        "results": results,
        "all_data": all_data,
        "note": "Prefers Class A shares when available, falls back to Common Stock"
    }
    
    with open(OUTPUT_JSON_FILE, "w") as f:
        json.dump(output_data, f, indent=2)
    
    print("", file=sys.stderr)
    print(f"Results saved to {OUTPUT_JSON_FILE}", file=sys.stderr)
    print("", file=sys.stderr)
    
    # Print CSV output to stdout
    print("Ticker,Date,Share Class Shares Outstanding,Weighted Shares Outstanding,Market Cap,Company Name")
    
    for ticker in valid_tickers:
        for date in DATES:
            data = results.get(ticker, {}).get(date, {})
            share_class = data.get("share_class_shares_outstanding")
            weighted = data.get("weighted_shares_outstanding")
            market_cap = data.get("market_cap")
            name = data.get("name", "")
            
            print(f"{ticker},{date},{format_number(share_class)},{format_number(weighted)},{format_number(market_cap)},{name}")
    
    # Print summary statistics
    print("", file=sys.stderr)
    print("Summary:", file=sys.stderr)
    successful = sum(1 for item in all_data if item.get("share_class_shares_outstanding") is not None)
    class_a_count = sum(1 for item in all_data if item.get("is_class_a"))
    print(f"  Successful: {successful}/{total_requests}", file=sys.stderr)
    print(f"  Class A shares: {class_a_count}/{successful}", file=sys.stderr)
    print(f"  Common Stock: {successful - class_a_count}/{successful}", file=sys.stderr)
    print(f"  Failed/Missing: {total_requests - successful}/{total_requests}", file=sys.stderr)
    print(f"  Valid tickers: {len(valid_tickers)}/{len(TICKERS)}", file=sys.stderr)


if __name__ == "__main__":
    main()
