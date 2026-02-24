#!/usr/bin/env python3
"""
Backfill missing dates in iv_history.json and price_history.json

Usage:
    python backfill_history.py [max_days_back]

This script identifies missing dates in the history files and fetches
historical data from Polygon to fill in the gaps.
"""

import sys
import datetime as dt
from typing import Dict, Any, List, Optional
from options_dashboard import (
    BASE_URL, TICKERS, MONEYNESS_PCT, RISK_FREE,
    get_json, _sleep, implied_vol,
    load_iv_history, save_iv_history,
    load_price_history, save_price_history,
)


def get_equity_close_on_date(ticker: str, date: dt.date) -> Optional[float]:
    """Get equity close price for a specific date."""
    url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{date}/{date}"
    try:
        data = get_json(url)
        results = data.get("results")
        if results and results[0].get("c") is not None:
            return float(results[0]["c"])
    except Exception:
        pass
    return None


def get_option_close_on_date(option_symbol: str, date: dt.date) -> Optional[float]:
    """Get option close price for a specific date."""
    url = f"{BASE_URL}/v2/aggs/ticker/{option_symbol}/range/1/day/{date}/{date}"
    try:
        data = get_json(url)
        results = data.get("results")
        if results and results[0].get("c") is not None:
            return float(results[0]["c"])
    except Exception:
        pass
    return None


def pick_contract_at_moneyness_for_date(
    ticker: str,
    spot: float,
    moneyness_pct: float,
    direction: str,
    target_date: dt.date,
) -> Optional[Dict[str, Any]]:
    """Pick contract at moneyness for a specific historical date."""
    direction = direction.lower()
    if direction == "down":
        target_strike = spot * (1 - moneyness_pct)
        desired_type = "put"
    else:
        target_strike = spot * (1 + moneyness_pct)
        desired_type = "call"

    url = f"{BASE_URL}/v3/reference/options/contracts"
    params = {
        "underlying_ticker": ticker,
        "as_of": str(target_date),
        "limit": 1000
    }

    try:
        data = get_json(url, params)
        contracts = data.get("results") or []
        if not contracts:
            return None

        def enrich(c: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            strike = c.get("strike_price")
            expiry_str = c.get("expiration_date")
            if strike is None or not expiry_str or not c.get("ticker"):
                return None
            try:
                expiry = dt.datetime.fromisoformat(expiry_str).date()
            except Exception:
                return None

            dte = (expiry - target_date).days
            if dte <= 0:
                return None

            c = dict(c)  # copy
            c["_strike"] = float(strike)
            c["_expiry"] = expiry
            c["_dte"] = dte
            c["_type"] = c.get("contract_type", "").lower()
            return c

        enriched: List[Dict[str, Any]] = []
        for c in contracts:
            ec = enrich(c)
            if ec is not None:
                enriched.append(ec)

        if not enriched:
            return None

        def score_contract(c: Dict[str, Any]) -> float:
            strike_err = abs(c["_strike"] - target_strike) / max(1.0, spot)
            dte_err = abs(c["_dte"] - 30) / 30.0
            return strike_err + 0.6 * dte_err

        def choose(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if not candidates:
                return None
            return min(candidates, key=score_contract)

        # Try progressively relaxed filters
        for filter_func in [
            lambda c: c["_type"] == desired_type and 10 <= c["_dte"] <= 60,
            lambda c: c["_type"] == desired_type and 5 <= c["_dte"] <= 90,
            lambda c: 10 <= c["_dte"] <= 60,
            lambda c: True,
        ]:
            candidates = [c for c in enriched if filter_func(c)]
            best = choose(candidates)
            if best:
                return best

        return None
    except Exception:
        return None


def backfill_missing_dates(ticker: str, missing_dates: List[dt.date]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Backfill missing dates for a ticker.
    Returns dict with 'iv_history' and 'price_history' entries to add.
    """
    iv_entries = []
    price_entries = []
    
    print(f"  Backfilling {len(missing_dates)} dates for {ticker}...")
    
    for date in sorted(missing_dates):
        try:
            # Get equity close
            spot = get_equity_close_on_date(ticker, date)
            if not spot:
                print(f"    ⚠️  {date}: No equity data")
                continue
            
            price_entries.append({"date": str(date), "close": round(spot, 4)})
            
            # Get option contracts and compute IV
            minus_contract = pick_contract_at_moneyness_for_date(ticker, spot, MONEYNESS_PCT, "down", date)
            plus_contract = pick_contract_at_moneyness_for_date(ticker, spot, MONEYNESS_PCT, "up", date)
            
            iv_values = []
            
            for contract, direction in [(minus_contract, "minus"), (plus_contract, "plus")]:
                if not contract:
                    continue
                
                opt_symbol = contract.get("ticker")
                strike = float(contract.get("strike_price"))
                opt_type = contract.get("contract_type", "call").lower()
                expiry_str = contract.get("expiration_date")
                
                expiry = dt.datetime.fromisoformat(expiry_str).date()
                T = max((expiry - date).days, 1) / 365.0
                
                opt_close = get_option_close_on_date(opt_symbol, date)
                if not opt_close:
                    continue
                
                vol = implied_vol(opt_close, spot, strike, T, RISK_FREE, opt_type)
                if vol:
                    iv_values.append(vol * 100)
            
            if iv_values:
                avg_iv = sum(iv_values) / len(iv_values)
                iv_entries.append({"date": str(date), "iv": round(avg_iv, 2)})
                print(f"    ✅ {date}: IV={round(avg_iv, 2)}%")
            else:
                print(f"    ⚠️  {date}: No IV data")
            
            _sleep()  # Rate limiting
            
        except Exception as e:
            print(f"    ❌ {date}: {e}")
            continue
    
    return {"iv_history": iv_entries, "price_history": price_entries}


def main():
    max_days_back = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    
    print(f"\n🔧 Starting backfill for missing dates (last {max_days_back} days)...\n")
    
    # Load existing history
    iv_hist = load_iv_history()
    price_hist = load_price_history()
    
    today = dt.date.today()
    start_date = today - dt.timedelta(days=max_days_back)
    
    # Find missing dates for each ticker
    all_tickers = set(list(iv_hist.keys()) + list(price_hist.keys()))
    if not all_tickers:
        all_tickers = set(TICKERS)
    
    total_backfilled = 0
    
    for ticker in sorted(all_tickers):
        # Get existing dates
        iv_dates = {dt.datetime.strptime(entry["date"], "%Y-%m-%d").date() 
                   for entry in iv_hist.get(ticker, [])}
        price_dates = {dt.datetime.strptime(entry["date"], "%Y-%m-%d").date() 
                      for entry in price_hist.get(ticker, [])}
        
        # Find missing dates (only weekdays, within range)
        missing = []
        check_date = start_date
        while check_date <= today:
            if check_date.weekday() < 5:  # Monday-Friday only
                if check_date not in iv_dates or check_date not in price_dates:
                    missing.append(check_date)
            check_date += dt.timedelta(days=1)
        
        if not missing:
            print(f"  {ticker}: ✅ No missing dates")
            continue
        
        print(f"  {ticker}: Found {len(missing)} missing dates")
        
        # Backfill
        backfilled = backfill_missing_dates(ticker, missing)
        
        # Merge into history
        if backfilled["iv_history"]:
            existing_iv = iv_hist.get(ticker, [])
            existing_iv.extend(backfilled["iv_history"])
            existing_iv.sort(key=lambda x: x["date"])
            iv_hist[ticker] = existing_iv
            total_backfilled += len(backfilled["iv_history"])
        
        if backfilled["price_history"]:
            existing_price = price_hist.get(ticker, [])
            existing_price.extend(backfilled["price_history"])
            existing_price.sort(key=lambda x: x["date"])
            price_hist[ticker] = existing_price
    
    # Save updated history
    if total_backfilled > 0:
        save_iv_history(iv_hist)
        save_price_history(price_hist)
        print(f"\n✅ Backfill complete: Added {total_backfilled} IV entries across all tickers")
    else:
        print(f"\n✅ No data to backfill")


if __name__ == "__main__":
    main()
