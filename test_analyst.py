import pandas as pd
import inspect
from insight_engine_light import match_tickers_in_title, load_tickers_and_aliases, is_headline_stock_moving

CSV_PATH = "us_ranked_headlines.csv"

print("\n==========================================")
print("TEST 1: Load Headlines")
print("==========================================")

df = pd.read_csv(CSV_PATH)
print(f"Loaded {len(df)} headlines")


print("\n==========================================")
print("TEST 2: Match tickers")
print("==========================================")

tickers, aliases = load_tickers_and_aliases()
examples = df.head(20)

for _, row in examples.iterrows():
    title = row["title"]
    hits = match_tickers_in_title(title, tickers, aliases)
    if hits:
        print(f"✔ {hits} : {title}")


print("\n==========================================")
print("TEST 3: LLM Relevance Check")
print("==========================================")

# Use the first few matched headlines
matched_rows = []
for _, row in df.iterrows():
    title = row["title"]
    hits = match_tickers_in_title(title, tickers, aliases)
    if hits:
        for tk in hits:
            matched_rows.append((tk, title))
        if len(matched_rows) >= 8:
            break

if not matched_rows:
    print("❌ No matched headlines to test.")
    exit()

print(f"Testing {len(matched_rows)} headlines with the LLM...\n")

for tk, title in matched_rows:
    print(f"---")
    print(f"[{tk}] {title}")
    try:
        verdict = is_headline_stock_moving(tk, title)
        print(f"LLM Verdict: {'YES' if verdict else 'NO'}")
    except Exception as e:
        print(f"❌ Error calling LLM: {e}")

print("\nDone.\n")
