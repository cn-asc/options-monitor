# Gmail token troubleshooting (daily email)

The Cloud Function sends the daily email using **GMAIL_TOKEN_JSON** from `env.yaml`. If the email never lands, work through these steps.

---

## Step 1: Confirm the token works locally (token.json)

This proves your **token.json** and Gmail credentials are valid.

```bash
# From project root, with .env containing SEND_FROM and SEND_TO
python test_gmail_send.py --step 1
```

- **If it succeeds:** You should get a test email. The token file is good; the issue is how the token is passed or used in Cloud (Step 2 or deploy).
- **If it fails:** Fix the token first: run `python gmail_auth.py`, complete the browser flow, then run Step 1 again.

---

## Step 2: Confirm the env.yaml value works (same as Cloud)

This uses the **exact** value that gets deployed (the `GMAIL_TOKEN_JSON` line in `env.yaml`). If this works, the Cloud Function should be able to send once that env is deployed.

```bash
python test_gmail_send.py --step 2
```

- **If it succeeds:** You should get a second test email. The string in `env.yaml` is valid and works; next check is that the deployed function really gets this value (Step 3).
- **If it fails:** The value in `env.yaml` is wrong or corrupted (e.g. bad copy/paste, YAML escaping, truncated). Update `env.yaml`: copy the **entire** contents of `token.json` as a single line and set `GMAIL_TOKEN_JSON: '<that string>'` (single quotes around the JSON). Then run Step 2 again.

---

## Step 3: Confirm what the Cloud Function sees

After deploying with `./quick_deploy.sh`, check that the function has the token and that it’s valid JSON:

```bash
gcloud functions describe options-dashboard-v2 --gen2 --region=us-east1 --project=investmentprocessor --format="yaml(serviceConfig.environmentVariables)"
```

In the output, find `GMAIL_TOKEN_JSON`. It should be a long single-quoted JSON string. If it’s missing or looks truncated/wrong, fix `env.yaml` and redeploy.

---

## Step 4: Trigger and check logs

Trigger one run and watch for the send result:

```bash
gcloud scheduler jobs run options-dashboard-v2-daily --project=investmentprocessor --location=us-east1
# Wait ~8 minutes, then:
gcloud functions logs read options-dashboard-v2 --gen2 --region=us-east1 --limit=200 | grep -E "Sent|Failed to send|invalid_scope|Gmail"
```

- **"✉️ Sent"** → Email is sending; check inbox (and spam).
- **"Failed to send" / "invalid_scope"** → Token in Cloud is still wrong or expired; refresh token (gmail_auth.py), update `env.yaml`, redeploy, then repeat from Step 2.

---

## Quick reference

| Step | Command | What it checks |
|------|--------|----------------|
| 1 | `python test_gmail_send.py --step 1` | token.json + .env work locally |
| 2 | `python test_gmail_send.py --step 2` | env.yaml GMAIL_TOKEN_JSON works like Cloud |
| 3 | `gcloud functions describe ... --format="yaml(serviceConfig.environmentVariables)"` | Deployed env has correct token |
| 4 | Scheduler run + logs grep | Function actually sends or errors |
