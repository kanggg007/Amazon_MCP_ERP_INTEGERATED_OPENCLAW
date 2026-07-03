import requests, json
r = requests.get("https://amazonmcperpintegeratedopenclaw-production.up.railway.app/admin/db-check", timeout=15)
d = r.json()
print(json.dumps(d, indent=2))
