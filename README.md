# Options Dashboard

Options and equity dashboard with daily email digest and a desktop monitor app.

## Setup

1. **Clone and install**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment**
   - Copy `.env.example` to `.env`
   - Add your API keys and settings (Polygon, Finnhub, Gmail OAuth, etc.). See `.env.example` for required variables.

3. **Gmail (for daily email)**
   - Run `python gmail_auth.py` once to create `token.json` (do not commit this file).

## Running

- **Daily email (local):** `python main.py` — builds the digest and sends via Gmail.
- **Desktop monitor:** `python monitor_launcher.py` — local Flask app; use Refresh to update.
- **Packaged app:** `pyinstaller monitor.spec` — builds the Options Monitor executable in `dist/`.

## Deploy (Google Cloud)

- **Cloud Function + Scheduler:** `./quick_deploy.sh` — deploys the daily-email function and 7am ET scheduler.

## Notes

- Do not commit `.env`, `env.yaml`, or `token.json`; they contain secrets.
- Equity/options data: Polygon.io (primary), Finnhub and yfinance as fallbacks for live/delayed prices.
