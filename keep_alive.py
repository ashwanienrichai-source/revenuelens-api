"""
keep_alive.py — Render Free Tier Keep-Alive Cron Job

Deploy this as a Render Cron Job (free):
  Command: python keep_alive.py  
  Schedule: Every 14 minutes (*/14 * * * *)

This pings the API every 14 minutes to prevent Render's
15-minute inactivity spin-down on free tier.

Setup in Render Dashboard:
  + New → Cron Job
  → Command: python keep_alive.py
  → Schedule: */14 * * * *
  → Instance: Free
"""
import urllib.request
import urllib.error
import sys
from datetime import datetime

API_URL = "https://revenuelens-api.onrender.com/health"

try:
    req = urllib.request.Request(API_URL, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        status = resp.status
        print(f"[{datetime.now().isoformat()}] ✓ API warm — status {status}")
        sys.exit(0)
except urllib.error.URLError as e:
    print(f"[{datetime.now().isoformat()}] ✗ Ping failed: {e.reason}")
    sys.exit(1)
except Exception as e:
    print(f"[{datetime.now().isoformat()}] ✗ Error: {e}")
    sys.exit(1)
