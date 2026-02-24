#!/usr/bin/env python3
"""
Options Dashboard — Daily Email Digest

- Runs options_dashboard.py to gather IV, RV, range, and comments
- Enriches rows with a single best headline per ticker (insight_engine_light)
- Renders HTML via daily_email.html
- Sends via Gmail API (or writes preview.html when PREVIEW_ONLY=1)

ENV (.env):
  POLYGON_API_KEY=...
  SEND_FROM=you@gmail.com
  SEND_TO=team@yourfirm.com
  SUBJECT_PREFIX=[OptionsDash]
  PREVIEW_ONLY=0
  TIMEZONE=America/New_York
"""

import os
import sys
import json
import base64
import datetime as dt
from zoneinfo import ZoneInfo
from typing import Any, List, Dict

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, select_autoescape
from email.mime.text import MIMEText

# Gmail API
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import subprocess
import time

# News / headlines scoring
from insight_engine_light import score_headlines

# Small delay if you're running this off a cron right after boot
time.sleep(2)

# -------------------- Config --------------------
load_dotenv()

TIMEZONE = os.getenv("TIMEZONE", "America/New_York")
SEND_FROM = os.getenv("SEND_FROM")
SEND_TO = os.getenv("SEND_TO")
SUBJECT_PREFIX = os.getenv("SUBJECT_PREFIX", "Options Dashboard")
PREVIEW_ONLY = os.getenv("PREVIEW_ONLY", "0") == "1"

TEMPLATE_FILE = "daily_email.html"
PREVIEW_FILE = "preview.html"
TOKEN_FILE = "token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


# -------------------- Helpers --------------------

def is_recent_article(article_date: str, max_age_days: int = 3) -> bool:
    """
    article_date: ISO string 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS'
    """
    try:
        published = dt.datetime.fromisoformat(article_date[:10]).date()
        return (dt.date.today() - published).days <= max_age_days
    except Exception:
        return False


def today_str(tzname: str) -> str:
    tz = ZoneInfo(tzname)
    return dt.datetime.now(tz).strftime("%Y-%m-%d")


def gmail_service() -> Any:
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def send_gmail_html(service, sender: str, recipient: str, subject: str, html: str) -> None:
    msg = MIMEText(html, "html", "utf-8")
    msg["To"] = recipient
    msg["From"] = sender
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    for attempt in range(3):
        try:
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            return
        except Exception as e:
            print(f"⚠️ Gmail send attempt {attempt+1} failed: {e}")
            time.sleep(10)
    raise RuntimeError("Failed to send email after 3 retries")


def run_options_dashboard() -> List[dict]:
    """Run options_dashboard.py and capture its JSON output."""
    print("📊 Running options_dashboard.py...")
    result = subprocess.run(
        ["python3", "options_dashboard.py"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError("options_dashboard.py failed")

    stdout = result.stdout.strip()

    if not stdout:
        raise RuntimeError("options_dashboard.py produced no output")

    # Find the last JSON array in stdout
    start = stdout.rfind("[")
    end = stdout.rfind("]")

    if start == -1 or end == -1 or end < start:
        raise RuntimeError(
            "Could not find JSON output from options_dashboard.py.\n"
            f"STDOUT:\n{stdout}\n\nSTDERR:\n{result.stderr}"
        )

    json_text = stdout[start : end + 1]
    data = json.loads(json_text)
    return data



def build_headline_map() -> Dict[str, Dict[str, str]]:
    """
    Refresh Google News → score headlines → return:
        { TICKER: {"Headline": ..., "Headline URL": ... } }
    """

    # 1) Refresh us_ranked_headlines.csv
        # Always refresh raw news before scoring
    try:
        print("📰 Fetching fresh Google News...")
        subprocess.run(
            ["python3", "market_trends_watcher.py"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"⚠️ Failed running market_trends_watcher.py: {e}", file=sys.stderr)

    # 2) Run lightweight scorer
    try:
        print("📰 Scoring headlines via insight_engine_light...")
        best = score_headlines()  # DataFrame
    except Exception as e:
        print(f"⚠️ insight_engine_light.score_headlines() failed: {e}", file=sys.stderr)
        return {}

    # 3) Convert to mapping
    headline_map: Dict[str, Dict[str, str]] = {}

    for _, row in best.iterrows():
        ticker = str(row.get("ticker", "")).upper().strip()
        title = row.get("title")
        url = row.get("url")

        # Try common date fields produced by insight_engine_light
        published = (
            row.get("published_at")
            or row.get("date")
            or row.get("published")
        )

        if not ticker or not isinstance(title, str) or not title or not isinstance(url, str) or not url:
            continue

        # Enforce freshness (≤ 3 days)
        if published and is_recent_article(str(published), max_age_days=3):
            headline_map[ticker] = {
                "Headline": title,
                "Headline URL": url,
            }


    return headline_map



# -------------------- Main --------------------
def main():
    as_of = today_str(TIMEZONE)

    # 1) Build headline map (ticker → headline + URL) from news
    headline_map = build_headline_map()

    # 2) Run options dashboard to get core metrics
    rows = run_options_dashboard()

    # 3) Attach headline info to each row (optional)
    for r in rows:
        tkr = str(r.get("Ticker", "")).upper().strip()
        hinfo = headline_map.get(tkr)
        if hinfo:
            r["Headline"] = hinfo["Headline"]
            r["Headline URL"] = hinfo["Headline URL"]
        else:
            r["Headline"] = None
            r["Headline URL"] = None

    # 4) Render template
    env = Environment(
        loader=FileSystemLoader("."),
        autoescape=select_autoescape(["html"]),
    )
    tpl = env.get_template(TEMPLATE_FILE)

    MONEYNESS_PCT = float(os.getenv("MONEYNESS_PCT", "0.15"))
    pct = int(MONEYNESS_PCT * 100)

    html = tpl.render(
        as_of=as_of,
        rows=rows,
        subject_prefix=SUBJECT_PREFIX,
        pct=pct,
    )

    subject = f"{SUBJECT_PREFIX} — {as_of}"

    if PREVIEW_ONLY:
        with open(PREVIEW_FILE, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"👀 Preview saved to {PREVIEW_FILE}")
        return

    if not (SEND_FROM and SEND_TO):
        raise RuntimeError("SEND_FROM and SEND_TO must be set in .env")

    svc = gmail_service()
    send_gmail_html(svc, SEND_FROM, SEND_TO, subject, html)
    print(f"✉️ Sent {subject} to {SEND_TO}")


if __name__ == "__main__":
    main()
