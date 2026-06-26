#!/bin/bash
# Ads ingestion wrapper for cron — runs at 2 PM China time
export PATH="/usr/local/bin:/usr/bin:/bin:/Library/Developer/CommandLineTools/usr/bin:$PATH"
cd "/Users/jiahe/.openclaw/workspace/Planned system project/amazon-ops-intel"
/usr/bin/python3 engines/ads_ingestion.py >> /tmp/ads_ingestion_cron.log 2>&1
echo "$(date): Done" >> /tmp/ads_ingestion_cron.log
