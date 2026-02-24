"""
Predictive insights from Google News headlines for a target ticker set.

Inputs
------
1) .env with: TICKERS
   (Optional) TICKER_ALIASES_JSON='{"GOOG":["Google","Alphabet"],"NVDA":["NVIDIA","GeForce"]}'
2) us_ranked_headlines.csv from your aggregator (columns may include:
   title, source, article_url, topic, rank_within_feed, relative_popularity,
   feed_count, composite_score, timestamp). Missing columns are handled.

Outputs
-------
- predictions_by_headline.csv : one row per (headline → ticker) prediction
- predictions_by_ticker.csv   : aggregated view per ticker

Install
-------
pip install python-dotenv pandas numpy nltk
python -c "import nltk; nltk.download('vader_lexicon')"
"""

import os
import json
import math
import datetime as dt
from typing import Dict, List, Tuple
import re
import string

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from nltk.sentiment import SentimentIntensityAnalyzer

# --------- CONFIG ---------
DEFAULT_HEADLINES_CSV = "us_ranked_headlines.csv"
OUT_PER_HEADLINE = "predictions_by_headline.csv"
OUT_BY_TICKER = "predictions_by_ticker.csv"

# Source credibility (tune as you like; 1.0 baseline)
SOURCE_WEIGHTS = {
    "Reuters": 1.20,
    "Bloomberg": 1.20,
    "Financial Times": 1.15,
    "Wall Street Journal": 1.15,
    "WSJ": 1.15,
    "CNBC": 1.10,
    "MarketWatch": 1.05,
    "Yahoo Finance": 1.00,
    "The Verge": 0.95,
    "TechCrunch": 0.95,
    "Twitter": 0.80,
    "X": 0.80,
    "Truth Social": 0.70,
}

# Event taxonomy (keywords → canonical event type)
EVENT_KEYWORDS: Dict[str, List[str]] = {
    "earnings_positive": ["beats estimates", "tops estimates", "record revenue", "raises guidance",
                          "profit surges", "better-than-expected", "above expectations"],
    "earnings_negative": ["misses estimates", "cuts guidance", "warns", "profit falls",
                          "below expectations", "disappoints"],
    "regulatory_positive": ["approval", "cleared", "wins case", "lawsuit dismissed", "antitrust approval"],
    "regulatory_negative": ["probe", "investigation", "sec charges", "antitrust suit", "fine", "ban", "blocked"],
    "mna_positive": ["acquires", "buy", "merger", "takeover", "strategic investment", "partnership"],
    "mna_negative": ["deal collapses", "walks away", "antitrust challenge to deal", "blocked deal"],
    "product_positive": ["launches", "unveils", "announces new", "breakthrough", "partnership", "integration"],
    "product_negative": ["recall", "outage", "security breach", "vulnerability", "data leak", "downtime"],
    "macro_positive": ["rate cut", "inflation cools", "soft landing", "jobs beat"],
    "macro_negative": ["rate hike", "inflation heats", "recession fears", "jobless claims surge", "yields spike"],
    "crypto_positive": ["bitcoin surges", "btc jumps", "etf approval", "spot etf inflows"],
    "crypto_negative": ["bitcoin plunges", "btc drops", "etf outflows", "crypto crackdown"],
}

# Base volatility impact by event type (in daily bps, rough priors; tune with history)
EVENT_BASE_VOL_BPS = {
    "earnings_positive": 250, "earnings_negative": 300,
    "regulatory_positive": 180, "regulatory_negative": 260,
    "mna_positive": 220, "mna_negative": 240,
    "product_positive": 150, "product_negative": 200,
    "macro_positive": 120, "macro_negative": 160,
    "crypto_positive": 250, "crypto_negative": 280,
    "generic": 100,
}

# Ticker default aliases (seeded; extend via .env JSON)
DEFAULT_TICKER_ALIASES = {
    "COIN": ["Coinbase"],
    "MSTR": ["MicroStrategy"],
    "NVDA": ["NVIDIA", "GeForce", "CUDA"],
    "FICO": ["Fair Isaac", "FICO"],
    "KLAC": ["KLA", "KLA-Tencor"],
    "GOOG": ["Alphabet", "Google", "YouTube"],
    "ADBE": ["Adobe", "Photoshop", "Acrobat", "Firefly"],
    "FTNT": ["Fortinet", "FortiGate"],
    "LRCX": ["Lam Research"],
    "CRCL": ["Circle Internet Group"],  # unknown common alias; override via env
    "META": ["Meta", "Facebook", "Instagram", "WhatsApp", "Threads", "Oculus"],
    "IBIT": ["Bitcoin"],
    "BRK.B": ["Berkshire", "Berkshire Hathaway", "Warren Buffet"],
}

# Macro tickers sensitivity (heuristics to tie macro/crypto headlines to single-name effects)
MACRO_SENSITIVITY = {
    "NVDA": {"macro_positive": +0.5, "macro_negative": -0.5},
    "GOOG": {"macro_positive": +0.3, "macro_negative": -0.3},
    "META": {"macro_positive": +0.3, "macro_negative": -0.3},
    "ADBE": {"macro_positive": +0.2, "macro_negative": -0.2},
    "KLAC": {"macro_positive": +0.4, "macro_negative": -0.4},
    "LRCX": {"macro_positive": +0.4, "macro_negative": -0.4},
    "FTNT": {"macro_positive": +0.2, "macro_negative": -0.2},
    "FICO": {"macro_positive": +0.1, "macro_negative": -0.1},
    "COIN": {"crypto_positive": +1.0, "crypto_negative": -1.0, "macro_negative": -0.3},
    "MSTR": {"crypto_positive": +1.2, "crypto_negative": -1.2, "macro_negative": -0.2},
    "CRCL": {"crypto_positive": +0.8, "crypto_negative": -0.8},
}

# -------------------------------------------------------------

def load_config_from_env() -> Tuple[List[str], Dict[str, List[str]]]:
    load_dotenv()
    tickers = os.getenv("TICKERS", "")
    tickers = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not tickers:
        tickers = list(DEFAULT_TICKER_ALIASES.keys())

    aliases_json = os.getenv("TICKER_ALIASES_JSON", "")
    if aliases_json:
        try:
            env_aliases = json.loads(aliases_json)
            # normalize to lists
            env_aliases = {k.upper(): [str(x) for x in v] for k, v in env_aliases.items()}
        except Exception:
            env_aliases = {}
    else:
        env_aliases = {}

    # merge defaults + env
    aliases = DEFAULT_TICKER_ALIASES.copy()
    for t, names in env_aliases.items():
        aliases.setdefault(t, [])
        for n in names:
            if n not in aliases[t]:
                aliases[t].append(n)
    # make sure every ticker has an alias list
    for t in tickers:
        aliases.setdefault(t, [])
    return tickers, aliases

def load_headlines(path: str = DEFAULT_HEADLINES_CSV) -> pd.DataFrame:
    df = pd.read_csv(path)
    # normalize expected columns
    for col in ["title", "source", "article_url"]:
        if col not in df.columns:
            df[col] = None
    # ranking features (optional)
    if "composite_score" not in df.columns:
        # fallback from other hints
        if "relative_popularity" in df.columns:
            df["composite_score"] = df["relative_popularity"]
        else:
            df["composite_score"] = 0.5  # neutral if unknown
    # timestamp normalization
    if "timestamp" in df.columns:
        try:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
        except Exception:
            pass
    else:
        df["timestamp"] = pd.Timestamp.utcnow()
    return df

def build_event_classifier() -> Tuple[Dict[str, List[str]], SentimentIntensityAnalyzer]:
    sia = SentimentIntensityAnalyzer()
    # lowercase all keyword lists for matching
    lowered = {k: [w.lower() for w in v] for k, v in EVENT_KEYWORDS.items()}
    return lowered, sia

MACRO_CONTEXT = [
    "inflation", "fed", "interest rates", "treasury", "bond yields",
    "jobs report", "cpi", "ppi", "gdp", "recession", "economy"
]

def detect_event_types(title: str, lowered_keywords: Dict[str, List[str]]) -> List[str]:
    text = (title or "").lower()
    hits = []
    for etype, kws in lowered_keywords.items():
        if any(kw in text for kw in kws):
            hits.append(etype)
    if not hits:
        # infer macro/crypto generic mentions
        if any(x in text for x in MACRO_CONTEXT):
            hits.append("macro_positive" if any(x in text for x in ["cool", "eases", "falls", "cut"]) else "macro_negative")
        if any(x in text for x in ["bitcoin", "btc", "crypto", "ethereum", "etf"]):
            # choose positive/negative by verb if present
            if any(x in text for x in ["surge", "spike", "jumps", "rallies", "approval", "inflows"]):
                hits.append("crypto_positive")
            elif any(x in text for x in ["plunge", "drop", "sinks", "selloff", "outflows", "crackdown"]):
                hits.append("crypto_negative")
    return hits or ["generic"]

def normalize_title(s: str) -> str:
    """Lowercase, strip punctuation, normalize spaces."""
    if not s:
        return ""
    s = s.lower()
    # Replace punctuation with spaces
    s = s.translate(str.maketrans({c: " " for c in string.punctuation}))
    # Collapse multiple spaces
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def match_tickers(title: str, tickers: List[str], aliases: Dict[str, List[str]]) -> List[str]:
    """
    Return tickers whose symbol/aliases appear in the title.

    Features:
    - Symbol as standalone word: GOOG, NVDA
    - Symbol possessive: GOOG's, NVDA’s
    - Alias match: Google, Alphabet, Nvidia, Berkshire Hathaway
    - Multi-word alias match: Berkshire Hathaway, Circle Internet Group
    - Alias possessive: Berkshire Hathaway's
    - Avoids substring false positives: META vs 'metaverse'
    """
    t_norm = normalize_title(title or "")
    tokens = t_norm.split()

    hits: List[str] = []

    for tk in tickers:
        tk_norm = normalize_title(tk)  # usually same, added for consistency

        # --- 1) Match ticker symbol ---
        # exact: GOOG
        if tk_norm in tokens:
            hits.append(tk)
            continue

        # possessive: GOOGs (from "GOOG's")
        if tk_norm + "s" in tokens:
            hits.append(tk)
            continue

        # --- 2) Match aliases ---
        for alias in aliases.get(tk, []):
            alias_norm = normalize_title(alias)

            # Multi-word alias: "berkshire hathaway"
            alias_tokens = alias_norm.split()
            n = len(alias_tokens)

            # sliding window comparison for multi-word aliases
            for i in range(len(tokens) - n + 1):
                window = tokens[i:i + n]

                # exact multi-word alias
                if window == alias_tokens:
                    hits.append(tk)
                    break

                # possessive: alias + 's' at the end
                if i + n <= len(tokens):
                    last_word = tokens[i + n - 1]
                    if (window[:-1] == alias_tokens[:-1]) and last_word == (alias_tokens[-1] + "s"):
                        hits.append(tk)
                        break

            if tk in hits:
                break  # no need to check more aliases

    return hits



def direction_from_sentiment(sia: SentimentIntensityAnalyzer, text: str) -> Tuple[float, int]:
    """Return sentiment_score (-1..+1) and sentiment_sign (-1/0/+1)."""
    vs = sia.polarity_scores(text or "")
    score = vs["compound"]  # -1..+1
    sign = 0
    if score > 0.1:
        sign = +1
    elif score < -0.1:
        sign = -1
    return score, sign

def infer_direction_from_event(events: List[str]) -> int:
    if any(e.endswith("positive") for e in events):
        return +1
    if any(e.endswith("negative") for e in events):
        return -1
    return 0

def expected_volatility_bps(events: List[str], popularity: float, source_w: float, sentiment_mag: float) -> int:
    base = max(EVENT_BASE_VOL_BPS.get(events[0], EVENT_BASE_VOL_BPS["generic"]), 60)
    # amplify by popularity (0..1.5) and source quality (0.7..1.2) and sentiment magnitude (|score|)
    pop_factor = 0.7 + 0.8 * min(max(popularity, 0.0), 1.0)   # 0.7..1.5
    src_factor = max(0.7, min(source_w, 1.3))
    sent_factor = 0.8 + 0.6 * min(abs(sentiment_mag), 1.0)    # 0.8..1.4
    return int(round(base * pop_factor * src_factor * sent_factor))

def confidence_score(mapped: bool, feed_count: float, source_w: float, popularity: float) -> float:
    # 0..1 heuristic
    c = 0.25
    if mapped: c += 0.25
    c += 0.2 * min(feed_count or 1.0, 3.0) / 3.0
    c += 0.2 * min(source_w, 1.2) / 1.2
    c += 0.1 * min(popularity or 0.5, 1.0)
    return round(min(c, 0.99), 2)

def source_weight(name: str) -> float:
    if not name:
        return 1.0
    return SOURCE_WEIGHTS.get(name, 1.0)

def score_headlines(
    headlines_csv: str = DEFAULT_HEADLINES_CSV,
    out_per_headline: str = OUT_PER_HEADLINE,
    out_by_ticker: str = OUT_BY_TICKER,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    tickers, aliases = load_config_from_env()
    df = load_headlines(headlines_csv)
    kw, sia = build_event_classifier()

    # Normalize helper cols
    if "feed_count" not in df.columns: df["feed_count"] = 1.0
    for pcol in ["composite_score", "relative_popularity"]:
        if pcol not in df.columns: df[pcol] = 0.5

    rows = []
    for _, r in df.iterrows():
        title = str(r.get("title", "") or "")
        src = r.get("source", None)
        url = r.get("article_url", None)
        popularity = float(r.get("composite_score", r.get("relative_popularity", 0.5)) or 0.5)
        fcount = float(r.get("feed_count", 1.0) or 1.0)

        events = detect_event_types(title, kw)
        sent_score, sent_sign = direction_from_sentiment(sia, title)
        event_sign = infer_direction_from_event(events)

        # Final direction sign: blend event signal with sentiment; tie → sentiment
        final_sign = event_sign if event_sign != 0 else sent_sign

        src_w = source_weight(src)

        # Primary mapping: explicit name/alias matches ONLY
        matched = match_tickers(title, tickers, aliases)

        if not matched:
            # No explicit company/ticker mention → we ignore this headline for per-ticker view
            continue

        # Build rows for explicit matches
        for tk in matched:
            vol_bps = expected_volatility_bps(events, popularity, src_w, sent_score)
            conf = confidence_score(True, fcount, src_w, popularity)
            rows.append({
                "timestamp": r.get("timestamp"),
                "ticker": tk,
                "headline_title": title,
                "source": src,
                "headline_url": url,
                "events": ",".join(events),
                "sentiment_score": round(sent_score, 3),
                "popularity_score": round(popularity, 3),
                "feed_count": int(fcount),
                "direction": int(final_sign),     # -1, 0, +1
                "expected_vol_bps": int(vol_bps), # daily basis points
                "confidence": conf,
                "rationale": f"Explicit match via alias/symbol; events={events}; src_w={src_w:.2f}",
            })


    per_headline = pd.DataFrame(rows)
    if not per_headline.empty:
        # Aggregate to ticker level: weighted by confidence and popularity
        per_headline["w"] = per_headline["confidence"] * (0.5 + per_headline["popularity_score"])
        agg = per_headline.groupby("ticker").apply(lambda g: pd.Series({
            "n_signals": len(g),
            "bull_score": float((g.loc[g["direction"] > 0, "w"]).sum()),
            "bear_score": float((g.loc[g["direction"] < 0, "w"]).sum()),
            "net_signal": float((g["direction"] * g["w"]).sum()),
            "avg_expected_vol_bps": int(np.average(g["expected_vol_bps"], weights=np.maximum(g["w"], 1e-6))),
            "top_headline": g.sort_values(["confidence", "popularity_score"], ascending=False).iloc[0]["headline_title"],
            "top_headline_url": g.sort_values(["confidence", "popularity_score"], ascending=False).iloc[0]["headline_url"],
        })).reset_index()

        # Normalize net signal to -1..+1 for easy UI
        max_abs = float(agg["net_signal"].abs().max() or 1.0)
        agg["net_signal_normalized"] = (agg["net_signal"] / max_abs).round(3)
        agg = agg.sort_values("net_signal_normalized", ascending=False)

    else:
        agg = pd.DataFrame(columns=[
            "ticker","n_signals","bull_score","bear_score","net_signal",
            "avg_expected_vol_bps","top_headline","top_headline_url","net_signal_normalized"
        ])

    # Save
    per_headline.to_csv(OUT_PER_HEADLINE, index=False)
    agg.to_csv(OUT_BY_TICKER, index=False)
    return per_headline, agg

# CLI
if __name__ == "__main__":
    ph, ag = score_headlines()
    print("\n=== Per-headline predictions (sample) ===")
    print(ph.head(10))
    print("\n=== Per-ticker rollup ===")
    print(ag)
