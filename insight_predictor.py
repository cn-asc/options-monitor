import os
import json
import time
import datetime as dt
from typing import Dict, List, Tuple

# ---- deps ----
# pip install python-dotenv pandas numpy feedparser nltk
import feedparser
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from nltk.sentiment import SentimentIntensityAnalyzer
import nltk
import requests
from openai import OpenAI

# ----------------- CONFIG -----------------
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TICKERS = [t.strip().upper() for t in os.getenv("TICKERS", "").split(",") if t.strip()]
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_ENABLED = bool(OPENAI_API_KEY)
if LLM_ENABLED:
    client = OpenAI(api_key=OPENAI_API_KEY)
else:
    client = None

REGION = "US"
LANG = "en"
TOPICS = ["TOP", "BUSINESS", "WORLD", "TECHNOLOGY"]
MAX_HEADLINES_PER_FEED = 50
MAX_HEADLINES_TO_ANALYZE = 20
OUTPUT_JSON = "predicted_market_impacts.json"
DEBUG = True

# ---- Source weights / aliases / event taxo ----
SOURCE_WEIGHTS = {
    "Reuters": 1.20, "Bloomberg": 1.30, "Financial Times": 1.15,
    "Wall Street Journal": 1.2, "WSJ": 1.2, "CNBC": 1.10, "New York Times": 1.0, "NYT": 1.0,
    "MarketWatch": 1.0, "Yahoo Finance": 1.00, "The Verge": 0.95,
    "TechCrunch": 0.95, "Twitter": 1.2, "X": 1.2, "Truth Social": 0.70,
}
EVENT_KEYWORDS: Dict[str, List[str]] = {
    "earnings_positive": ["beats estimates", "tops estimates", "record revenue", "raises guidance",
                          "profit surges", "better-than-expected", "above expectations"],
    "earnings_negative": ["misses estimates", "cuts guidance", "warns", "profit falls",
                          "below expectations", "disappoints"],
    "regulatory_positive": ["approval", "cleared", "wins case", "lawsuit dismissed", "antitrust approval"],
    "regulatory_negative": ["probe", "investigation", "sec charges", "antitrust suit", "fine", "ban", "blocked"],
    "mna_positive": ["acquires", "buy", "merger", "takeover", "strategic investment", "partnership"],
    "mna_negative": ["deal collapses", "walks away", "antitrust challenge to deal", "blocked deal"],
    "product_positive": ["launches", "unveils", "announces new", "breakthrough", "integration"],
    "product_negative": ["recall", "outage", "security breach", "vulnerability", "data leak", "downtime"],
    "macro_positive": ["rate cut", "inflation cools", "soft landing", "jobs beat"],
    "macro_negative": ["rate hike", "inflation heats", "recession fears", "jobless claims surge", "yields spike"],
    "crypto_positive": ["bitcoin surges", "btc jumps", "etf approval", "spot etf inflows"],
    "crypto_negative": ["bitcoin plunges", "btc drops", "etf outflows", "crypto crackdown"],
}
EVENT_BASE_VOL_BPS = {
    "earnings_positive": 250, "earnings_negative": 300,
    "regulatory_positive": 180, "regulatory_negative": 260,
    "mna_positive": 220, "mna_negative": 240,
    "product_positive": 150, "product_negative": 200,
    "macro_positive": 120, "macro_negative": 160,
    "crypto_positive": 250, "crypto_negative": 280,
    "generic": 100,
}
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
    "CRCL": ["Circle"],
    "META": ["Meta", "Facebook", "Instagram", "WhatsApp", "Threads", "Oculus"],
}
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

# -------------- helpers --------------

def log(msg):
    if DEBUG:
        print(msg)

def ensure_vader():
    try:
        nltk.data.find('sentiment/vader_lexicon.zip')
    except LookupError:
        nltk.download('vader_lexicon')

def load_config_from_env() -> Tuple[List[str], Dict[str, List[str]]]:
    tickers_raw = os.getenv("TICKERS", "")
    tickers = [t.strip().upper() for t in tickers_raw.split(",") if t.strip()] or list(DEFAULT_TICKER_ALIASES.keys())
    aliases_json = os.getenv("TICKER_ALIASES_JSON", "")
    env_aliases = {}
    if aliases_json:
        try:
            env_aliases = {k.upper(): [str(x) for x in v] for k, v in json.loads(aliases_json).items()}
        except Exception:
            log("WARN: failed to parse TICKER_ALIASES_JSON; using defaults only.")
    aliases = DEFAULT_TICKER_ALIASES.copy()
    for k, v in env_aliases.items():
        aliases.setdefault(k, [])
        for a in v:
            if a not in aliases[k]:
                aliases[k].append(a)
    for t in tickers:
        aliases.setdefault(t, [])
    return tickers, aliases

def build_feed_url(topic: str, region: str = REGION, lang: str = LANG) -> str:
    if topic.upper() == "TOP":
        return f"https://news.google.com/rss?hl={lang}-{region}&gl={region}&ceid={region}:{lang}"
    return f"https://news.google.com/rss/headlines/section/topic/{topic.upper()}?hl={lang}-{region}&gl={region}&ceid={region}:{lang}"

def fetch_google_news_feeds(topics=TOPICS) -> pd.DataFrame:
    records = []
    for topic in topics:
        url = build_feed_url(topic)
        log(f"Fetching {topic} feed: {url}")
        feed = feedparser.parse(url)
        if not feed or not getattr(feed, "entries", None):
            log(f"  -> EMPTY feed for {topic}")
            continue
        for rank, e in enumerate(feed.entries[:MAX_HEADLINES_PER_FEED], start=1):
            rel_pop = (MAX_HEADLINES_PER_FEED - rank + 1) / MAX_HEADLINES_PER_FEED
            src = None
            if hasattr(e, "source") and e.source:
                src = getattr(e.source, "title", None)
            elif hasattr(e, "author_detail") and e.author_detail:
                src = getattr(e.author_detail, "name", None)
            pub_time = dt.datetime.fromtimestamp(time.mktime(e.published_parsed)) if hasattr(e, "published_parsed") and e.published_parsed else dt.datetime.utcnow()
            records.append({
                "topic": topic,
                "title": e.title,
                "source": src,
                "article_url": e.link,
                "rank_within_feed": rank,
                "relative_popularity": rel_pop,
                "feed_pub_time": pub_time.isoformat(),
                "timestamp": dt.datetime.utcnow().isoformat()
            })
    df = pd.DataFrame(records)
    if df.empty:
        return df
    dup_counts = df.groupby("title")["topic"].count().rename("feed_count")
    df = df.merge(dup_counts, on="title")
    df["composite_score"] = df["relative_popularity"] * (1 + 0.2 * (df["feed_count"] - 1))
    df = df.sort_values("composite_score", ascending=False).drop_duplicates("title").reset_index(drop=True)
    log(f"Fetched {len(df)} unique headlines across {len(topics)} feeds.")
    return df

# ---- new llm helper ----
def llm_analyze_headline(headline: str, tickers: List[str], aliases: dict) -> dict:
    """
    LLM determines which tickers are impacted by this headline.
    Returns {ticker: {"direction": str, "expected_vol_bps": int, "score": float, "rationale": str}}.
    """
    if not LLM_ENABLED or not OPENAI_API_KEY:
        return {}
    alias_lines = "\n".join([f"- {t}: {', '.join(aliases.get(t, []))}" for t in tickers])
    prompt = f"""
    You are a senior portfolio manager at Citadel Securities and hold a PhD in Finance from MIT. 
    Your whose sole focus is to read and interpret news headlines through the lens of market impact. 
    You deeply understand your entire coverage universe—every stock, sector, and interconnected company within it:{alias_lines}. 
    Your sole purpose is to evaluate whether a given headline will move a stock or group of stocks, and why. 
    You are precise, deeply analytical, and intuitive about second- and third-order effects in the market. 
    Even obscure or tangential news items are analyzed for their potential causality on equities, sectors, or macro dynamics. 
    You never miss a potential linkage. When information is insufficient, you hypothesize plausible connections and notes confidence levels. 
    The tone should reflect the gravitas and intensity of someone whose career depends on accurate judgment, with an emphasis on decisiveness, market intuition, and rigor.

    Headline: "{headline}"

    Coverage universe and aliases:
    {alias_lines}

    Scoring guidance:
    - 0.0 → negligible or speculative connection
    - 0.1–0.5 → indirect or sector-wide relevance
    - 0.6–0.8 → directly relevant, likely short-term move
    - 0.9–1.0 → highly impactful or breaking event for that stock

    Respond only in JSON with this structure:
    {{
      "TICKER": {{
         "direction": "up" | "down" | "neutral",
         "expected_vol_bps": int,
         "score": float,
         "rationale": "short reason (<60 chars)"
      }},
      ...
    }}
    Only include tickers that may be meaningfully impacted.
    """
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": "Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=700,
        )
        text = resp.choices[0].message.content.strip()
        start, end = text.find("{"), text.rfind("}")
        parsed = json.loads(text[start:end + 1])
        return parsed
    except Exception as e:
        print(f"[LLM DEBUG] headline analysis failed: {type(e).__name__}: {e}")
        return {}

# ---- sentiment / event helpers ----
def build_event_classifier():
    ensure_vader()
    sia = SentimentIntensityAnalyzer()
    lowered = {k: [w.lower() for w in v] for k, v in EVENT_KEYWORDS.items()}
    return lowered, sia

def detect_event_types(title: str, lowered_keywords: Dict[str, List[str]]) -> List[str]:
    t = (title or "").lower()
    hits = [etype for etype, kws in lowered_keywords.items() if any(kw in t for kw in kws)]
    MACRO_CONTEXT = ["inflation", "fed", "interest rates", "rate hike", "rate cut", "treasury", "bond yields", "jobs report", "cpi", "ppi", "gdp", "recession", "economy", "market", "nasdaq", "dow", "s&p", "wall street"]
    if not hits:
        if any(k in t for k in MACRO_CONTEXT):
            hits.append("macro_positive" if any(x in t for x in ["cut", "eases", "falls", "cool"]) else "macro_negative")
        elif any(x in t for x in ["bitcoin", "btc", "crypto", "ethereum", "etf"]):
            hits.append("crypto_positive" if any(x in t for x in ["surge", "spike", "jumps", "rallies", "approval", "inflows"]) else "crypto_negative")
    return hits or ["generic"]

def direction_from_sentiment(sia: SentimentIntensityAnalyzer, text: str) -> Tuple[float, int]:
    vs = sia.polarity_scores(text or "")
    score = vs["compound"]
    sign = +1 if score > 0.1 else -1 if score < -0.1 else 0
    return score, sign

def infer_direction_from_event(events: List[str]) -> int:
    if any(e.endswith("positive") for e in events): return +1
    if any(e.endswith("negative") for e in events): return -1
    return 0

def source_weight(name: str) -> float:
    return SOURCE_WEIGHTS.get(name, 1.0) if name else 1.0

def expected_volatility_bps(events: List[str], popularity: float, source_w: float, sentiment_mag: float) -> int:
    base = max(EVENT_BASE_VOL_BPS.get(events[0], EVENT_BASE_VOL_BPS["generic"]), 60)
    pop_factor = 0.7 + 0.8 * min(max(popularity, 0.0), 1.0)
    src_factor = max(0.7, min(source_w, 1.3))
    sent_factor = 0.8 + 0.6 * min(abs(sentiment_mag), 1.0)
    return int(round(base * pop_factor * src_factor * sent_factor))

# -------------- MAIN --------------

def analyze():
    tickers, aliases = load_config_from_env()
    log(f"TICKERS: {tickers}")
    df = fetch_google_news_feeds()
    if df.empty:
        log("ERROR: No headlines fetched. Exiting.")
        return []
    top_df = df.head(MAX_HEADLINES_TO_ANALYZE).copy()
    log(f"Analyzing {len(top_df)} headlines...")
    lowered_kw, sia = build_event_classifier()
    out = []

    for _, r in top_df.iterrows():
        title = r["title"]
        src = r.get("source", None)
        popularity = float(r.get("composite_score", r.get("relative_popularity", 0.5)) or 0.5)
        url = r["article_url"]
        log(f"\n→ [{src}] {title}")

        # 1️⃣ LLM first
        llm_preds = llm_analyze_headline(title, tickers, aliases)
        log(f"[LLM RAW OUTPUT] {llm_preds}")


        # 2️⃣ Python analysis
        events = detect_event_types(title, lowered_kw)
        sent_score, sent_sign = direction_from_sentiment(sia, title)
        event_sign = infer_direction_from_event(events)
        final_sign = event_sign if event_sign != 0 else sent_sign
        direction_label = "up" if final_sign > 0 else "down" if final_sign < 0 else "neutral"
        src_w = source_weight(src)
        vol_bps = expected_volatility_bps(events, popularity, src_w, sent_score)

        # 3️⃣ Merge LLM + Python signals
        llm_tickers = set(llm_preds.keys())
        python_hits = set(
            tk for tk, names in aliases.items()
            if tk.lower() in title.lower() or any(a.lower() in title.lower() for a in names)
        )
        combined_hits = llm_tickers | python_hits

        for tk in combined_hits:
            llm_entry = llm_preds.get(tk)
            llm_vol = llm_entry.get("expected_vol_bps", vol_bps) if llm_entry else vol_bps
            avg_vol = int(round((vol_bps + llm_vol) / 2)) if llm_entry else vol_bps
            direction = llm_entry.get("direction", direction_label) if llm_entry else direction_label
            score = llm_entry.get("score", 1.0) if llm_entry else 0.0
            rationale = llm_entry.get("rationale", "merged") if llm_entry else "LLM not used for this name"

            out.append({
                "Stock Ticker": tk,
                "Impact": {"direction": direction, "expected_vol_bps": avg_vol},
                "Headline": title,
                "Meat": None,
                "Publication": src,
                "Popularity": round(popularity, 3),
                "URL": url,
                "Published": str(r.get("feed_pub_time", "")),
                "Timestamp": dt.datetime.utcnow().isoformat(),
                "LLMRelevance": {"score": score, "rationale": rationale},
            })
            log(f"   ✓ {tk}: {direction}, ~{avg_vol} bps (LLM={score:.2f}); pop={popularity:.2f}")
        time.sleep(0.5)

    return out

if __name__ == "__main__":
    try:
        results = analyze()
        with open(OUTPUT_JSON, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\n✅ Wrote {len(results)} predictions to {OUTPUT_JSON}")
        if DEBUG and results[:3]:
            print("\nSample:")
            print(json.dumps(results[:3], indent=2))
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n❌ Fatal error: {e}")
