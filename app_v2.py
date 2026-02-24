#!/usr/bin/env python3
"""
Options Dashboard v2 — Daily Email Digest

- Runs options_dashboard_v2.py to gather IV, RV, range, and comments (uses Polygon Options API for IV)
- Enriches rows with a single best headline per ticker (insight_engine_light)
- Renders HTML via daily_email.html
- Sends via Gmail API (or writes preview.html when PREVIEW_ONLY=1)

ENV (.env):
  POLYGON_API_KEY=... (must have Options plan)
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

# News / headlines scoring
from insight_engine_light import score_headlines

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
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",  # for Trade Options draft creation
]


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
    # Check if running in Cloud Functions (token.json stored as secret)
    token_json_str = os.getenv("GMAIL_TOKEN_JSON")
    if token_json_str:
        # Running in Cloud Functions - token.json is stored as a secret
        import tempfile
        import json as json_lib
        token_data = json_lib.loads(token_json_str)
        # Use the token's own scopes so we don't trigger invalid_scope (token may have send only or send+compose)
        token_scopes = token_data.get("scopes") or SCOPES
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            json_lib.dump(token_data, f)
            temp_token_file = f.name
        creds = Credentials.from_authorized_user_file(temp_token_file, token_scopes)
        os.unlink(temp_token_file)  # Clean up temp file
    else:
        # Running locally - use token.json file
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def send_gmail_html(service, sender: str, recipient: str, subject: str, html: str) -> None:
    # Use ASCII hyphen only so subject displays correctly in all mail clients (no UTF-8 mojibake)
    subject_safe = subject.replace("\u2013", "-").replace("\u2014", "-")  # en-dash, em-dash -> hyphen
    msg = MIMEText(html, "html", "utf-8")
    msg["To"] = recipient
    msg["From"] = sender
    msg["Subject"] = subject_safe
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    for attempt in range(3):
        try:
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            return
        except Exception as e:
            print(f"⚠️ Gmail send attempt {attempt+1} failed: {e}")
            # No sleep - retry immediately
    raise RuntimeError("Failed to send email after 3 retries")


def run_options_dashboard() -> List[dict]:
    """Run options_dashboard_v2.py and capture its JSON output."""
    print("📊 Running options_dashboard_v2.py...")
    # Run without capturing output so stderr (debug logs) are visible in real-time
    result = subprocess.run(
        ["python3", "options_dashboard_v2.py"],
        capture_output=True,
        text=True,
    )
    
    # Print stderr output (includes debug logs) even if successful
    if result.stderr:
        print("--- Debug Output (stderr) ---", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        print("--- End Debug Output ---", file=sys.stderr)
    
    if result.returncode != 0:
        raise RuntimeError("options_dashboard_v2.py failed")

    stdout = result.stdout.strip()

    if not stdout:
        raise RuntimeError("options_dashboard_v2.py produced no output")

    # Find the last JSON array in stdout
    start = stdout.rfind("[")
    end = stdout.rfind("]")

    if start == -1 or end == -1 or end < start:
        raise RuntimeError(
            "Could not find JSON output from options_dashboard_v2.py.\n"
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

    # Sort so more red (higher IV/RV ratio) floats to top, blue (lower ratio) to bottom
    _pct = int(float(os.getenv("MONEYNESS_PCT", "0.15")) * 100)
    _r30_neg = f"-{_pct}% 30D Ratio"
    _r30_pos = f"+{_pct}% 30D Ratio"
    _rltm_neg = f"-{_pct}% LTM Ratio"
    _rltm_pos = f"+{_pct}% LTM Ratio"

    def _ratio_sort_key(r):
        vals = [
            r.get(_r30_neg), r.get(_r30_pos),
            r.get(_rltm_neg), r.get(_rltm_pos),
        ]
        nums = [float(v) for v in vals if v is not None]
        return -max(nums) if nums else 0  # higher ratio first (red on top)

    rows = sorted(rows, key=_ratio_sort_key)

    # 4) Banner/logo from assets folder as data URLs for email
    import base64
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _assets = os.path.join(_script_dir, "assets")
    banner_data_url = None
    logo_data_url = None
    for name, mime_suffix in [("BANNER.png", "image/png"), ("banner.png", "image/png"), ("banner.jpg", "image/jpeg"), ("BANNER.jpg", "image/jpeg")]:
        path = os.path.join(_assets, name)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            banner_data_url = f"data:{mime_suffix};base64,{data}"
            break
    _logo_path = os.path.join(_assets, "LOGO.svg")
    if os.path.isfile(_logo_path):
        with open(_logo_path, "rb") as f:
            logo_data_url = "data:image/svg+xml;base64," + base64.b64encode(f.read()).decode()

    # 5) Render template
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
        banner_data_url=banner_data_url,
        logo_data_url=logo_data_url,
    )

    subject = f"{SUBJECT_PREFIX} - {as_of}"
    subject = subject.replace("\u2013", "-").replace("\u2014", "-")  # ensure ASCII for log and email

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
