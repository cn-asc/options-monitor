#!/usr/bin/env bash
# Quick deployment script for Google Cloud Functions
# 
# This deploys the Options Dashboard as a separate, independent service.
# It does NOT modify or interfere with existing InvestmentProcessor services:
#   - inv-monitor
#   - investment-processor
#   - auto-pass-draft-job
#
# Usage: ./quick_deploy.sh

set -euo pipefail

# Configuration - using same project as InvestmentProcessor
PROJECT="${PROJECT:-$(gcloud config get-value project)}"
EXPECTED_PROJECT="investmentprocessor"
if [[ "$PROJECT" != "$EXPECTED_PROJECT" ]]; then
  echo "❌ Refusing to deploy: active gcloud project is '$PROJECT' (expected '$EXPECTED_PROJECT')."
  echo "👉 Run:  gcloud config set project $EXPECTED_PROJECT"
  echo "   or:   PROJECT=$EXPECTED_PROJECT ./quick_deploy.sh"
  exit 1
fi

REGION="us-east1"
FUNCTION_NAME="options-dashboard-v2"  # Unique name - won't conflict with existing services
SCHEDULER_JOB="${FUNCTION_NAME}-daily"  # Unique scheduler job name
SERVICE_ACCOUNT="${FUNCTION_NAME}-sa@${PROJECT}.iam.gserviceaccount.com"
TZ="America/New_York"

echo "🚀 Deploying Options Dashboard to Google Cloud Functions..."
echo "Project: $PROJECT"
echo "Region: $REGION"
echo "Function: $FUNCTION_NAME (separate from InvestmentProcessor services)"
echo ""

# Create service account if it doesn't exist
echo "⏳ Checking service account..."
if ! gcloud iam service-accounts describe "${SERVICE_ACCOUNT}" --project="${PROJECT}" &>/dev/null; then
  echo "   Creating service account ${FUNCTION_NAME}-sa..."
  gcloud iam service-accounts create "${FUNCTION_NAME}-sa" \
    --display-name="Options Dashboard Service Account" \
    --project="${PROJECT}" \
    --quiet || true
fi

# Build env.yaml from .env so you only maintain .env (single source of truth)
if [[ -f .env ]]; then
  echo "📋 Building env.yaml from .env..."
  python3 build_env_yaml.py || { echo "❌ build_env_yaml.py failed."; exit 1; }
fi
if [[ ! -f env.yaml ]]; then
  echo "❌ env.yaml not found. Run: python3 build_env_yaml.py (requires .env)."
  echo "   If you have no .env, copy env.yaml.example to env.yaml and add GMAIL_TOKEN_JSON, POLYGON_API_KEY, TICKERS, SEND_FROM, SEND_TO. See DEPLOY.md."
  exit 1
fi

# Most common reason for "no email this morning": preview mode deployed to Cloud
if [[ -f .env ]] && grep -qE '^PREVIEW_ONLY[[:space:]]*=[[:space:]]*1([[:space:]]|$)' .env; then
  echo ""
  echo "❌ .env has PREVIEW_ONLY=1 — the scheduled job will NOT send email (only writes preview)."
  echo "   Set PREVIEW_ONLY=0 in .env, then run ./quick_deploy.sh again."
  echo "   To deploy anyway: PREVIEW_ONLY_ALLOW_DEPLOY=1 ./quick_deploy.sh"
  echo ""
  [[ "${PREVIEW_ONLY_ALLOW_DEPLOY:-}" == "1" ]] || exit 1
fi

if ! grep -q '^GMAIL_TOKEN_JSON:' env.yaml 2>/dev/null; then
  echo "❌ env.yaml has no GMAIL_TOKEN_JSON. Add it to .env (one line) or create env.yaml with GMAIL_TOKEN_JSON once, then run deploy again. See DEPLOY.md."
  exit 1
fi

echo "📦 Deploying Cloud Function (source=. and env.yaml)..."
gcloud functions deploy $FUNCTION_NAME \
    --gen2 \
    --runtime=python311 \
    --region=$REGION \
    --project=$PROJECT \
    --source=. \
    --entry-point=options_dashboard_cloud_function \
    --trigger-http \
    --service-account="${SERVICE_ACCOUNT}" \
    --no-allow-unauthenticated \
    --env-vars-file=env.yaml \
    --memory=512MB \
    --timeout=540s \
    --max-instances=1

# Grant the service account permission to invoke the function
# Note: Cloud Functions Gen2 uses Cloud Run under the hood, so we need both permissions
echo "⏳ Granting invoke permissions..."
gcloud functions add-iam-policy-binding $FUNCTION_NAME \
    --gen2 \
    --region=$REGION \
    --project=$PROJECT \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/cloudfunctions.invoker" \
    --quiet || true

# Also grant Cloud Run invoker role (required for Gen2 functions)
echo "⏳ Granting Cloud Run invoke permissions..."
gcloud run services add-iam-policy-binding $FUNCTION_NAME \
    --region=$REGION \
    --project=$PROJECT \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/run.invoker" \
    --quiet || true

FUNCTION_URL=$(gcloud functions describe $FUNCTION_NAME \
    --gen2 \
    --region=$REGION \
    --project=$PROJECT \
    --format="value(serviceConfig.uri)")

echo ""
echo "✅ Deployment complete!"
echo "Function URL: $FUNCTION_URL"
echo ""

# Create or update scheduler job (unique name, won't conflict with existing jobs)
echo "📅 Configuring scheduler job ${SCHEDULER_JOB}..."
echo "   (This is separate from: inv-monitor-job, run-deck-funnel, auto-pass-draft-job)"

if gcloud scheduler jobs describe "${SCHEDULER_JOB}" --project="${PROJECT}" --location="${REGION}" &>/dev/null; then
  ACTION="update"
else
  ACTION="create"
fi

# Schedule: 7am ET
# When timezone is set to America/New_York, the cron schedule is interpreted in ET
# So "0 7 * * *" means 7:00 AM Eastern Time (automatically handles EST/EDT)
SCHEDULE="0 7 * * *"  # 7:00 AM ET (timezone handles EST/EDT automatically)

if [[ "$ACTION" == "update" ]]; then
  gcloud scheduler jobs update http "${SCHEDULER_JOB}" \
    --project="${PROJECT}" \
    --location="${REGION}" \
    --schedule="${SCHEDULE}" \
    --time-zone="${TZ}" \
    --uri="${FUNCTION_URL}" \
    --http-method=GET \
    --oidc-service-account-email="${SERVICE_ACCOUNT}" \
    --oidc-token-audience="${FUNCTION_URL}" \
    --attempt-deadline=600s \
    --quiet
  gcloud scheduler jobs resume "${SCHEDULER_JOB}" \
    --project="$PROJECT" \
    --location="$REGION" \
    --quiet
else
  gcloud scheduler jobs create http "${SCHEDULER_JOB}" \
    --project="${PROJECT}" \
    --location="${REGION}" \
    --schedule="${SCHEDULE}" \
    --time-zone="${TZ}" \
    --uri="${FUNCTION_URL}" \
    --http-method=GET \
    --oidc-service-account-email="${SERVICE_ACCOUNT}" \
    --oidc-token-audience="${FUNCTION_URL}" \
    --attempt-deadline=600s \
    --quiet
fi

echo ""
echo "✅ Scheduler job '${SCHEDULER_JOB}' configured: ${SCHEDULE} (${TZ})"
echo "   Runs daily at 7am ET (12:00 UTC)"
echo ""

# Test the function (trigger it and check logs instead of waiting)
echo "🧪 Triggering function test (checking logs for execution)..."
echo "   Function URL: $FUNCTION_URL"
echo ""

# Trigger the function asynchronously and check logs
TOKEN=$(gcloud auth print-identity-token 2>/dev/null || echo "")
if [[ -n "$TOKEN" ]]; then
    echo "   Triggering function via HTTP..."
    curl -s -X GET \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        "$FUNCTION_URL" > /dev/null &
    CURL_PID=$!
    
    echo "   Function triggered (running in background)"
    echo "   Waiting 5 seconds, then checking logs..."
    sleep 5
    
    # Check recent logs
    echo ""
    echo "📋 Recent function logs:"
    gcloud functions logs read $FUNCTION_NAME \
        --gen2 \
        --region=$REGION \
        --project=$PROJECT \
        --limit=10 2>&1 | head -15 || echo "   (No logs yet - function may still be starting)"
    
    echo ""
    echo "✅ Function test triggered!"
    echo "   View full logs: gcloud functions logs read $FUNCTION_NAME --gen2 --region=$REGION --limit=50"
else
    echo "⚠️  Could not get auth token for testing"
    echo "   You can test manually: gcloud functions call $FUNCTION_NAME --gen2 --region=$REGION"
fi

echo ""
echo "📋 Summary:"
echo "   Function: $FUNCTION_NAME"
echo "   URL: $FUNCTION_URL"
echo "   Scheduler: $SCHEDULER_JOB (runs daily at 7am ET)"
echo "   Service Account: $SERVICE_ACCOUNT"
