# Google Cloud Deployment Guide

This guide will help you deploy the Options Dashboard to Google Cloud Functions and schedule it to run daily at 7am ET.

**Important:** This deployment is **additive** and **independent**. It does NOT modify or interfere with existing InvestmentProcessor services:
- `inv-monitor` (Gmail poller)
- `investment-processor` (deck → Drive/Airtable)
- `auto-pass-draft-job` (Gmail drafts)

The Options Dashboard uses:
- Unique function name: `options-dashboard-v2`
- Unique scheduler job: `options-dashboard-v2-daily`
- Same project: `investmentprocessor` (for convenience)

## Prerequisites

1. Google Cloud account with billing enabled
2. `gcloud` CLI installed and authenticated: https://cloud.google.com/sdk/docs/install
3. Gmail API credentials (`token.json` and OAuth credentials)

## Step 1: Set Up Google Cloud Project

This deployment uses the same `investmentprocessor` project as your InvestmentProcessor setup.

```bash
# Set the project (should match InvestmentProcessor)
export PROJECT="investmentprocessor"
gcloud config set project $PROJECT

# Enable required APIs (if not already enabled)
gcloud services enable cloudfunctions.googleapis.com --project=$PROJECT
gcloud services enable cloudscheduler.googleapis.com --project=$PROJECT
```

## Step 2: env.yaml is built from .env when you deploy

**When you run `./quick_deploy.sh`, the script runs `build_env_yaml.py`, which builds `env.yaml` from your `.env`.** So you only maintain `.env`; the deploy step creates `env.yaml` for the Cloud Function.

- **First-time only:** If you don’t have `GMAIL_TOKEN_JSON` in `.env`, create `env.yaml` once (e.g. copy from `env.yaml.example`) and add `GMAIL_TOKEN_JSON`. On later deploys, `build_env_yaml.py` will keep that value from the existing `env.yaml` when merging.
- Set `PREVIEW_ONLY=0` in `.env` so the scheduled job sends email (use `1` only for local preview).
- The `GMAIL_TOKEN_JSON` in `env.yaml` (or `.env`) is your OAuth token. If it expires, regenerate with `gmail_auth.py` and update `.env` or `env.yaml`.

## Step 3: Deploy Cloud Function

The quick deploy script will handle both deployment and scheduler setup:

```bash
# Make sure you're using the investmentprocessor project
gcloud config set project investmentprocessor

# Run the deployment script (it will create/update the scheduler automatically)
./quick_deploy.sh
```

Or deploy manually:

```bash
# Deploy the function using env.yaml
gcloud functions deploy options-dashboard-v2 \
    --gen2 \
    --runtime=python311 \
    --region=us-east1 \
    --project=investmentprocessor \
    --source=. \
    --entry-point=options_dashboard_cloud_function \
    --env-vars-file=env.yaml \
    --memory=512MB \
    --timeout=540s \
    --max-instances=1
```

The `quick_deploy.sh` script will automatically create/update the Cloud Scheduler job to run daily at 7am ET.

**Note:** The schedule `0 12 * * *` runs at 12:00 UTC daily. To adjust for 7am ET:
- EST (Nov-Mar): 7am ET = 12:00 UTC ✓
- EDT (Mar-Nov): 7am ET = 11:00 UTC

For exact 7am ET year-round, you may need two schedules or use a more complex cron expression.

## Step 4: Test the Function

```bash
# Test the function manually
gcloud functions call options-dashboard-v2 \
    --gen2 \
    --region=us-east1 \
    --project=investmentprocessor

# Or get the URL and test via HTTP
FUNCTION_URL=$(gcloud functions describe options-dashboard-v2 \
    --gen2 \
    --region=us-east1 \
    --project=investmentprocessor \
    --format="value(serviceConfig.uri)")
curl "$FUNCTION_URL"
```

## Step 5: Monitor Logs

```bash
# View logs
gcloud functions logs read options-dashboard-v2 \
    --gen2 \
    --region=us-east1 \
    --project=investmentprocessor \
    --limit=50

# Or in the Cloud Console
# https://console.cloud.google.com/functions/details/us-east1/options-dashboard-v2?project=investmentprocessor
```

## Troubleshooting

1. **Function timeout**: Increase `--timeout` if the function takes longer than 9 minutes
2. **Memory issues**: Increase `--memory` if you see OOM errors
3. **Gmail auth**: If `token.json` expires, regenerate it using `gmail_auth.py` and update `GMAIL_TOKEN_JSON` in `env.yaml`
4. **Environment variables**: Check `env.yaml` has all required values

## Saving your changes to the Cloud Run job

**Code and template changes** (metrics, sort order, thick lines, new tickers in the app) are deployed only when you run a deploy. **Environment variables** (including `TICKERS`) come only from `env.yaml`, not from `.env`.

To ensure today’s run uses the latest code and ticker list:

1. **Update `env.yaml`**  
   - Set `TICKERS` to the same list as in `.env` (see `env.yaml.example` for the current list).  
   - Set `PREVIEW_ONLY: "0"` so the scheduled job sends email.

2. **Deploy from the repo that has your latest code**  
   ```bash
   gcloud config set project investmentprocessor
   ./quick_deploy.sh
   ```  
   This uploads the current directory (all Python and HTML) and the current `env.yaml`. The next scheduled run (and any manual run) will use this code and config.

## Updating the Function (manual)

To update only the code/config without the full script:

```bash
gcloud functions deploy options-dashboard-v2 \
    --gen2 \
    --runtime=python311 \
    --region=us-east1 \
    --source=. \
    --env-vars-file=env.yaml \
    # ... (same flags as in quick_deploy.sh)
```

## Cost Estimate

- Cloud Functions: ~$0.40/month (1 invocation/day, ~5 min runtime)
- Cloud Scheduler: Free tier (3 jobs free)
- **Total: ~$0.40/month**

**Note:** Using `env.yaml` is simpler but less secure than Secret Manager. For production, consider migrating sensitive values to Secret Manager.
