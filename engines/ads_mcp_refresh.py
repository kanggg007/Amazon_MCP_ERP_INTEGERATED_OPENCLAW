#!/usr/bin/env python3
"""Refresh Amazon Ads MCP access token — runs every 50 min via cron."""
import os, httpx, json, sys
from pathlib import Path

BASE = Path(__file__).parent.parent
CONFIG = Path.home() / ".openclaw" / "openclaw.json"

def load_env():
    for line in open(BASE / ".env").read().splitlines():
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

def refresh_token(client_id, client_secret, refresh_token):
    r = httpx.post("https://api.amazon.com/auth/o2/token",
        json={"grant_type": "refresh_token", "client_id": client_id,
              "client_secret": client_secret, "refresh_token": refresh_token},
        timeout=10)
    if r.status_code != 200:
        raise Exception(f"Token refresh failed: {r.status_code} {r.text[:200]}")
    return r.json()["access_token"]

if __name__ == "__main__":
    load_env()
    
    # Use CUCZUUS as primary (all stores share same permission scope)
    client_id = os.environ["STORE_02_ADS_CLIENT_ID"]
    client_secret = os.environ["STORE_02_ADS_CLIENT_SECRET"]
    refresh_token_val = os.environ["STORE_02_ADS_REFRESH_TOKEN"]
    
    access_token = refresh_token(client_id, client_secret, refresh_token_val)
    
    # Update OpenClaw config
    with open(CONFIG) as f:
        cfg = json.load(f)
    
    cfg["mcp"]["servers"]["amazon-ads"]["headers"]["Amazon-Ads-ClientId"] = client_id
    cfg["mcp"]["servers"]["amazon-ads"]["headers"]["Authorization"] = f"Bearer {access_token}"
    
    with open(CONFIG, "w") as f:
        json.dump(cfg, f, indent=2)
    
    print(f"Token refreshed: {access_token[:10]}...")
