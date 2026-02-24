#!/usr/bin/env bash
# Trigger the daily email once from Cloud (same as 7am run).
# Usage: ./run_daily_once.sh

set -euo pipefail

PROJECT="${PROJECT:-investmentprocessor}"
REGION="us-east1"
JOB="options-dashboard-v2-daily"

echo "▶️  Triggering scheduler job: $JOB"
echo "   Project: $PROJECT  Region: $REGION"
echo ""

if ! gcloud scheduler jobs run "$JOB" --project="$PROJECT" --location="$REGION"; then
  echo "❌ Trigger failed. Check: gcloud auth list && gcloud config get-value project"
  exit 1
fi

echo ""
echo "✅ Job triggered. The function runs in the cloud (~8–10 min for full run)."
echo ""
echo "To check if the email was sent (run after a few minutes):"
echo "  gcloud functions logs read options-dashboard-v2 --gen2 --region=$REGION --limit=200 2>/dev/null | grep -E 'Sent|Failed to send'"
echo ""
