#!/usr/bin/env python3
"""Split master ads report into per-store files for sub admins."""
from pathlib import Path

MASTER = Path("/Users/jiahe/.openclaw/workspace/ads_health_report.md")
STORES = ["CUCZUUS", "BOOLUU", "Heliumx"]

if not MASTER.exists():
    print("No master report yet. Run ads_daily_scan.py first.")
    exit(1)

text = MASTER.read_text()
    
for store in STORES:
    lines = []
    in_store = False
    for line in text.split("\n"):
        if line.startswith("## ") and store in line:
            in_store = True
        elif line.startswith("## ") and in_store:
            in_store = False
        if in_store:
            lines.append(line)
    
    if lines:
        out = Path(f"/Users/jiahe/.openclaw/workspace/ads_health_{store.lower()}.md")
        out.write_text("\n".join(lines))
        print(f"✅ {store}: {out}")
