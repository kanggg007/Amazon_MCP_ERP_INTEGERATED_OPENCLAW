import psycopg2, os

# Read env
env_path = "/Users/jiahe/.openclaw/workspace/Planned system project/amazon-ops-intel/.env"
env={}
for line in open(env_path).read().splitlines():
    if line and '=' in line and not line.startswith('#'):
        k,v=line.split('=',1)
        env[k.strip()]=v.strip()

dsn=env['DATABASE_URL']
conn=psycopg2.connect(dsn)
cur=conn.cursor()

# Ads by store/market for July 1
cur.execute("""
    SELECT store, market, SUM(cost), COUNT(*) 
    FROM ads_daily 
    WHERE date = '2026-07-01' 
    GROUP BY store, market 
    ORDER BY store, market
""")
print("July 1 Ads by store/market:")
for row in cur.fetchall():
    print(f"  {row[0]:10} {row[1]:>4} ${row[2] or 0:>8.2f} ({row[3]} rows)")

# Missing stores/markets
cur.execute("""
    SELECT DISTINCT store, market, date FROM ads_daily 
    WHERE date >= '2026-06-28'
    ORDER BY store, market, date
""")
print("\nRecent ads data:")
for row in cur.fetchall():
    print(f"  {row[0]:10} {row[1]:>4} {row[2]}")

conn.close()
