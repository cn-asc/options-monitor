#!/usr/bin/env python3
"""
Build env.yaml from .env for Cloud deploy. Single source of truth: .env.

- Reads .env (KEY=value, skips comments/empty).
- Writes env.yaml with quoted values (safe for special chars).
- If GMAIL_TOKEN_JSON is not in .env but exists in env.yaml, keeps it from env.yaml
  (so you can keep the token only in env.yaml if you prefer).

Usage: python3 build_env_yaml.py
  Reads .env, writes env.yaml.
"""

import os
import re
import sys

def load_dotenv(path: str) -> dict:
    out = {}
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
            if not m:
                continue
            key, value = m.group(1), m.group(2).strip()
            if len(value) >= 2 and (value[0], value[-1]) in (('"', '"'), ("'", "'")):
                value = value[1:-1].replace('\\"', '"').replace("\\'", "'")
            out[key] = value
    return out

def load_existing_yaml(path: str) -> dict:
    """Parse minimal YAML: KEY: value (one line). Used to preserve GMAIL_TOKEN_JSON from existing env.yaml."""
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, "r") as f:
        for line in f:
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.+)$", line.rstrip())
            if not m:
                continue
            key, v = m.group(1), m.group(2).strip()
            if "\n" in v:
                continue
            if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
                v = v[1:-1].replace('\\"', '"')
            elif len(v) >= 2 and v[0] == "'" and v[-1] == "'":
                v = v[1:-1].replace("\\'", "'")
            out[key] = v
    return out

def yaml_escape(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(script_dir, ".env")
    out_path = os.path.join(script_dir, "env.yaml")

    if not os.path.isfile(env_path):
        print("No .env found; cannot build env.yaml.", file=sys.stderr)
        sys.exit(1)

    vars_from_env = load_dotenv(env_path)

    # GMAIL_TOKEN_JSON often lives only in env.yaml (long JSON); preserve it if missing from .env
    if "GMAIL_TOKEN_JSON" not in vars_from_env:
        existing = load_existing_yaml(out_path)
        if existing.get("GMAIL_TOKEN_JSON"):
            vars_from_env["GMAIL_TOKEN_JSON"] = existing["GMAIL_TOKEN_JSON"]

    lines = []
    for k, v in vars_from_env.items():
        lines.append(f"{k}: {yaml_escape(str(v))}")

    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Wrote {out_path} from .env ({len(vars_from_env)} vars).", file=sys.stderr)

if __name__ == "__main__":
    main()
