#!/usr/bin/env python3
"""
Step-by-step Gmail token troubleshooting.

Step 1: Send using token.json only (same as local run). Proves the token file works.
Step 2: Send using GMAIL_TOKEN_JSON loaded from env.yaml (same as Cloud Function). Proves the deployed value works.

Usage:
  python test_gmail_send.py --step 1    # use token.json, .env for SEND_FROM/SEND_TO
  python test_gmail_send.py --step 2    # use GMAIL_TOKEN_JSON from env.yaml
"""
import argparse
import os
import sys

# Load .env for SEND_FROM, SEND_TO (used in both steps)
from dotenv import load_dotenv
load_dotenv()

SEND_FROM = os.getenv("SEND_FROM")
SEND_TO = os.getenv("SEND_TO")
if not SEND_FROM or not SEND_TO:
    print("❌ Set SEND_FROM and SEND_TO in .env")
    sys.exit(1)


def get_gmail_token_json_from_env_yaml():
    """Read env.yaml and return the GMAIL_TOKEN_JSON value (the raw JSON string)."""
    path = os.path.join(os.path.dirname(__file__), "env.yaml")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"env.yaml not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip().startswith("GMAIL_TOKEN_JSON:"):
                rest = line.split(":", 1)[1].strip()
                # YAML single-quoted: '...' -> strip outer quotes; '' inside is escaped '
                if rest.startswith("'") and rest.endswith("'"):
                    rest = rest[1:-1].replace("''", "'")
                return rest
    raise ValueError("GMAIL_TOKEN_JSON not found in env.yaml")


def run_step1():
    """Use token.json only (unset GMAIL_TOKEN_JSON)."""
    print("Step 1: Send using token.json (local style)")
    print("  - Unsetting GMAIL_TOKEN_JSON so app uses token.json")
    if "GMAIL_TOKEN_JSON" in os.environ:
        del os.environ["GMAIL_TOKEN_JSON"]
    # Import after env is set so app_v2 sees no GMAIL_TOKEN_JSON
    from app_v2 import gmail_service, send_gmail_html
    svc = gmail_service()
    subject = "Options Dashboard — Gmail test (step 1: token.json)"
    body = "<p>If you get this, token.json works locally.</p>"
    send_gmail_html(svc, SEND_FROM, SEND_TO, subject, body)
    print("  ✅ Step 1 passed: email sent using token.json")


def run_step2():
    """Use GMAIL_TOKEN_JSON from env.yaml (Cloud-style)."""
    print("Step 2: Send using GMAIL_TOKEN_JSON from env.yaml (Cloud-style)")
    token_value = get_gmail_token_json_from_env_yaml()
    # Validate JSON parses
    import json
    try:
        data = json.loads(token_value)
    except json.JSONDecodeError as e:
        print(f"  ❌ env.yaml GMAIL_TOKEN_JSON is not valid JSON: {e}")
        sys.exit(1)
    if "refresh_token" not in data or "client_id" not in data:
        print("  ❌ env.yaml GMAIL_TOKEN_JSON missing refresh_token or client_id")
        sys.exit(1)
    print(f"  - Loaded token from env.yaml (scopes: {data.get('scopes', [])})")
    os.environ["GMAIL_TOKEN_JSON"] = token_value
    from app_v2 import gmail_service, send_gmail_html
    svc = gmail_service()
    subject = "Options Dashboard — Gmail test (step 2: env.yaml token)"
    body = "<p>If you get this, the token in env.yaml works (same as Cloud).</p>"
    send_gmail_html(svc, SEND_FROM, SEND_TO, subject, body)
    print("  ✅ Step 2 passed: email sent using env.yaml GMAIL_TOKEN_JSON")


def main():
    parser = argparse.ArgumentParser(description="Gmail token troubleshooting")
    parser.add_argument("--step", type=int, choices=[1, 2], required=True, help="1=token.json, 2=env.yaml")
    args = parser.parse_args()
    if args.step == 1:
        run_step1()
    else:
        run_step2()


if __name__ == "__main__":
    main()
