"""
Real-time FX rate fetcher for Amazon P&L.
Primary: Sina Finance (works from China)
Fallback: ExchangeRate-API + Bank of China
"""

import urllib.request
import json
import re
from datetime import datetime

# Exchange rate pairs: (from, to, sina_symbol)
PAIRS = {
    ("CNY", "USD"): ("USDCNY", lambda x: 1/x),  # Sina gives USDCNY, invert
    ("CNY", "CAD"): ("CADCNY", lambda x: 1/x),
    ("CNY", "AUD"): ("AUDCNY", lambda x: 1/x),
    ("CNY", "JPY"): ("JPYCNY", lambda x: 1/x),  # Sina gives JPY per 1 CNY
    ("CNY", "EUR"): ("EURCNY", lambda x: 1/x),
}

# Monthly averages (update at start of each month)
# Derived from: CNY→USD=0.14, USD→CAD=0.76, USD→AUD=0.66, USD→JPY=0.0067
MONTHLY_AVERAGES = {
    "2026-05": {
        "CNY": {"USD": 0.14, "CAD": 0.184, "AUD": 0.22, "JPY": 20.9, "EUR": 0.128},
    }
}


def _sina_fetch(symbol: str) -> float:
    """Fetch spot rate from Sina Finance."""
    url = f"https://hq.sinajs.cn/list=fx_s{symbol.lower()}"
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    data = urllib.request.urlopen(req, timeout=10).read().decode("gbk")
    # Format: var hq_str_fx_sXXX="...,price,...";
    match = re.search(r'"([^"]*)"', data)
    if not match:
        raise ValueError(f"Sina parse error: {data[:100]}")
    parts = match.group(1).split(",")
    return float(parts[1])  # Current price


def _exchangerate_fetch(from_cur: str, to_cur: str) -> float:
    """Fallback: ExchangeRate-API (free, no key)."""
    url = f"https://api.exchangerate-api.com/v4/latest/{from_cur}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=10).read())
    return data["rates"].get(to_cur, 0)


def get_spot_rate(from_currency: str, to_currency: str) -> float:
    """Get current spot rate. Uses Sina Finance (China-accessible)."""
    pair = (from_currency, to_currency)
    if pair in PAIRS:
        symbol, transform = PAIRS[pair]
        raw = _sina_fetch(symbol)
        return transform(raw)
    
    # Try fallback
    try:
        return _exchangerate_fetch(from_currency, to_currency)
    except:
        return 0


def get_monthly_average(from_currency: str, to_currency: str, month: str = None) -> float:
    """Get monthly average rate. Falls back to spot if no historical data."""
    if month is None:
        month = datetime.now().strftime("%Y-%m")
    
    if month in MONTHLY_AVERAGES:
        rates = MONTHLY_AVERAGES[month]
        if from_currency in rates and to_currency in rates[from_currency]:
            return rates[from_currency][to_currency]
    
    # Fallback: try spot rate
    try:
        return get_spot_rate(from_currency, to_currency)
    except:
        return 0


def convert(amount: float, from_currency: str, to_currency: str, month: str = "2026-05") -> float:
    """Convert amount using monthly average."""
    rate = get_monthly_average(from_currency, to_currency, month)
    if rate == 0:
        raise ValueError(f"No rate for {from_currency}→{to_currency}")
    return amount * rate


def get_all_market_rates(month: str = "2026-05"):
    """Get all CNY → market currency conversion rates."""
    return {
        "US": get_monthly_average("CNY", "USD", month),
        "CA": get_monthly_average("CNY", "CAD", month),
        "AU": get_monthly_average("CNY", "AUD", month),
        "JP": get_monthly_average("CNY", "JPY", month),
        "DE": get_monthly_average("CNY", "EUR", month),
    }


if __name__ == "__main__":
    print("=== Spot Rates (Sina Finance) ===")
    for pair, (sym, _) in PAIRS.items():
        try:
            rate = get_spot_rate(*pair)
            print(f"  {pair[0]} → {pair[1]}: {rate:.6f}")
        except Exception as e:
            print(f"  {pair[0]} → {pair[1]}: ERROR ({e})")
    
    print(f"\n=== May 2026 Averages ===")
    for mkt, rate in get_all_market_rates().items():
        print(f"  CNY → {mkt}: {rate:.4f}")
