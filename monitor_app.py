#!/usr/bin/env python3
"""
Options Monitor — Web view with Refresh button

Serves the same dashboard table as the daily email (IV, RV, headlines).
Deploy to Replit and optionally embed in Notion via an Embed block.

- GET / → Runs dashboard + headlines, renders table with a "Refresh" button.
- Refresh button reloads the page to re-fetch data.

Env: Same as options_dashboard_v2 + app_v2 (POLYGON_API_KEY, TICKERS, etc.).
On Replit: set Secrets (POLYGON_API_KEY, etc.); Replit sets PORT.
"""

import os
import sys
import json

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, redirect
from jinja2 import Environment, FileSystemLoader, select_autoescape
from dotenv import load_dotenv

load_dotenv()

# Import dashboard builder and headline map (no Gmail)
from options_dashboard_v2 import build_dashboard
from app_v2 import build_headline_map

app = Flask(__name__)

# When packaged (PyInstaller), templates are in sys._MEIPASS
def _template_dir():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(os.path.abspath(__file__))

TEMPLATE_FILE = "daily_email.html"
SUBJECT_PREFIX = os.getenv("SUBJECT_PREFIX", "Options Dashboard")
TIMEZONE = os.getenv("TIMEZONE", "America/New_York")
MONEYNESS_PCT = float(os.getenv("MONEYNESS_PCT", "0.15"))


def today_str(tzname: str) -> str:
    from zoneinfo import ZoneInfo
    from datetime import datetime
    tz = ZoneInfo(tzname)
    return datetime.now(tz).strftime("%Y-%m-%d")


def refresh_time_str(tzname: str) -> str:
    """Current date and time in the given timezone for 'last refreshed' display."""
    from zoneinfo import ZoneInfo
    from datetime import datetime
    tz = ZoneInfo(tzname)
    return datetime.now(tz).strftime("%Y-%m-%d %I:%M %p %Z").replace(" 0", " ")


def _build_headline_map_frozen() -> dict:
    """When packaged, we can't use subprocess; fetch headlines in-process."""
    from market_trends_watcher import fetch_and_rank_headlines
    from insight_engine_light import score_headlines

    exe_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(exe_dir, "us_ranked_headlines.csv")
    try:
        df = fetch_and_rank_headlines()
        if not df.empty:
            df.to_csv(csv_path, index=False)
    except Exception as e:
        if os.path.isfile(csv_path):
            pass  # use existing file
        else:
            return {}
    try:
        best = score_headlines(headlines_csv=csv_path)
    except Exception:
        return {}
    headline_map = {}
    for _, row in best.iterrows():
        ticker = str(row.get("ticker", "")).upper().strip()
        title = row.get("title")
        url = row.get("url")
        if ticker and title and url:
            headline_map[ticker] = {"Headline": title, "Headline URL": url}
    return headline_map


def get_monitor_html() -> str:
    """Build dashboard rows, attach headlines, return rendered HTML."""
    as_of = today_str(TIMEZONE)
    refreshed_at = refresh_time_str(TIMEZONE)
    pct = int(MONEYNESS_PCT * 100)

    # 1) Dashboard data (in-process, no subprocess)
    raw_json = build_dashboard()
    rows = json.loads(raw_json)

    # 2) Headlines (in packaged app, use in-process path; no subprocess)
    if getattr(sys, "frozen", False):
        headline_map = _build_headline_map_frozen()
    else:
        headline_map = build_headline_map()
    for r in rows:
        tkr = str(r.get("Ticker", "")).upper().strip()
        hinfo = headline_map.get(tkr)
        if hinfo:
            r["Headline"] = hinfo["Headline"]
            r["Headline URL"] = hinfo["Headline URL"]
        else:
            r["Headline"] = None
            r["Headline URL"] = None

    pct = int(MONEYNESS_PCT * 100)
    # Sort: indices (SPY, QQQ) first, then alphabetical, then gold/silver/copper at bottom
    TICKER_DISPLAY_ORDER = [
        "SPY", "QQQ", "AAPL", "ADBE", "AMZN", "APP", "BRK.B", "COIN", "CRCL", "DPZ",
        "FICO", "FTNT", "GOOG", "HOOD", "IBIT", "IBM", "KLAC", "LRCX", "META", "MSCI",
        "MSFT", "MSTR", "NVDA", "GLD", "GDX", "SLV", "SIL", "CPER", "COPX",
    ]
    _order_map = {t: i for i, t in enumerate(TICKER_DISPLAY_ORDER)}

    def _display_sort_key(r):
        t = str(r.get("Ticker", "")).upper().strip()
        return (_order_map.get(t, 9999), t)  # unknown tickers at end, then alpha

    rows = sorted(rows, key=_display_sort_key)

    # 3) Banner/logo from assets folder as data URLs (works in packaged app and email)
    import base64
    _root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    _assets = os.path.join(_root, "assets")
    banner_data_url = None
    logo_data_url = None
    # Banner: try assets/BANNER.png (and common variants)
    for _banner_name, _mime in [("BANNER.png", "image/png"), ("banner.png", "image/png"), ("banner.jpg", "image/jpeg"), ("BANNER.jpg", "image/jpeg")]:
        _banner_path = os.path.join(_assets, _banner_name)
        if os.path.isfile(_banner_path):
            with open(_banner_path, "rb") as f:
                banner_data_url = f"data:{_mime};base64," + base64.b64encode(f.read()).decode()
            break
    # Logo: assets/LOGO.svg
    _logo_path = os.path.join(_assets, "LOGO.svg")
    if os.path.isfile(_logo_path):
        with open(_logo_path, "rb") as f:
            logo_data_url = "data:image/svg+xml;base64," + base64.b64encode(f.read()).decode()

    # 4) Render same template as email (use _template_dir for packaged app)
    env = Environment(
        loader=FileSystemLoader(_template_dir()),
        autoescape=select_autoescape(["html"]),
    )
    tpl = env.get_template(TEMPLATE_FILE)
    html_body = tpl.render(
        as_of=as_of,
        rows=rows,
        subject_prefix=SUBJECT_PREFIX,
        pct=pct,
        banner_data_url=banner_data_url,
        logo_data_url=logo_data_url,
        show_trade_buttons=True,
    )

    # 5) Inject refresh bar right after <body> (red Refresh button)
    refresh_bar = f"""
  <div class="refresh-bar" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:16px;padding:10px 16px;background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
    <span class="last-updated" style="font-size:13px;color:#666;">Refreshed at {refreshed_at} — click Refresh to fetch latest</span>
    <a href="/" class="refresh-btn" style="display:inline-block;padding:10px 20px;background:#c00;color:#fff!important;text-decoration:none;border-radius:6px;font-weight:600;font-size:14px;">Refresh</a>
  </div>
"""
    if "<body>" in html_body:
        html_body = html_body.replace("<body>", "<body>" + refresh_bar, 1)
    else:
        html_body = refresh_bar + html_body
    return html_body


def _trade_config_path():
    """Path to trade_config.json (next to app or exe)."""
    root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    if getattr(sys, "frozen", False):
        root = os.path.dirname(sys.executable)
    return os.path.join(root, "trade_config.json")


def _load_trade_config():
    """Load clients and email templates from trade_config.json."""
    path = _trade_config_path()
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _create_gmail_drafts(ticker: str) -> tuple[int, str | None]:
    """
    Create one Gmail draft per email entry for the given ticker.
    Config can use "emails" (list of { to, body_template }) or legacy "clients" (list of { email, name }) with one body_template.
    Returns (count_created, error_message). error_message is set if config or Gmail fails.
    """
    cfg = _load_trade_config()
    if not cfg:
        return 0, "trade_config.json not found. Copy trade_config.json.example to trade_config.json and add clients + templates."

    from zoneinfo import ZoneInfo
    from datetime import datetime
    tz = ZoneInfo(os.getenv("TIMEZONE", "America/New_York"))
    date_str = datetime.now(tz).strftime("%Y-%m-%d")

    subject_tpl = cfg.get("subject_template") or "Trade idea: {ticker}"
    subject = subject_tpl.format(ticker=ticker, date=date_str)

    # New format: "emails" array with per-entry "to" and "body_template" (uses {ticker}, {date})
    emails = cfg.get("emails")
    if emails:
        entries = []
        for e in emails:
            to_raw = e.get("to") or e.get("email")
            body_tpl = e.get("body_template") or e.get("body") or ""
            if to_raw and body_tpl:
                to_normalized = ", ".join(addr.strip() for addr in to_raw.replace(";", ",").split(",") if addr.strip())
                if to_normalized:
                    entries.append((to_normalized, body_tpl))
    else:
        # Legacy: "clients" + single body_template
        clients = cfg.get("clients") or []
        body_tpl = cfg.get("body_template") or "Hi {client_name},\n\nOptions trade idea for {ticker}."
        entries = []
        for c in clients:
            email = c.get("email") or c.get("to")
            name = c.get("name") or email or "Client"
            if email:
                entries.append((email, body_tpl.format(ticker=ticker, client_name=name)))

    if not entries:
        return 0, "trade_config.json has no 'emails' or 'clients' with valid 'to' and body."

    # For new "emails" format, body_tpl uses {ticker} and {date}; for legacy, body is already formatted
    use_date_ticker = bool(emails)

    try:
        from app_v2 import gmail_service
        from email.mime.text import MIMEText
        import base64 as b64

        service = gmail_service()
        send_from = os.getenv("SEND_FROM")
        if not send_from:
            return 0, "SEND_FROM not set in .env or OptionsMonitor.env."

        created = 0
        for to_addrs, body_tpl in entries:
            body = body_tpl.format(ticker=ticker, date=date_str) if use_date_ticker else body_tpl
            msg = MIMEText(body, "plain", "utf-8")
            msg["To"] = to_addrs
            msg["From"] = send_from
            msg["Subject"] = subject
            raw = b64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
            service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
            created += 1
        return created, None
    except Exception as e:
        err = str(e)
        if "insufficient" in err.lower() or "403" in err or "permission" in err.lower():
            return 0, "Gmail needs compose permission. Re-run: python gmail_auth.py (then restart the app)."
        return 0, err


@app.route("/")
def index():
    try:
        html = get_monitor_html()
        return html
    except Exception as e:
        return f"<pre>Error loading monitor: {e}</pre>", 500


@app.route("/create-drafts/<ticker>")
def create_drafts(ticker: str):
    """Create Gmail drafts for this ticker and redirect to Gmail drafts."""
    count, err = _create_gmail_drafts(ticker.strip().upper())
    if err:
        return f"<pre>Could not create drafts: {err}</pre><p><a href='/'>Back to dashboard</a></p>", 400
    # Open Gmail drafts in the same tab
    return redirect("https://mail.google.com/mail/#drafts", code=302)


def main():
    # Default 5050 to avoid macOS AirPlay Receiver on 5000
    port = int(os.environ.get("PORT", 5050))
    # Bind to 0.0.0.0 so Replit and other hosts can reach it
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")


if __name__ == "__main__":
    main()
