#!/bin/bash
set -euo pipefail

# === Configuration ===
PROJECT_DIR="/Users/carolynenewman/Options"
VENV_DIR="$PROJECT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python3"
LOG_FILE="$PROJECT_DIR/logs/run.log"
LOCK_FILE="$PROJECT_DIR/.cron_lock"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# === Lockfile to prevent overlapping cron runs ===
if [ -f "$LOCK_FILE" ]; then
  echo "🚫 $(date '+%Y-%m-%d %H:%M:%S') — Another run is already in progress. Exiting." >> "$LOG_FILE"
  exit 0
fi
touch "$LOCK_FILE"

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') starting Options dashboard ==="

  # Move to project directory
  cd "$PROJECT_DIR" || {
    echo "❌ ERROR: Cannot cd to $PROJECT_DIR"
    rm -f "$LOCK_FILE"
    exit 1
  }

  # === Activate virtualenv (critical for cron) ===
  if [ -f "$VENV_DIR/bin/activate" ]; then
      echo "Activating virtualenv at $VENV_DIR"
      source "$VENV_DIR/bin/activate"
  else
      echo "❌ ERROR: virtualenv not found at $VENV_DIR"
      rm -f "$LOCK_FILE"
      exit 1
  fi

  echo "Using Python: $(which python3)"
  echo "Working directory: $(pwd)"

  # === Load .env so cron can access email settings ===
  if [ -f "$PROJECT_DIR/.env" ]; then
      echo "Loading .env into cron environment..."
      set -o allexport
      source "$PROJECT_DIR/.env"
      set +o allexport
  else
      echo "⚠️ WARNING: .env not found — email may not send"
  fi

  # ----------------------------------------------------
  # 1) Fetch fresh Google News
  # ----------------------------------------------------
  echo "📰 Fetching fresh Google News..."
  if ! python3 market_trends_watcher.py; then
      echo "⚠️ Failed to update Google News (market_trends_watcher.py)"
  fi

  # ----------------------------------------------------
  # 2) Score headlines with insight_engine_light
  # ----------------------------------------------------
  echo "📰 Scoring headlines via insight_engine_light..."
  if ! python3 - << 'EOF'
from insight_engine_light import score_headlines
score_headlines()
EOF
  then
      echo "⚠️ Failed inside insight_engine_light.score_headlines()"
  fi

  # ----------------------------------------------------
  # 3) Run main dashboard (app.py)
  # ----------------------------------------------------
  echo "📊 Running options_dashboard.py via app.py..."
  python3 app.py
  RC=$?

  echo "=== $(date '+%Y-%m-%d %H:%M:%S') finished with exit code $RC ==="

} >> "$LOG_FILE" 2>&1

# Cleanup lockfile
rm -f "$LOCK_FILE"
exit 0
