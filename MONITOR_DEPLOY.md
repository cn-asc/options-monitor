# Options Monitor — Replit & Notion

The **Options Monitor** is a web view of the same dashboard table (IV, RV, headlines) as the daily email, with a **Refresh** button to re-fetch data.

- **Replit**: Run the app and get a public URL.
- **Notion**: Embed that URL in a page so the monitor lives inside Notion.

## Run locally

```bash
pip install -r requirements.txt
python monitor_app.py
```

Open http://localhost:5000 . Click **Refresh** to reload and re-fetch data.

## Deploy to Replit

1. **Create a Repl**
   - Go to [Replit](https://replit.com) and create a new Repl.
   - Choose **Import from GitHub** and point to this repo, or upload the project files.

2. **Set Secrets (env)**  
   In Replit: **Tools → Secrets**. Add at least:
   - `POLYGON_API_KEY` — your Polygon API key (Options plan)
   - `TICKERS` — e.g. `COIN,MSTR,NVDA,AAPL`  
   Optional: `MONEYNESS_PCT`, `SUBJECT_PREFIX`, `TIMEZONE`, `MAX_TICKERS_PER_RUN`.

3. **Run**
   - Replit should use `.replit` and run `python monitor_app.py`.
   - Click **Run**. The monitor will be at the URL Replit shows (e.g. `https://your-repl.your-username.repl.co`).

4. **Always On (optional)**  
   For a persistent URL, upgrade to Replit’s “Always On” so the server doesn’t sleep.

## Embed in Notion

1. Get your Replit app URL (e.g. `https://options-monitor.your-username.repl.co`).
2. In Notion, open the page where you want the monitor.
3. Type `/embed` and choose **Embed**.
4. Paste the Replit URL and confirm.
5. The dashboard will appear in the page; use the **Refresh** button on the embed to reload data.

**Note:** Notion doesn’t host the app; it only embeds your Replit (or other) URL. The app must be running and reachable at that URL.

## Env reference

Same as the rest of the project:

- `POLYGON_API_KEY` — required
- `TICKERS` — comma-separated symbols
- `MONEYNESS_PCT` — default `0.15`
- `MAX_TICKERS_PER_RUN` — default from `TICKERS` length
- `SUBJECT_PREFIX` — title prefix (default: Options Dashboard)
- `TIMEZONE` — e.g. `America/New_York`

No Gmail or `SEND_FROM`/`SEND_TO` needed for the monitor.
