#!/usr/bin/env python3
"""
CUCZUUS Daily Revenue Report
Run daily at 5pm China time via cron or heartbeat.
Pulls order #, selling price, country, product name.
Converts to USD using Sina FX rates.
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from datetime import datetime, timedelta
import urllib.request, re
from collections import defaultdict

# === Fetch Sina FX rates ===
def sina_rate(sym):
    url = f"https://hq.sinajs.cn/list=fx_s{sym.lower()}"
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    data = urllib.request.urlopen(req, timeout=10).read().decode("gbk")
    m = re.search(r'"([^"]*)"', data)
    return 1 / float(m.group(1).split(",")[1]) if m else 0

rates = {"USD": sina_rate("USDCNY"), "CAD": sina_rate("CADCNY"),
         "AUD": sina_rate("AUDCNY"), "EUR": sina_rate("EURCNY")}
# JPY special handling
data = urllib.request.urlopen(urllib.request.Request(
    "https://hq.sinajs.cn/list=fx_sjpycny", headers={"Referer": "https://finance.sina.com.cn"}), timeout=10).read().decode("gbk")
rates["JPY"] = 1 / float(re.search(r'"([^"]*)"', data).group(1).split(",")[1])

# === Parse orders ===
# NOTE: Pull fresh orders report from SP-API or use latest cached file
# For demo, uses cached files. Replace with SP-API pull in production.

ORDER_FILES = [
    ("/tmp/na_orders.txt", "NA"),       # US + CA
    ("/tmp/au_orders.txt", "AU"),
    ("/tmp/cuczuus_de_orders.txt", "EU"),
]

daily = defaultdict(lambda: {"rev_usd": 0, "orders": []})

for fname, mkt in ORDER_FILES:
    try:
        with open(fname) as f:
            for line in f.read().strip().split("\n")[1:]:
                c = line.split("\t")
                if len(c) < 27 and "na_orders" in fname: continue
                try:
                    date = c[2][:10]; oid = c[0].strip()[:20]
                    asin = c[11].strip(); name = c[9][:40]
                    price = float(c[15]) if c[15] else 0
                    country = c[26].strip() if len(c) > 26 else mkt
                    if "non-amazon" in str(c[6]).lower(): continue
                    if price <= 0: continue
                except: continue

                # Map country to currency
                curr_map = {"US": "USD", "CA": "CAD", "AU": "AUD", "JP": "JPY",
                            "DE": "EUR", "FR": "EUR", "AT": "EUR", "CH": "EUR"}
                curr = curr_map.get(country, "USD")
                usd = price * rates[curr]

                # Today only
                today = datetime.now().strftime("%Y-%m-%d")
                if date != today: continue

                daily[date]["rev_usd"] += usd
                daily[date]["orders"].append(
                    f"{oid} | {asin} | {name[:30]} | {curr}{price:,.0f} | {country}"
                )
    except FileNotFoundError:
        print(f"[WARN] File not found: {fname}")

# === Report ===
today = datetime.now().strftime("%Y-%m-%d")
if today in daily:
    d = daily[today]
    print(f"\n{'='*60}")
    print(f"CUCZUUS Daily Revenue — {today} (5pm CST)")
    print(f"Total: ${d['rev_usd']:,.0f} USD | {len(d['orders'])} orders")
    print(f"FX Rates (Sina): CNY/USD={1/rates['USD']:.4f} | CNY/CAD={1/rates['CAD']:.4f}")
    print(f"{'='*60}")
    for o in d["orders"]:
        print(f"  {o}")
else:
    print(f"No orders found for {today}")

# === Ads Summary (from PostgreSQL, ingested at 1 PM) ===
print(f"\n{'='*60}")
print("Ads Spend Summary (June 25 from ads_daily)")
print(f"{'='*60}")
try:
    import psycopg2
    dsn = os.environ.get("DATABASE_URL", "")
    if dsn:
        conn = psycopg2.connect(dsn)
        cur = conn.cursor()
        cur.execute("SELECT store, market, SUM(cost), SUM(sales) FROM ads_daily WHERE report_type='sp_campaigns' GROUP BY store, market ORDER BY store, market")
        total_cost = 0; total_sales = 0
        for r in cur.fetchall():
            store, market, cost, sales = r
            cost = float(cost); sales = float(sales)
            acos = (cost/sales*100) if sales else 0
            total_cost += cost; total_sales += sales
            print(f"  {store:<15} {market:<5} Spend: \${cost:>8,.2f} | Sales: \${sales:>8,.2f} | ACOS: {acos:>5.0f}%")
        total_acos = (total_cost/total_sales*100) if total_sales else 0
        print(f"  {'─'*55}")
        print(f"  {'TOTAL':<15} {'':5} Spend: \${total_cost:>8,.2f} | Sales: \${total_sales:>8,.2f} | ACOS: {total_acos:>5.0f}%")
        conn.close()
except Exception as e:
    print(f"  [Ads data not available: {e}]")
