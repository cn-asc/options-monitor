import os
import json
import math
import time
import sys
import datetime as dt
from typing import Dict, Any, List, Optional, Tuple
import requests
from dotenv import load_dotenv
import subprocess
import statistics

"""
Options Dashboard v2 (API-Only - No Fallbacks)

- Equity data: prefer Polygon Stock Snapshot (15-min delayed) for Last Px and 1D %; if unavailable (e.g. 403),
  use Finnhub quote (real-time US) then yfinance; else use daily aggregates (last close only).
  Daily aggregates (range/1/day) used for 7D %, 3M/6M/52W highs/lows, and realized volatility.
- Per-moneyness IV (±X%) fetched directly from Polygon Options Snapshot API
  (requires Options Starter/Developer/Advanced/Business plan).
  NO Black-Scholes calculations - API-only.
- Realized Volatility (30D & LTM) computed from API-fetched equity aggregates.
  NO price_history.json fallbacks - API-only.
- RV "Rank" fields are *ratios*: IV / RV (e.g., -15% 30D RV Rank = (-15% IV) / (30D RV)).

If API doesn't provide data, returns None and logs error - no fallbacks.

Environment (.env):
  POLYGON_API_KEY=... (must have Options plan access)
  TICKERS=COIN,MSTR,NVDA,...
  RISK_FREE_RATE=0.04
  MAX_TICKERS_PER_RUN=15
  MONEYNESS_PCT=0.15          # 15% OTM/ITM
"""

# --- Config & setup ---------------------------------------------------------
load_dotenv()
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "").strip()
TICKERS = [t.strip().upper() for t in os.getenv("TICKERS", "AAPL,MSFT,GOOG,META").split(",") if t.strip()]
# RISK_FREE removed - v2 uses API-only, no Black-Scholes calculations
MAX_TICKERS = int(os.getenv("MAX_TICKERS_PER_RUN", str(len(TICKERS))))
MONEYNESS_PCT = float(os.getenv("MONEYNESS_PCT", "0.15"))
if MONEYNESS_PCT <= 0 or MONEYNESS_PCT > 0.5:
    MONEYNESS_PCT = 0.15

if not POLYGON_API_KEY:
    raise SystemExit("POLYGON_API_KEY missing in .env")

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()
BASE_URL = "https://api.polygon.io"
FINNHUB_BASE = "https://finnhub.io/api/v1"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "OptionsDashboard/2.0"})

# Storage for IV history (for future use)
IV_HISTORY_FILE = "iv_history.json"   # {ticker: [{"date": "YYYY-MM-DD", "iv": float}, ...]}
HISTORY_DAYS = 252                    # ~1y trading days for IV history

# Storage for price history (for RV)
PRICE_HISTORY_FILE = "price_history.json"  # {ticker: [{"date": "YYYY-MM-DD", "close": float}, ...]}

# No rate limiting - unlimited API calls available


# --- Utilities --------------------------------------------------------------

def get_json(url: str, params: Optional[Dict[str, Any]] = None, max_retries: int = 8) -> Dict[str, Any]:
    """
    Thin wrapper over requests + Polygon, with:
      - API key injection
      - aggressive retry on 429 / 5xx with exponential backoff for 429
      - no delays for successful calls - unlimited API calls
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
                    time.sleep(backoff)
                    backoff = min(backoff * 1.5, 15)  # Cap at 15 seconds, slower growth
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
            # No delay - unlimited API calls
            return data

        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                # Check if it's a rate limit error
                if "429" in str(e) or "Rate limited" in str(e):
                    time.sleep(backoff)
                    backoff = min(backoff * 1.5, 15)
                else:
                    time.sleep(1.0)  # Brief delay for other errors

    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


# --- Polygon helpers --------------------------------------------------------

def get_equity_snapshot(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Get 15-min delayed stock snapshot (last price, today's change, prev day).
    Returns None if endpoint not available (e.g. Options-only plan without Stocks).
    """
    url = f"{BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
    try:
        data = get_json(url)
        return data.get("ticker")
    except Exception as e:
        print(f"⚠️ Stock snapshot not available for {ticker}: {e}", file=sys.stderr)
        return None


def get_last_px_and_1d_finnhub(ticker: str) -> Optional[Tuple[float, float]]:
    """
    Get current price and 1D % from Finnhub quote API (real-time for US stocks).
    Requires FINNHUB_API_KEY in .env. Returns (last_px, 1d_pct) or None.
    """
    if not FINNHUB_API_KEY:
        return None
    # Finnhub uses hyphen for some symbols (e.g. BRK-B)
    symbol = ticker.replace(".", "-")
    try:
        url = f"{FINNHUB_BASE}/quote"
        params = {"symbol": symbol, "token": FINNHUB_API_KEY}
        resp = SESSION.get(url, params=params, timeout=10)
        resp.raise_for_status()
        q = resp.json()
        c = q.get("c")
        pc = q.get("pc")
        if c is None or pc is None or float(pc) == 0:
            return None
        last_px = float(c)
        prev_close = float(pc)
        one_d_pct = (last_px - prev_close) / prev_close * 100.0
        return (round(last_px, 2), round(one_d_pct, 2))
    except Exception as e:
        print(f"⚠️ Finnhub quote not available for {ticker}: {e}", file=sys.stderr)
        return None


def get_last_px_and_1d_yfinance(ticker: str) -> Optional[Tuple[float, float]]:
    """
    Get latest (delayed) price and 1D % from yfinance when Polygon stock snapshot is unavailable.
    Prefers intraday bars (1d with 1h interval) so we get the most recent trade when market is open;
    falls back to daily bars / fast_info when intraday is empty (e.g. market closed).
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        prev_close = None
        last_px = None

        # 5d daily bars: get previous close for 1D % and fallback last_px
        hist_5d = t.history(period="5d")
        if hist_5d is not None and not hist_5d.empty:
            closes = hist_5d["Close"]
            prev_close = float(closes.iloc[-2]) if len(closes) >= 2 else float(closes.iloc[-1])
            last_px = float(closes.iloc[-1])  # default to last daily close

        # Prefer intraday (today's bars) for latest price when market is open — 15m then 1h
        for interval in ("15m", "1h"):
            hist_1d = t.history(period="1d", interval=interval)
            if hist_1d is not None and not hist_1d.empty:
                last_px = float(hist_1d["Close"].iloc[-1])
                break

        if last_px is None:
            if hasattr(t, "fast_info"):
                try:
                    last_px = getattr(t.fast_info, "last_price", None)
                    if prev_close is None:
                        prev_close = getattr(t.fast_info, "previous_close", None)
                except Exception:
                    pass
            if last_px is None and hasattr(t, "info"):
                info = t.info or {}
                last_px = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
                if prev_close is None:
                    prev_close = info.get("previousClose")

        if last_px is None:
            return None
        last_px = float(last_px)
        if prev_close is not None and float(prev_close) != 0:
            one_d_pct = (last_px - float(prev_close)) / float(prev_close) * 100.0
        else:
            one_d_pct = 0.0
        return (round(last_px, 2), round(one_d_pct, 2))
    except Exception as e:
        print(f"⚠️ yfinance fallback not available for {ticker}: {e}", file=sys.stderr)
        return None


def get_equity_window(ticker: str, days_back: int = 365) -> Dict[str, Any]:
    """
    Get equity aggregates for calculating price metrics.
    
    The date range is used to calculate:
    - Last Px: most recent close price
    - 1D %, 7D %: daily and weekly percentage changes
    - 3M/6M/52W High/Low: historical highs and lows
    
    We request 365 calendar days (~252 trading days) to get accurate 52W metrics.
    This may hit rate limits, but we have aggressive retry logic to handle it.
    """
    today = dt.date.today()
    start = today - dt.timedelta(days=days_back)
    url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{today}"
    return get_json(url)


def equity_metrics_from_aggs(aggs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    res = aggs.get("results")
    if not res or len(res) == 0:
        return None

    closes = [r["c"] for r in res if r.get("c") is not None]
    highs = [r["h"] for r in res if r.get("h") is not None]
    lows = [r["l"] for r in res if r.get("l") is not None]

    if not closes:
        return None

    last_px = closes[-1]
    one_day = ((closes[-1] - closes[-2]) / closes[-2] * 100.0) if len(closes) > 1 else 0.0
    seven_day = ((closes[-1] - closes[-7]) / closes[-7] * 100.0) if len(closes) > 7 else 0.0

    three_m_high = max(highs[-63:]) if len(highs) >= 63 else (max(highs) if highs else last_px)
    six_m_high = max(highs[-126:]) if len(highs) >= 126 else (max(highs) if highs else last_px)
    year_high = max(highs) if highs else last_px

    three_m_low = min(lows[-63:]) if len(lows) >= 63 else (min(lows) if lows else last_px)
    six_m_low = min(lows[-126:]) if len(lows) >= 126 else (min(lows) if lows else last_px)
    year_low = min(lows) if lows else last_px

    return {
        "Last Px": round(last_px, 2),
        "1D %": round(one_day, 2),
        "7D %": round(seven_day, 2),
        "3M High": round(three_m_high, 2),
        "6M High": round(six_m_high, 2),
        "52W High": round(year_high, 2),
        "3M Low": round(three_m_low, 2),
        "6M Low": round(six_m_low, 2),
        "52W Low": round(year_low, 2),
    }


def _last_px_and_1d_from_snapshot(snap: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """Extract (last_px, 1d_pct) from stock snapshot if available. Otherwise None."""
    if not snap:
        return None
    # Prefer minute bar close (15-min delayed), then day bar, then prevDay
    last_px = None
    if snap.get("min") and snap["min"].get("c") is not None:
        last_px = float(snap["min"]["c"])
    elif snap.get("day") and snap["day"].get("c") is not None:
        last_px = float(snap["day"]["c"])
    elif snap.get("prevDay") and snap["prevDay"].get("c") is not None:
        last_px = float(snap["prevDay"]["c"])
    if last_px is None:
        return None
    # 1D % from snapshot (todaysChangePerc) when available
    one_d_pct = snap.get("todaysChangePerc")
    if one_d_pct is not None:
        return (last_px, round(float(one_d_pct), 2))
    # Fallback: (last_px - prevDay.c) / prevDay.c * 100
    prev_c = snap.get("prevDay", {}).get("c")
    if prev_c is not None and float(prev_c) != 0:
        one_d_pct = (last_px - float(prev_c)) / float(prev_c) * 100.0
        return (last_px, round(one_d_pct, 2))
    return (last_px, 0.0)


def get_target_expiry_date(target_dte: int = 90, use_exact_dte: bool = True) -> Optional[dt.datetime]:
    """
    Determine the target expiry date for all tickers in the monitor.
    
    If use_exact_dte=True: Uses exact target_dte days from today (matches Bloomberg Quick Pricer).
    If use_exact_dte=False: Finds monthly expiry (third Friday) closest to today + target_dte days.
    
    Args:
        target_dte: Target days to expiry (default 90)
        use_exact_dte: If True, use exact DTE regardless of expiry type (default True, matches Bloomberg)
    
    Returns:
        Target expiry datetime, or None if unable to determine
    """
    today = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    
    if use_exact_dte:
        # Bloomberg approach: Use exact DTE (e.g., 28-Apr-2026 for 90 DTE from 28-Jan-2026)
        # This may not be a 3rd Friday, but Bloomberg can calculate IV for any expiry date
        target_date = today + dt.timedelta(days=target_dte)
        # Set to market close time (4:00 PM ET = 16:00 UTC, but adjust for timezone)
        # For simplicity, use 16:00 UTC (market close is typically 4:00 PM ET = 20:00 UTC)
        return target_date.replace(hour=20, minute=0, second=0, microsecond=0)
    
    # Original logic: Find monthly expiry closest to target_dte
    target_date = today + dt.timedelta(days=target_dte)
    
    def find_third_friday(year: int, month: int) -> Optional[dt.datetime]:
        """Find the third Friday of a given year/month."""
        for day in range(15, 22):
            candidate = dt.datetime(year, month, day, tzinfo=dt.UTC)
            if candidate.weekday() == 4:  # Friday
                return candidate.replace(hour=16, minute=0, second=0)
        return None
    
    candidate_expiries = []
    target_year = target_date.year
    target_month = target_date.month
    
    for month_offset in range(-6, 7):
        year = target_year
        month = target_month + month_offset
        while month < 1:
            month += 12
            year -= 1
        while month > 12:
            month -= 12
            year += 1
        
        expiry = find_third_friday(year, month)
        if expiry and expiry > today:
            days_diff = abs((expiry.date() - target_date.date()).days)
            candidate_expiries.append({
                'expiry': expiry,
                'dte': (expiry.date() - today.date()).days,
                'distance_from_target_date': days_diff
            })
    
    if not candidate_expiries:
        return None
    
    best = min(candidate_expiries, key=lambda x: x['distance_from_target_date'])
    return best['expiry']


def pick_contract_at_moneyness(
    ticker: str,
    spot: float,
    moneyness_pct: float,
    direction: str,
    target_expiry: Optional[dt.datetime] = None,
):
    """
    Robust option picker for ±X% moneyness.
    
    If target_expiry is provided, uses that exact expiry date for consistency across all tickers.
    Otherwise, prefers monthly/quarterly expiries over weekly/daily options.
    Monthly options expire on the third Friday of each month (most liquid and standard).

    direction = "down" → target_strike = spot * (1 - X), prefer puts
    direction = "up"   → target_strike = spot * (1 + X), prefer calls

    The function:
      - pulls Polygon reference contracts
      - if target_expiry provided: filters to that exact expiry
      - otherwise: identifies monthly/quarterly expiries (third Friday of month)
      - prioritizes monthly/quarterly expiries over weekly/daily
      - filters by desired type and DTE windows
      - scores by distance to target strike
      - has multiple fallbacks if the ideal contract is not available
    """
    direction = direction.lower()
    if direction not in ("down", "up"):
        raise ValueError(f"direction must be 'down' or 'up', got {direction}")

    if direction == "down":
        target_strike = spot * (1 - moneyness_pct)
        desired_type = "put"
    else:
        target_strike = spot * (1 + moneyness_pct)
        desired_type = "call"

    url = f"{BASE_URL}/v3/reference/options/contracts"

    # Build a list of expiry dates to query: target first, then fallback candidates
    # We query by specific expiry to avoid paginating through thousands of near-term contracts
    today = dt.datetime.now(dt.UTC)

    def _monthly_expiries_near(anchor: dt.datetime, count: int = 6) -> List[dt.datetime]:
        """Return up to `count` third-Friday monthly expiries on or after anchor."""
        results = []
        year, month = anchor.year, anchor.month
        for _ in range(count * 2):
            # Find third Friday of (year, month)
            first_day = dt.datetime(year, month, 1, tzinfo=dt.UTC)
            first_friday = first_day + dt.timedelta(days=(4 - first_day.weekday()) % 7)
            third_friday = first_friday + dt.timedelta(weeks=2)
            if third_friday >= anchor:
                results.append(third_friday)
            month += 1
            if month > 12:
                month, year = 1, year + 1
            if len(results) >= count:
                break
        return results

    # Candidate expiry dates to try, in priority order
    candidate_expiries: List[Optional[dt.datetime]] = []
    if target_expiry:
        candidate_expiries.append(target_expiry)
        # Add nearby monthly expiries as fallbacks
        candidate_expiries.extend(_monthly_expiries_near(today))
    else:
        # No target: try monthly expiries starting ~30 DTE out
        candidate_expiries.extend(_monthly_expiries_near(today + dt.timedelta(days=25)))

    contracts = []
    used_expiry: Optional[dt.datetime] = None
    for cand_expiry in candidate_expiries:
        expiry_str = cand_expiry.strftime("%Y-%m-%d")
        params = {
            "underlying_ticker": ticker,
            "expiration_date": expiry_str,
            "contract_type": desired_type,
            "limit": 250,
        }
        data = get_json(url, params)
        page_contracts = data.get("results") or []
        if page_contracts:
            contracts = page_contracts
            used_expiry = cand_expiry
            if target_expiry and cand_expiry.date() != target_expiry.date():
                best_dte = (cand_expiry - today).days
                print(
                    f"⚠️ No contracts found for {ticker} matching target expiry "
                    f"{target_expiry.date()}, using closest monthly expiry "
                    f"{expiry_str} (DTE: {best_dte})",
                    file=sys.stderr,
                )
            break

    if not contracts:
        return None

    # Enrich contracts with computed fields
    def enrich(c: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        strike = c.get("strike_price")
        expiry_str = c.get("expiration_date")
        if strike is None or not expiry_str or not c.get("ticker"):
            return None
        try:
            expiry = dt.datetime.fromisoformat(expiry_str)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=dt.UTC)
        except Exception:
            return None

        dte = (expiry - today).days
        if dte <= 0:
            return None

        c = dict(c)  # copy
        c["_strike"] = float(strike)
        c["_expiry"] = expiry
        c["_dte"] = dte
        c["_type"] = c.get("contract_type", "").lower()
        # All contracts fetched via expiry-filtered query are treated as matching
        c["_matches_target"] = True
        c["_is_monthly"] = used_expiry is not None and (
            used_expiry.weekday() == 4 and 15 <= used_expiry.day <= 21
        )
        c["_is_quarterly"] = c["_is_monthly"] and (
            used_expiry is not None and used_expiry.month in [3, 6, 9, 12]
        )
        return c

    enriched: List[Dict[str, Any]] = [ec for c in contracts if (ec := enrich(c)) is not None]

    if not enriched:
        return None

    # scoring: minimize strike distance (in percent)
    # If target_expiry is set, all contracts should match it, so we only score by strike distance
    def score_contract(c: Dict[str, Any]) -> float:
        # Calculate strike error as percentage of spot price
        strike_err = abs(c["_strike"] - target_strike) / max(1.0, spot)
        
        if target_expiry:
            # When using target expiry, only score by strike distance
            # This ensures we get the strike closest to ±15% moneyness
            return strike_err
        else:
            # Original scoring: strike distance + DTE distance + expiry type penalty
            dte_err = abs(c["_dte"] - 30) / 30.0
            expiry_penalty = 0.0 if c.get("_is_monthly", False) else 10.0
            quarterly_bonus = -0.1 if c.get("_is_quarterly", False) else 0.0
            return strike_err + 0.6 * dte_err + expiry_penalty + quarterly_bonus

    # multi-pass filtering: progressively relax constraints
    def choose(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not candidates:
            return None
        return min(candidates, key=score_contract)

    if target_expiry:
        # When target_expiry is set, all contracts should match it
        # Just filter by type and choose best strike match
        
        # Pass 1: desired type, matching target expiry
        pass1 = [c for c in enriched if c["_type"] == desired_type]
        best = choose(pass1)
        if best:
            return best
        
        # Pass 2: any type, matching target expiry
        return choose(enriched)
    else:
        # Original logic: prioritize monthly/quarterly expiries
        # Pass 1: desired type, monthly expiry, 10–60 DTE (IDEAL)
        pass1 = [c for c in enriched
                 if c["_type"] == desired_type and c.get("_is_monthly", False) and 10 <= c["_dte"] <= 60]
        best = choose(pass1)
        if best:
            return best

        # Pass 2: desired type, monthly expiry, 5–90 DTE (relaxed DTE)
        pass2 = [c for c in enriched
                 if c["_type"] == desired_type and c.get("_is_monthly", False) and 5 <= c["_dte"] <= 90]
        best = choose(pass2)
        if best:
            return best

        # Pass 3: desired type, any expiry, 10–60 DTE (fallback to non-monthly if needed)
        pass3 = [c for c in enriched
                 if c["_type"] == desired_type and 10 <= c["_dte"] <= 60]
        best = choose(pass3)
        if best:
            return best

        # Pass 4: desired type, any expiry, 5–90 DTE
        pass4 = [c for c in enriched
                 if c["_type"] == desired_type and 5 <= c["_dte"] <= 90]
        best = choose(pass4)
        if best:
            return best

        # Pass 5: any type, monthly expiry, 10–60 DTE
        pass5 = [c for c in enriched
                 if c.get("_is_monthly", False) and 10 <= c["_dte"] <= 60]
        best = choose(pass5)
        if best:
            return best

        # Pass 6: any type, any expiry, 10–60 DTE
        pass6 = [c for c in enriched if 10 <= c["_dte"] <= 60]
        best = choose(pass6)
        if best:
            return best

        # Pass 7: any type, any positive DTE – last resort
        return choose(enriched)


def get_iv_from_snapshot(underlying_ticker: str, option_contract_ticker: str) -> Optional[float]:
    """
    Fetch implied volatility directly from Polygon Options Snapshot API.
    
    Args:
        underlying_ticker: The underlying ticker (e.g., "AAPL")
        option_contract_ticker: The option contract ticker (e.g., "O:AAPL250117C00150000")
    
    Returns:
        IV as a percentage (e.g., 25.5 for 25.5%), or None if unavailable.
    
    Polygon returns implied_volatility as a decimal (e.g. 0.3049 = 30.49%). We normalize:
    - If raw value in (0, 1.5] → treat as decimal, multiply by 100.
    - If raw value in (1.5, 150) → treat as already percent (API inconsistency), use as-is.
    - Otherwise reject and log (avoids showing 3000% or 0.01% from wrong field/units).
    """
    debug_iv = os.getenv("DEBUG_IV", "0") == "1"
    url = f"{BASE_URL}/v3/snapshot/options/{underlying_ticker}/{option_contract_ticker}"
    
    try:
        data = get_json(url)
        if debug_iv:
            print(f"[DEBUG_IV] Snapshot response for {option_contract_ticker}: {json.dumps(data, indent=2)[:1500]}", file=sys.stderr)
        results = data.get("results")
        # API may return results as object or as single-element list
        if isinstance(results, list):
            results = results[0] if results else None
        if not results or not isinstance(results, dict):
            return None
        
        # IV is at the top level of results (not inside greeks - greeks has delta/gamma/theta/vega only)
        iv = results.get("implied_volatility")
        if iv is None:
            # Fallback: check inside greeks (some docs suggest it can appear there)
            greeks = results.get("greeks")
            if greeks and isinstance(greeks, dict):
                iv = greeks.get("implied_volatility")
        
        if iv is None:
            return None
        
        iv_raw = float(iv)
        # Normalize: Polygon docs say decimal (0.30 = 30%); some responses may be percent already
        if 0 < iv_raw <= 1.5:
            iv_percent = round(iv_raw * 100, 2)
        elif 1.5 < iv_raw < 150:
            iv_percent = round(iv_raw, 2)
            if debug_iv:
                print(f"[DEBUG_IV] Treated IV as already percent: raw={iv_raw} -> {iv_percent}%", file=sys.stderr)
        else:
            print(f"    ⚠️ IV out of range for {underlying_ticker} {option_contract_ticker}: raw={iv_raw} (rejecting)", file=sys.stderr)
            return None
        if iv_percent > 100:
            print(f"    ⚠️ Unusually high IV for {underlying_ticker} {option_contract_ticker}: {iv_percent}%", file=sys.stderr)
        return iv_percent
        
    except Exception as e:
        # API-only: Log error if API fails - this indicates a problem with the API/service
        print(f"❌ API failed to fetch IV from snapshot for {option_contract_ticker}: {e}", file=sys.stderr)
        print(f"   Check your Options Starter plan includes IV data and the contract is valid", file=sys.stderr)
        return None


# --- IV history persistence (for future use) ---------------------------------

def load_iv_history() -> Dict[str, List[Dict[str, Any]]]:
    if not os.path.exists(IV_HISTORY_FILE):
        return {}
    try:
        with open(IV_HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_iv_history(hist: Dict[str, List[Dict[str, Any]]]) -> None:
    # Trim history to last N entries per ticker
    for tkr, rows in hist.items():
        hist[tkr] = rows[-HISTORY_DAYS:]
    with open(IV_HISTORY_FILE, "w") as f:
        json.dump(hist, f, indent=2)


def append_iv_history(ticker: str, iv_value: float) -> None:
    """Store today's average IV for this ticker in iv_history.json."""
    if iv_value is None:
        return
    today_str = str(dt.date.today())

    hist = load_iv_history()
    rows = hist.get(ticker, [])

    if not rows or rows[-1].get("date") != today_str:
        rows.append({"date": today_str, "iv": round(iv_value, 4)})
    else:
        rows[-1]["iv"] = round(iv_value, 4)

    hist[ticker] = rows
    save_iv_history(hist)


# --- Price history persistence ----------------------------------------------

def load_price_history() -> Dict[str, List[Dict[str, Any]]]:
    if not os.path.exists(PRICE_HISTORY_FILE):
        return {}
    try:
        with open(PRICE_HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_price_history(hist: Dict[str, List[Dict[str, Any]]]) -> None:
    # Keep last ~400 days per ticker to support LTM realized vol
    for tkr, rows in hist.items():
        hist[tkr] = rows[-400:]
    with open(PRICE_HISTORY_FILE, "w") as f:
        json.dump(hist, f, indent=2)


def append_price_history(ticker: str, price: float) -> None:
    """Append today's close for this ticker into price_history.json."""
    today_str = str(dt.date.today())

    hist = load_price_history()
    rows = hist.get(ticker, [])

    if not rows or rows[-1].get("date") != today_str:
        rows.append({"date": today_str, "close": round(price, 4)})
    else:
        rows[-1]["close"] = round(price, 4)

    hist[ticker] = rows
    save_price_history(hist)


# --- Realized Volatility (RV) ----------------------------------------------

def compute_realized_vol(series: List[float], window: int) -> Optional[float]:
    """Compute annualized realized volatility (%) from close prices."""
    if len(series) < window + 1:
        return None

    rets = []
    for i in range(1, window + 1):
        p1 = series[-i]
        p0 = series[-i - 1]
        if p0 <= 0 or p1 <= 0:
            return None
        rets.append(math.log(p1 / p0))

    if len(rets) < 2:
        return None

    std = statistics.stdev(rets)
    return round(std * math.sqrt(252) * 100, 2)


def safe_ratio(iv: Optional[float], rv: Optional[float]) -> Optional[float]:
    """IV / RV ratio used as 'RV Rank' surrogate."""
    if iv is None or rv is None or rv <= 0:
        return None
    return round(iv / rv, 2)


# --- Main per-ticker pipeline ----------------------------------------------

def process_ticker(ticker: str, target_expiry: Optional[dt.datetime] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"Ticker": ticker}

    # 1) Equity: prefer stock snapshot (15-min delayed) for Last Px + 1D %; daily aggs for rest
    eq_aggs = None
    eq = None
    max_equity_retries = 3

    snapshot = get_equity_snapshot(ticker)

    for attempt in range(max_equity_retries):
        try:
            eq_aggs = get_equity_window(ticker)
            eq = equity_metrics_from_aggs(eq_aggs)
            if eq:
                break
        except Exception as e:
            if attempt < max_equity_retries - 1:
                time.sleep(3.0 * (attempt + 1))
            else:
                print(f"❌ API failed to provide equity data for {ticker} after {max_equity_retries} attempts: {e}", file=sys.stderr)
                print(f"   This indicates an API issue - check your plan and API key", file=sys.stderr)

    # Override Last Px and 1D % with intraday/delayed data when available (not just last close)
    if eq:
        snap_vals = None
        if snapshot:
            snap_vals = _last_px_and_1d_from_snapshot(snapshot)
        if snap_vals is None:
            # Polygon stock snapshot unavailable — try Finnhub (real-time US), then yfinance
            snap_vals = get_last_px_and_1d_finnhub(ticker)
        if snap_vals is None:
            snap_vals = get_last_px_and_1d_yfinance(ticker)
        if snap_vals:
            last_px, one_d_pct = snap_vals
            eq["Last Px"] = round(last_px, 2) if isinstance(last_px, (int, float)) else last_px
            eq["1D %"] = one_d_pct
            if eq_aggs and eq_aggs.get("results"):
                closes = [r["c"] for r in eq_aggs["results"] if r.get("c") is not None]
                if len(closes) >= 7:
                    eq["7D %"] = round((last_px - closes[-7]) / closes[-7] * 100.0, 2)

    pct = int(MONEYNESS_PCT * 100)

    if not eq:
        out.update({
            "Error": "No equity data",
            "Last Px": None, "1D %": None, "7D %": None,
            "3M High": None, "6M High": None, "52W High": None,
            "3M Low": None, "6M Low": None, "52W Low": None,
            f"-{pct}% IV": None,
            f"+{pct}% IV": None,
            "30D RV": None,
            "LTM RV": None,
            f"-{pct}% 30D Ratio": None,
            f"+{pct}% 30D Ratio": None,
            f"-{pct}% LTM Ratio": None,
            f"+{pct}% LTM Ratio": None,
            "Expiry Date": None,
            "Earnings": None,
            "Comment": "No data",
        })
        return out

    out.update(eq)

    # 2) Select ±X% moneyness option contracts
    spot = eq["Last Px"]
    # Note: Not storing to price_history.json - v2 is API-only

    # Get target expiry from module-level variable set in build_dashboard
    # We'll pass it through a different mechanism - using a closure or global
    # For now, we'll determine it per-ticker but prefer the same one
    # Actually, let's pass it as a parameter - but we need to get it from build_dashboard
    # Let's use a simpler approach: determine target expiry once and store it
    minus_contract = pick_contract_at_moneyness(ticker, spot, MONEYNESS_PCT, "down", target_expiry)
    plus_contract = pick_contract_at_moneyness(ticker, spot, MONEYNESS_PCT, "up", target_expiry)
    
    # Log detailed contract selection for debugging (Bloomberg comparison)
    if minus_contract:
        expiry = minus_contract.get("_expiry")
        dte = minus_contract.get("_dte")
        strike = minus_contract.get("_strike") or minus_contract.get("strike_price")
        target_strike = spot * (1 - MONEYNESS_PCT)
        contract_type = minus_contract.get("_type", "put")
        is_monthly = minus_contract.get("_is_monthly", False)
        matches_target = minus_contract.get("_matches_target", False)
        expiry_type = "Monthly" if is_monthly else ("Quarterly" if minus_contract.get("_is_quarterly", False) else "Weekly/Daily")
        target_note = " (TARGET)" if matches_target else ""
        strike_diff = abs(strike - target_strike) / spot * 100 if strike and target_strike else 0
        actual_moneyness = ((strike - spot) / spot * 100) if strike and spot else 0
        print(f"  {ticker} -15%: {contract_type.upper()} | {expiry_type} expiry{target_note} | DTE={dte} | Strike=${strike:.2f} (target=${target_strike:.2f}, diff={strike_diff:.2f}%) | Actual moneyness={actual_moneyness:.2f}% | Expiry={expiry.strftime('%Y-%m-%d') if expiry else 'N/A'}", file=sys.stderr)
    if plus_contract:
        expiry = plus_contract.get("_expiry")
        dte = plus_contract.get("_dte")
        strike = plus_contract.get("_strike") or plus_contract.get("strike_price")
        target_strike = spot * (1 + MONEYNESS_PCT)
        contract_type = plus_contract.get("_type", "call")
        is_monthly = plus_contract.get("_is_monthly", False)
        matches_target = plus_contract.get("_matches_target", False)
        expiry_type = "Monthly" if is_monthly else ("Quarterly" if plus_contract.get("_is_quarterly", False) else "Weekly/Daily")
        target_note = " (TARGET)" if matches_target else ""
        strike_diff = abs(strike - target_strike) / spot * 100 if strike and target_strike else 0
        actual_moneyness = ((strike - spot) / spot * 100) if strike and spot else 0
        print(f"  {ticker} +15%: {contract_type.upper()} | {expiry_type} expiry{target_note} | DTE={dte} | Strike=${strike:.2f} (target=${target_strike:.2f}, diff={strike_diff:.2f}%) | Actual moneyness={actual_moneyness:.2f}% | Expiry={expiry.strftime('%Y-%m-%d') if expiry else 'N/A'}", file=sys.stderr)

    # Fetch IV directly from Polygon Options Snapshot API - API ONLY, no fallbacks
    # If selected contract doesn't have IV, try nearby contracts until we find one with IV
    def get_iv_for_contract(contract, direction):
        if not contract:
            return None, False  # (IV value, is_alternative_contract)
        opt_symbol = contract.get("ticker")
        if not opt_symbol:
            return None, False
        
        # Try the selected contract first
        iv = get_iv_from_snapshot(ticker, opt_symbol)
        if iv is not None:
            return iv, False  # Using selected contract
        
        # If no IV, try nearby contracts (within ±5% of target strike)
        # This handles cases where the closest strike has no IV data (illiquid, deep ITM, etc.)
        target_strike = float(contract.get("strike_price"))
        strike_range = spot * 0.05  # ±5% of spot price
        
        # Get contracts for this underlying
        url = f"{BASE_URL}/v3/reference/options/contracts"
        params = {"underlying_ticker": ticker, "limit": 200}
        try:
            contracts_data = get_json(url, params)
            contracts = contracts_data.get("results") or []
            
            # Filter by direction and strike proximity
            if direction == "down":
                desired_type = "put"
            else:
                desired_type = "call"
            
            nearby_contracts = []
            for c in contracts:
                c_strike = c.get("strike_price")
                c_type = c.get("contract_type", "").lower()
                if (c_strike and c_type == desired_type and 
                    abs(float(c_strike) - target_strike) <= strike_range):
                    nearby_contracts.append(c)
            
            # Sort by strike distance and try contracts until we find one with IV
            nearby_contracts.sort(key=lambda x: abs(float(x.get("strike_price", 0)) - target_strike))
            
            for alt_contract in nearby_contracts[:5]:  # Try up to 5 nearby contracts
                alt_symbol = alt_contract.get("ticker")
                if alt_symbol and alt_symbol != opt_symbol:
                    alt_iv = get_iv_from_snapshot(ticker, alt_symbol)
                    if alt_iv is not None:
                        print(f"✅ Found IV for {ticker} {direction} using alternative contract {alt_symbol} (strike {alt_contract.get('strike_price')})", file=sys.stderr)
                        return alt_iv, True  # Using alternative contract
            
        except Exception as e:
            print(f"⚠️ Error finding alternative contract for {opt_symbol}: {e}", file=sys.stderr)
        
        # No IV found in selected or nearby contracts
        print(f"⚠️ API did not return IV for {opt_symbol} or nearby contracts - contract may be illiquid or deep ITM/OTM", file=sys.stderr)
        return None, False

    iv_minus, alt_minus = get_iv_for_contract(minus_contract, "down")
    iv_plus, alt_plus = get_iv_for_contract(plus_contract, "up")
    
    # Log IV values for debugging
    if iv_minus is not None:
        print(f"  {ticker} -15% IV: {iv_minus:.2f}%", file=sys.stderr)
    if iv_plus is not None:
        print(f"  {ticker} +15% IV: {iv_plus:.2f}%", file=sys.stderr)
    
    # API-only: No symmetry fallback. If API doesn't provide IV for one side, it stays None.
    # This ensures you know when the API is not providing complete data.

    # Append IV history
    iv_values = [v for v in (iv_minus, iv_plus) if v is not None]
    if iv_values:
        avg_iv = sum(iv_values) / len(iv_values)
        append_iv_history(ticker, avg_iv)

    # --- Realized Volatility ---
    # Calculate RV from API-fetched equity data ONLY (API-only, no price_history.json)
    if not eq_aggs or not eq_aggs.get("results"):
        print(f"❌ No API equity data available for RV calculation for {ticker}", file=sys.stderr)
        rv_30d = None
        rv_ltm = None
    else:
        closes = [r["c"] for r in eq_aggs["results"] if r.get("c") is not None]
        if not closes:
            print(f"❌ No valid price data in API response for RV calculation for {ticker}", file=sys.stderr)
            rv_30d = None
            rv_ltm = None
        else:
            rv_30d = compute_realized_vol(closes, 30)
            # For LTM, use all available data if less than 252 days (for newer tickers)
            available_days = len(closes) - 1  # Need at least window + 1
            ltm_window = min(252, max(30, available_days)) if available_days >= 30 else None
            rv_ltm = compute_realized_vol(closes, ltm_window) if ltm_window else None

    # --- IV / RV Ratios ---
    # Use actual IV values (not display strings) for ratio calculations
    rank_30d_minus = safe_ratio(iv_minus, rv_30d)
    rank_30d_plus  = safe_ratio(iv_plus, rv_30d)
    rank_ltm_minus = safe_ratio(iv_minus, rv_ltm)
    rank_ltm_plus  = safe_ratio(iv_plus, rv_ltm)

    # --- 8-bin color classification ---
    def classify_ratio(r: Optional[float]) -> str:
        if r is None:
            return ""
        if r < 0.50: return "ratio-bin-1"
        if r < 0.75: return "ratio-bin-2"
        if r < 0.90: return "ratio-bin-3"
        if r < 1.05: return "ratio-bin-4"
        if r < 1.25: return "ratio-bin-5"
        if r < 1.50: return "ratio-bin-6"
        if r < 2.00: return "ratio-bin-7"
        return "ratio-bin-8"

    rank_30d_minus_cls = classify_ratio(rank_30d_minus)
    rank_30d_plus_cls  = classify_ratio(rank_30d_plus)
    rank_ltm_minus_cls = classify_ratio(rank_ltm_minus)
    rank_ltm_plus_cls  = classify_ratio(rank_ltm_plus)

    # --- Comments ---
    comments = []
    if out["Last Px"] >= 0.95 * out["52W High"]:
        comments.append("Price near 52W high")
    if out["Last Px"] <= 1.05 * out["52W Low"]:
        comments.append("Price near 52W low")

    # --- Final Output ---
    # Add asterisk to IV values that used alternative contracts
    iv_minus_display = f"{iv_minus}*" if (iv_minus is not None and alt_minus) else iv_minus
    iv_plus_display = f"{iv_plus}*" if (iv_plus is not None and alt_plus) else iv_plus
    
    # Track if any alternative contracts were used (for footer note)
    used_alternative = alt_minus or alt_plus
    
    # Extract expiry date from contracts
    # Both contracts should have the same expiry (they use the same target_expiry),
    # but we'll show both if they differ for transparency
    expiry_date_str = None
    minus_expiry = minus_contract["_expiry"].strftime("%Y-%m-%d") if (minus_contract and minus_contract.get("_expiry")) else None
    plus_expiry = plus_contract["_expiry"].strftime("%Y-%m-%d") if (plus_contract and plus_contract.get("_expiry")) else None
    
    if minus_expiry and plus_expiry:
        if minus_expiry == plus_expiry:
            expiry_date_str = minus_expiry  # Same expiry for both
        else:
            expiry_date_str = f"{minus_expiry} / {plus_expiry}"  # Different expiries (shouldn't happen)
    elif minus_expiry:
        expiry_date_str = minus_expiry
    elif plus_expiry:
        expiry_date_str = plus_expiry
    
    out.update({
        f"-{pct}% IV": iv_minus_display,
        f"+{pct}% IV": iv_plus_display,
        "_used_alternative_contract": used_alternative,  # Flag for template

        "30D RV": rv_30d,
        "LTM RV": rv_ltm,

        f"-{pct}% 30D Ratio": rank_30d_minus,
        f"-{pct}% 30D Ratio_cls": rank_30d_minus_cls,

        f"+{pct}% 30D Ratio": rank_30d_plus,
        f"+{pct}% 30D Ratio_cls": rank_30d_plus_cls,

        f"-{pct}% LTM Ratio": rank_ltm_minus,
        f"-{pct}% LTM Ratio_cls": rank_ltm_minus_cls,

        f"+{pct}% LTM Ratio": rank_ltm_plus,
        f"+{pct}% LTM Ratio_cls": rank_ltm_plus_cls,

        "Expiry Date": expiry_date_str,
        "Earnings": None,
        "Comment": "; ".join(comments) if comments else "Stable",
    })

    return out


# --- Orchestrator -----------------------------------------------------------

def build_dashboard() -> str:
    results: List[Dict[str, Any]] = []
    
    # Determine target expiry date ONCE for all tickers (ensures consistency)
    # Use monthly expiry closest to 90 DTE (Bloomberg shows April 17, 2026 = 79 DTE in their table)
    # Note: Bloomberg Quick Pricer can calculate IV for exact 90 DTE (April 28), but Polygon only has
    # actual contracts, so we must use a real expiry date (monthly = 3rd Friday)
    target_expiry = get_target_expiry_date(target_dte=90, use_exact_dte=False)
    if target_expiry:
        dte = (target_expiry.date() - dt.date.today()).days
        is_third_friday = target_expiry.weekday() == 4 and 15 <= target_expiry.day <= 21
        expiry_type = "3rd Friday Monthly" if is_third_friday else "Monthly (closest to 90 DTE)"
        print(f"📅 Using target expiry date: {target_expiry.date()} ({expiry_type}, DTE: {dte}, target was 90)", file=sys.stderr)
        print(f"   Note: Using monthly expiry closest to 90 DTE (options only expire on Fridays)", file=sys.stderr)
    else:
        print(f"⚠️ Could not determine target expiry date, will use per-ticker selection", file=sys.stderr)
    
    # Optional: legacy insights refresh (safe to keep / ignore if file missing)
    # Skip when packaged (PyInstaller) — no subprocess to run
    if not getattr(sys, "frozen", False):
        try:
            subprocess.run(
                ["python3", "insight_engine.py"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,  # keep error visibility
            )
        except Exception as e:
            print(f"⚠️ Failed to refresh insights: {e}")

    for t in TICKERS[:MAX_TICKERS]:
        try:
            results.append(process_ticker(t, target_expiry))
            # Delay between tickers to avoid rate limits
            # With 365-day requests, we need longer delays to avoid hitting per-minute limits
            time.sleep(3.0)
        except Exception as e:
            print(f"⚠️ Error processing {t}: {e}", file=sys.stderr)
            pct = int(MONEYNESS_PCT * 100)
            results.append({
                "Ticker": t,
                "Error": str(e),
                "Last Px": None, "1D %": None, "7D %": None,
                "3M High": None, "6M High": None, "52W High": None,
                "3M Low": None, "6M Low": None, "52W Low": None,
                f"-{pct}% IV": None,
                f"+{pct}% IV": None,
                "30D RV": None,
                "LTM RV": None,
                f"-{pct}% 30D Ratio": None,
                f"+{pct}% 30D Ratio": None,
                f"-{pct}% LTM Ratio": None,
                f"+{pct}% LTM Ratio": None,
                "Earnings": None,
                "Comment": "Error",
            })
    return json.dumps(results, indent=2)


if __name__ == "__main__":
    print(build_dashboard())
