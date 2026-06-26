# Session Summary — June 8, 2026

## CUCZZUS P&L (May 2026)
| Market | Revenue | Profit | Margin |
|---|---|---|---|
| US | $43,223 | ~$11,000 | 26% |
| CA | CAD$29,134 | -CAD$687 | -2% |
| AU | AUD$27,901 | ~$4,800 | 27% |
| JP | ¥149,112 | PENDING (need rate card) | |

## BOOLUU P&L (May 2026)
| Market | Revenue | Profit | Margin |
|---|---|---|---|
| US | $24,972 | $1,848 | 7.4% |
| CA | CAD$48,744 | ~$8,136 | 22% (ads pending) |

## Confirmed formulas
- US FBA: CEIL(lb) × rate card
- CA FBA: CEIL(500g increments) × rate card  
- AU FBA: actual weight × rate card
- Returns: -(price × 0.85) per return
- Storage: CBM_sold × rate × 0.5
- Disposal: $15.38/unit (oversize)

## Key rules
- Always use RAW MODE prices (not average)
- CAD × 0.76 → USD, AUD × 0.66 → USD, JPY × 0.0067 → USD
- CNY COGS × 0.14 → USD
- Sea freight: US per-kg, CA/AU/JP per-CBM
- Ads: use Ads API when available, else Finances API proxy

## TODO next session
- [ ] BOOLUU CA ads report
- [ ] JP rate card → JP P&L
- [ ] Heliumx store
- [ ] BOOLUU missing dims (4 US, 1 CA)
- [ ] EU token refresh
- [ ] Compare storage estimate vs actual
