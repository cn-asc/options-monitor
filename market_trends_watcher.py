import feedparser
import pandas as pd
import datetime

REGION = "US"
LANG = "en"
TOPICS = ["TOP", "BUSINESS", "WORLD", "TECHNOLOGY", "ARTS", "ENTERTAINMENT","SCIENCE"]
OUTPUT_FILE = "us_ranked_headlines.csv"
MAX_HEADLINES_PER_FEED = 1000

def build_feed_url(topic, region=REGION, lang=LANG):
    if topic.upper() == "TOP":
        return f"https://news.google.com/rss?hl={lang}-{region}&gl={region}&ceid={region}:{lang}"
    return f"https://news.google.com/rss/headlines/section/topic/{topic.upper()}?hl={lang}-{region}&gl={region}&ceid={region}:{lang}"

def fetch_and_rank_headlines(topics=TOPICS):
    """Fetch headlines from multiple feeds and rank by inferred popularity."""
    all_records = []

    for topic in topics:
        url = build_feed_url(topic)
        print(f"Fetching {topic} headlines for {REGION}...")
        feed = feedparser.parse(url)

        # Each feed is ordered by importance → assign descending scores
        for rank, entry in enumerate(feed.entries[:MAX_HEADLINES_PER_FEED], start=1):
            score = (MAX_HEADLINES_PER_FEED - rank + 1) / MAX_HEADLINES_PER_FEED  # 1.0 = top, 0.0 = bottom
            source = None
            if hasattr(entry, "source") and entry.source:
                source = getattr(entry.source, "title", None)
            elif hasattr(entry, "author_detail") and entry.author_detail:
                source = getattr(entry.author_detail, "name", None)

            all_records.append({
                "topic": topic,
                "title": entry.title,
                "source": source,
                "article_url": entry.link,
                "rank_within_feed": rank,
                "relative_popularity": score,
                "timestamp": datetime.datetime.now()
            })

    df = pd.DataFrame(all_records)

    # Boost score if headline appears in multiple feeds
    dup_counts = df.groupby("title")["topic"].count().rename("feed_count")
    df = df.merge(dup_counts, on="title")
    df["composite_score"] = df["relative_popularity"] * (1 + 0.2 * (df["feed_count"] - 1))

    # Keep the highest score per headline
    df = df.sort_values("composite_score", ascending=False).drop_duplicates("title")

    print(f"[{datetime.datetime.now()}] ✅ Ranked {len(df)} unique headlines across {len(topics)} feeds.")
    print(df.head(10))
    return df

def save_ranked_headlines(df, output_file=OUTPUT_FILE):
    if df.empty:
        print("No headlines to save.")
        return
    df.to_csv(output_file, index=False)
    print(f"✅ Saved ranked headlines to {output_file}")

if __name__ == "__main__":
    df = fetch_and_rank_headlines()
    save_ranked_headlines(df)
