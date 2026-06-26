#!/usr/bin/env python3
"""Run ads health scan — write incrementally."""
import sys, traceback
out = open("/tmp/ads_health_output.txt", "w")
sys.stdout = out
sys.stderr = out
print("Starting ads health scan...", flush=True)
try:
    from engines.ads_health_scanner import scan, print_scan, load_env
    load_env()
    for store in ["CUCZUUS", "BOOLUU", "Heliumx"]:
        print(f"\n=== Scanning {store} ===", flush=True)
        issues, totals = scan(store)
        print_scan(store, issues, totals)
        import time
        time.sleep(2)
    print("\n✅ All scans complete", flush=True)
except Exception as e:
    print(f"\nERROR: {e}", flush=True)
    traceback.print_exc()
out.close()
