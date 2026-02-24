import os
import json
import math
import time
import sys
import datetime as dt
from typing import Dict, Any, List, Optional
import requests
from dotenv import load_dotenv
import subprocess
import statistics

"""
Options Dashboard (Hybrid, Free Tier Friendly)

- Equity data from Polygon (Basic plan): EOD aggregates for price & highs/lows.
- Per-moneyness IV (±X%) computed locally via Black–Scholes using EOD option prices
  from Polygon (Basic) for near-30D contracts.
- Realized Volatility (30D & LTM) computed from stored close history in price_history.json.
- RV "Rank" fields are *ratios*: IV / RV (e.g., -15% 30D RV Rank = (-15% IV) / (30D RV)).

Environment (.env):
  POLYGON_API_KEY=...
  TICKERS=COIN,MSTR,NVDA,...
  RISK_FREE_RATE=0.04
  MAX_TICKERS_PER_RUN=15
  MONEYNESS_PCT=0.15          # 15% OTM/ITM
"""

# --- Config & setup ---------------------------------------------------------
load_dotenv()
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "").strip()
TICKERS = [t.strip().upper() for t in os.getenv("TICKERS", "AAPL,MSFT,GOOG,META").split(",") if t.strip()]
RISK_FREE = float(os.getenv("RISK_FREE_RATE", "0.04"))  # fallback 4%
MAX_TICKERS = int(os.getenv("MAX_TICKERS_PER_RUN", str(len(TICKERS))))
MONEYNESS_PCT = float(os.getenv("MONEYNESS_PCT", "0.15"))
if MONEYNESS_PCT <= 0 or MONEYNESS_PCT > 0.5:
    MONEYNESS_PCT = 0.15

if not POLYGON_API_KEY:
    raise SystemExit("POLYGON_API_KEY missing in .env")

BASE_URL = "https://api.polygon.io"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "OptionsDashboard/1.0"})

# Storage for IV history (for future use)
IV_HISTORY_FILE = "iv_history.json"   # {ticker: [{"date": "YYYY-MM-DD", "iv": float}, ...]}
HISTORY_DAYS = 252                    # ~1y trading days for IV history

# Storage for price history (for RV)
PRICE_HISTORY_FILE = "price_history.json"  # {ticker: [{"date": "YYYY-MM-DD", "close": float}, ...]}

# Respect Basic plan: 5 calls/min. We'll pause ~13s per request to be safe.
RATE_PAUSE = 15


# --- Utilities --------------------------------------------------------------

def _sleep():
    time.sleep(RATE_PAUSE)


def get_json(url: str, params: Optional[Dict[str, Any]] = None, max_retries: int = 3) -> Dict[str, Any]:
    """
    Thin wrapper over requests + Polygon, with:
      - API key injection
      - basic retry on 429 / 5xx
      - per-call pause to respect Basic plan
    """
    if params is None:
        params = {}
    params["apiKey"] = POLYGON_API_KEY

    backoff = RATE_PAUSE
    last_err = None

    for attempt in range(max_retries):
        try:
            resp = SESSION.get(url, params=params, timeout=30)
            status = resp.status_code

            # Handle rate limiting and transient errors
            if status == 429 or 500 <= status < 600:
                last_err = RuntimeError(f"Polygon HTTP {status} on {url}")
                # exponential-ish backoff, but never less than RATE_PAUSE
                sleep_for = max(backoff, RATE_PAUSE)
                time.sleep(sleep_for)
                backoff *= 1.3
                continue

            resp.raise_for_status()
            data = resp.json()
            # normal pause after successful call
            _sleep()
            return data

        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(backoff)
                backoff *= 1.3
            else:
                break

    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


# Standard normal CDF using math.erf (no SciPy dependency)
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# Black–Scholes price (European), continuous dividend yield q (set q=0 if unknown)
def bs_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
    q: float = 0.0,
) -> float:
    if sigma <= 0 or T <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option_type.lower() == "call":
        return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * _norm_cdf(-d2) - S * math.exp(-q * T) * _norm_cdf(-d1)


def implied_vol(
    price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: str = "call",
    q: float = 0.0,
) -> Optional[float]:
    """
    Robust implied volatility solver.

    - First tries a bisection method on [1e-4, 3.0].
    - If the payoff is not bracketed (price outside BS bounds), falls back
      to a coarse grid search to get a "good enough" IV rather than None.
    """
    if price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None

    option_type = option_type.lower()

    def f(sigma: float) -> float:
        return bs_price(S, K, T, r, sigma, option_type, q) - price

    # --- Phase 1: try to bracket with bisection ---
    lo, hi = 1e-4, 3.0
    plo, phi = f(lo), f(hi)

    if plo * phi < 0:
        # Standard bisection
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            pmid = f(mid)
            if abs(pmid) < 1e-6:
                return mid
            if pmid * plo > 0:
                lo, plo = mid, pmid
            else:
                hi, phi = mid, pmid
        return 0.5 * (lo + hi)

    # --- Phase 2: fallback coarse grid search ---
    best_sigma = None
    best_err = float("inf")

    # try a range of vols from 5% to 300%
    for sigma in [0.05 * i for i in range(1, 61)]:  # 0.05, 0.10, ... 3.0
        val = f(sigma)
        err = abs(val)
        if err < best_err:
            best_err = err
            best_sigma = sigma

    # if we still can't match reasonably, bail
    if best_sigma is None:
        return None

    # rough sanity check: price error should not be insane
    # (allow up to ~5% of underlying or 0.10 absolute)
    tolerance = max(0.10, 0.05 * S)
    if best_err > tolerance:
        return None

    return best_sigma



# --- Polygon helpers --------------------------------------------------------

def get_equity_window(ticker: str, days_back: int = 370) -> Dict[str, Any]:
    today = dt.date.today()
    start = today - dt.timedelta(days=days_back)
    url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{today}"
    return get_json(url)


def equity_metrics_from_aggs(aggs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    res = aggs.get("results")
    if not res:
        return None
    closes = [r["c"] for r in res]
    highs = [r["h"] for r in res]
    lows = [r["l"] for r in res]

    last_px = closes[-1]
    one_day = ((closes[-1] - closes[-2]) / closes[-2] * 100.0) if len(closes) > 1 else 0.0
    seven_day = ((closes[-1] - closes[-7]) / closes[-7] * 100.0) if len(closes) > 7 else 0.0

    three_m_high = max(highs[-63:]) if len(highs) >= 63 else max(highs)
    six_m_high = max(highs[-126:]) if len(highs) >= 126 else max(highs)
    year_high = max(highs)

    three_m_low = min(lows[-63:]) if len(lows) >= 63 else min(lows)
    six_m_low = min(lows[-126:]) if len(lows) >= 126 else min(lows)
    year_low = min(lows)

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


def pick_contract_at_moneyness(
    ticker: str,
    spot: float,
    moneyness_pct: float,
    direction: str,
):
    """
    Robust ~30D option picker for ±X% moneyness.

    direction = "down" → target_strike = spot * (1 - X), prefer puts
    direction = "up"   → target_strike = spot * (1 + X), prefer calls

    The function:
      - pulls Polygon reference contracts
      - filters by desired type and DTE windows
      - scores by distance to target strike and 30D DTE
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
    params = {"underlying_ticker": ticker, "limit": 1000}

    data = get_json(url, params)
    contracts = data.get("results") or []
    if not contracts:
        return None

    today = dt.datetime.now(dt.UTC)

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
        return c

    enriched: List[Dict[str, Any]] = []
    for c in contracts:
        ec = enrich(c)
        if ec is not None:
            enriched.append(ec)

    if not enriched:
        return None

    # scoring: minimize strike distance (in percent) + weighted DTE distance from 30
    def score_contract(c: Dict[str, Any]) -> float:
        strike_err = abs(c["_strike"] - target_strike) / max(1.0, spot)
        dte_err = abs(c["_dte"] - 30) / 30.0
        # prioritize strike more than DTE
        return strike_err + 0.6 * dte_err

    # multi-pass filtering: progressively relax constraints
    def choose(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not candidates:
            return None
        return min(candidates, key=score_contract)

    # Pass 1: desired type, 10–60 DTE
    pass1 = [c for c in enriched
             if c["_type"] == desired_type and 10 <= c["_dte"] <= 60]
    best = choose(pass1)
    if best:
        return best

    # Pass 2: desired type, 5–90 DTE
    pass2 = [c for c in enriched
             if c["_type"] == desired_type and 5 <= c["_dte"] <= 90]
    best = choose(pass2)
    if best:
        return best

    # Pass 3: any type, 10–60 DTE
    pass3 = [c for c in enriched if 10 <= c["_dte"] <= 60]
    best = choose(pass3)
    if best:
        return best

    # Pass 4: any type, any positive DTE – just grab the closest
    return choose(enriched)


def get_option_prev_close(option_symbol: str) -> Optional[float]:
    """
    Try hard to get a reasonable last price for an option:
      1) /v2/aggs/ticker/{sym}/prev
      2) /v2/aggs/ticker/{sym}/range/1/day/... (last close in a short window)
      3) /v3/trades/{sym}/latest
    Returns None only if all three fail.
    """
    # 1) primary: /prev
    url_prev = f"{BASE_URL}/v2/aggs/ticker/{option_symbol}/prev"
    try:
        data = get_json(url_prev)
        res = data.get("results") or []
        if res and res[0].get("c") is not None:
            return float(res[0]["c"])
    except Exception:
        pass

    # 2) fallback: last few days' daily bars, take the latest close
    try:
        today = dt.date.today()
        start = today - dt.timedelta(days=5)
        url_range = f"{BASE_URL}/v2/aggs/ticker/{option_symbol}/range/1/day/{start}/{today}"
        data = get_json(url_range)
        res = data.get("results") or []
        if res:
            # last bar's close
            last = res[-1]
            if last.get("c") is not None:
                return float(last["c"])
    except Exception:
        pass

    # 3) fallback: latest trade
    try:
        url_trades = f"{BASE_URL}/v3/trades/{option_symbol}/latest"
        data = get_json(url_trades)
        # Polygon's latest trade schema: "results": {"p": price, ...}
        results = data.get("results") or {}
        px = results.get("p") or results.get("price")
        if px is not None:
            return float(px)
    except Exception:
        pass

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

def process_ticker(ticker: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"Ticker": ticker}

    # 1) Equity window
    eq_aggs = get_equity_window(ticker)
    eq = equity_metrics_from_aggs(eq_aggs)
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
            "Earnings": None,
            "Comment": "No data",
        })
        return out

    out.update(eq)

    # 2) Select ±X% moneyness option contracts
    spot = eq["Last Px"]
    append_price_history(ticker, spot)

    minus_contract = pick_contract_at_moneyness(ticker, spot, MONEYNESS_PCT, "down")
    plus_contract = pick_contract_at_moneyness(ticker, spot, MONEYNESS_PCT, "up")

    # Compute IV for each selected contract
    def compute_iv_for_contract(contract):
        if not contract:
            return None
        opt_symbol = contract.get("ticker")
        strike = float(contract.get("strike_price"))
        opt_type = contract.get("contract_type", "call").lower()
        expiry_str = contract.get("expiration_date")

        expiry = dt.datetime.fromisoformat(expiry_str)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=dt.UTC)
        today_dt = dt.datetime.now(dt.UTC)
        T = max((expiry - today_dt).days, 1) / 365.0

        opt_close = get_option_prev_close(opt_symbol)
        if not opt_close:
            return None

        vol = implied_vol(opt_close, spot, strike, T, RISK_FREE, opt_type)
        return round(vol * 100, 2) if vol else None

    iv_minus = compute_iv_for_contract(minus_contract)
    iv_plus = compute_iv_for_contract(plus_contract)
    # --- Symmetry fallback for missing IVs ---------------------------------
    # If only one side's IV could be computed, use it for both ±moneyness.
    # This avoids random "None" entries when one chosen contract is illiquid
    # or missing Polygon prices, while the other side is fine.
    if iv_minus is None and iv_plus is not None:
        iv_minus = iv_plus
    elif iv_plus is None and iv_minus is not None:
        iv_plus = iv_minus


    # Append IV history
    iv_values = [v for v in (iv_minus, iv_plus) if v is not None]
    if iv_values:
        avg_iv = sum(iv_values) / len(iv_values)
        append_iv_history(ticker, avg_iv)

    # --- Realized Volatility ---
    price_hist = load_price_history().get(ticker, [])
    closes = [row["close"] for row in price_hist]

    rv_30d = compute_realized_vol(closes, 30)
    rv_ltm = compute_realized_vol(closes, 252)

    # --- IV / RV Ratios ---
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
    out.update({
        f"-{pct}% IV": iv_minus,
        f"+{pct}% IV": iv_plus,

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

        "Earnings": None,
        "Comment": "; ".join(comments) if comments else "Stable",
    })

    return out


# --- Orchestrator -----------------------------------------------------------

def build_dashboard() -> str:
    results: List[Dict[str, Any]] = []
    # Optional: legacy insights refresh (safe to keep / ignore if file missing)
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
            results.append(process_ticker(t))
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
