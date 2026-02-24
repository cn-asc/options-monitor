"""
insight_engine_light.py

Lightweight headline relevance engine for the Options Dashboard.

Purpose:
--------
Given us_ranked_headlines.csv and a set of tickers + aliases,
identify the *single best* headline for each ticker where the
headline explicitly mentions the ticker symbol or one of its aliases.

Output:
-------
A DataFrame with:
    ticker
    headline
    url

This module is intentionally minimal:
- No sentiment
- No volatility scoring
- No macro propagation
- No LLMs
- Only explicit symbol/alias matching
"""

import json
import re
import string
from typing import Dict, List, Tuple

import pandas as pd
from dotenv import load_dotenv
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
load_dotenv(ENV_PATH)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
# Don't fail if OpenAI key is missing - headlines will just be filtered less intelligently


# ------------- LLM relevance filter --------------

from openai import OpenAI
# Only create client when key is set (avoids OpenAIError in packaged app when OPENAI_API_KEY is missing)
llm_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def is_headline_stock_moving(ticker: str, headline: str) -> bool:
    """
    Uses an LLM to determine if a headline is stock-moving.
    Fail-OPEN (YES) to avoid dropping everything.
    """
    prompt = f"""
You are a long/short equities analyst at Point72, a prestigious hedge fund known for their successful fundamental trading strategies.

Evaluate whether this headline is likely to move {ticker} stock
in the short term.

Return EXACTLY one word: YES or NO.
When unsure, answer YES.

Headline: "{headline}"
"""

    if not OPENAI_API_KEY or not llm_client:
        return True  # fail-open if no API key
    
    try:
        resp = llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=5,
            temperature=0,
        )

        content = resp.choices[0].message.content
        if not content:
            return True  # fail-open

        answer = content.strip().upper()
        return answer.startswith("Y")

    except Exception as e:
        print("LLM ERROR:", e)
        return True  # fail-open



# ---------------------------
# Config
# ---------------------------

DEFAULT_HEADLINES_CSV = "us_ranked_headlines.csv"
OUT_BY_TICKER = "predictions_by_ticker.csv"

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
    "CRCL": ["Circle Internet Group"],
    "META": ["Meta", "Facebook", "Instagram", "WhatsApp", "Threads", "Oculus"],
    "IBIT": ["Bitcoin"],
    "BRK.B": ["Berkshire", "Berkshire Hathaway", "Warren Buffett"],
}


# ---------------------------
# Helpers
# ---------------------------

def load_tickers_and_aliases() -> Tuple[List[str], Dict[str, List[str]]]:
    load_dotenv()
    tickers = os.getenv("TICKERS", "")
    tickers = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    if not tickers:
        tickers = list(DEFAULT_TICKER_ALIASES.keys())

    # Allow overriding/default aliases via env JSON
    alias_json = os.getenv("TICKER_ALIASES_JSON", "")
    if alias_json:
        try:
            env_aliases = json.loads(alias_json)
            env_aliases = {k.upper(): [str(x) for x in v] for k, v in env_aliases.items()}
        except Exception:
            env_aliases = {}
    else:
        env_aliases = {}

    aliases = DEFAULT_TICKER_ALIASES.copy()

    # merge overrides
    for tk, names in env_aliases.items():
        aliases.setdefault(tk, [])
        for name in names:
            if name not in aliases[tk]:
                aliases[tk].append(name)

    return tickers, aliases


def normalize(text: str) -> str:
    """Lowercase + strip punctuation for clean matching."""
    if not text:
        return ""
    text = text.lower()
    text = text.translate(str.maketrans({c: " " for c in string.punctuation}))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def match_tickers_in_title(title: str, tickers: List[str], aliases: Dict[str, List[str]]) -> List[str]:
    """
    Detect explicit company mentions using:
    - Exact ticker symbol words (GOOG, NVDA, META)
    - Alias exact words (Google, Alphabet)
    - Possessive alias (Google's, Nvidia's)
    - Alias + corporate suffix ("nvidia corp", "alphabet inc", "adobe systems")
    - Multi-word aliases ("Berkshire Hathaway")

    No macro/crypto propagation. Only explicit company references.
    """
    raw = title or ""
    text = normalize(raw)

    hits = []

    # Corporate suffixes (expandable)
    corp_suffixes = [
        "inc", "inc", "incorporated",
        "corp", "corp", "corporation",
        "co", "co", "company",
        "ltd", "llc", "plc",
        "nv", "ag", "sa",
        "group", "groups",
        "holdings", "holding",
        "systems", "platforms"
    ]

    for tk in tickers:
        tk_norm = tk.lower()

        # 1) Exact ticker symbol word match
        if tk != "COIN":
            if re.search(rf"\b{re.escape(tk_norm)}\b", text):
                hits.append(tk)
                continue

        # 2) Alias-based fuzzy matching
        for alias in aliases.get(tk, []):
            alias_norm = normalize(alias)

            # 2a) Exact alias match
            if re.search(rf"\b{re.escape(alias_norm)}\b", text):
                hits.append(tk)
                break

            # 2b) Alias possessive (e.g. "google's")
            if re.search(rf"\b{re.escape(alias_norm)}s\b", text):
                hits.append(tk)
                break

            # 2c) Alias + corporate suffix patterns (e.g. "nvidia corp", "alphabet inc")
            for suf in corp_suffixes:
                pattern = rf"\b{re.escape(alias_norm)} {suf}\b"
                if re.search(pattern, text):
                    hits.append(tk)
                    break
            else:
                continue  # continue alias loop
            break        # break out early if matched

            # 2d) Multi-word alias matching ("Berkshire Hathaway")
            # If alias contains spaces, treat it as phrase
            if " " in alias_norm:
                if alias_norm in text:
                    hits.append(tk)
                    break

    return hits


# ---------------------------
# Main logic
# ---------------------------

def score_headlines(
    headlines_csv: str = DEFAULT_HEADLINES_CSV,
    out_by_ticker: str = OUT_BY_TICKER
) -> pd.DataFrame:

    tickers, aliases = load_tickers_and_aliases()

    df = pd.read_csv(headlines_csv)

    # Ensure columns exist
    for col in ["title", "article_url"]:
        if col not in df.columns:
            df[col] = None

    # Use composite_score or fallback popularity measure
    if "composite_score" in df.columns:
        df["popularity"] = df["composite_score"].fillna(0.5)
    elif "relative_popularity" in df.columns:
        df["popularity"] = df["relative_popularity"].fillna(0.5)
    else:
        df["popularity"] = 0.5

    rows = []

    for _, r in df.iterrows():
        title = str(r.get("title", "") or "")
        url = r.get("article_url", None)
        popularity = float(r.get("popularity", 0.5))

        matched = match_tickers_in_title(title, tickers, aliases)
        if not matched:
            continue

        for tk in matched:
            # LLM second-stage filter: only keep if headline is ACTUALLY stock-moving
            if is_headline_stock_moving(tk, title):
                rows.append({
                    "ticker": tk,
                    "title": title,
                    "url": url,
                    "popularity": popularity
                })


    if rows:
        df_hits = pd.DataFrame(rows)

        # For each ticker, pick MOST POPULAR headline
        best = (
            df_hits.sort_values(["ticker", "popularity"], ascending=[True, False])
                  .groupby("ticker")
                  .head(1)
                  .reset_index(drop=True)
        )
    else:
        best = pd.DataFrame(columns=["ticker", "title", "url"])

    best.to_csv(out_by_ticker, index=False)
    return best


# CLI
if __name__ == "__main__":
    out = score_headlines()
    print(out)
