#!/usr/bin/env bash
# Check Cloud Scheduler job status
# Usage: ./check_scheduler.sh

set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project)}"
EXPECTED_PROJECT="investmentprocessor"
if [[ "$PROJECT" != "$EXPECTED_PROJECT" ]]; then
  PROJECT="$EXPECTED_PROJECT"
fi

REGION="us-east1"
SCHEDULER_JOB="options-dashboard-v2-daily"

echo "🔍 Checking Cloud Scheduler job: $SCHEDULER_JOB"
echo "Project: $PROJECT"
echo "Region: $REGION"
echo ""

# Check if job exists
if ! gcloud scheduler jobs describe "${SCHEDULER_JOB}" --project="${PROJECT}" --location="${REGION}" &>/dev/null; then
  echo "❌ ERROR: Scheduler job '$SCHEDULER_JOB' does not exist!"
  echo ""
  echo "Creating it now..."
  exit 1
fi

# Get job details
echo "📋 Job Details:"
gcloud scheduler jobs describe "${SCHEDULER_JOB}" \
    --project="${PROJECT}" \
    --location="${REGION}" \
    --format="yaml"

echo ""
echo "📊 Job Status:"
gcloud scheduler jobs describe "${SCHEDULER_JOB}" \
    --project="${PROJECT}" \
    --location="${REGION}" \
    --format="value(state)"

echo ""
echo "🕐 Schedule:"
gcloud scheduler jobs describe "${SCHEDULER_JOB}" \
    --project="${PROJECT}" \
    --location="${REGION}" \
    --format="value(schedule)"

echo ""
echo "📝 Recent execution history:"
gcloud scheduler jobs describe "${SCHEDULER_JOB}" \
    --project="${PROJECT}" \
    --location="${REGION}" \
    --format="value(lastAttemptTime,status.code,status.message)" 2>/dev/null || echo "No execution history yet"

echo ""
echo "🔧 To manually trigger the job:"
echo "   gcloud scheduler jobs run $SCHEDULER_JOB --project=$PROJECT --location=$REGION"
