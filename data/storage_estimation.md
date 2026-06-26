# Storage Fee Estimation — Marked June 8, 2026

## Formula
```
Storage = total_CBM_sold × rate × avg_inventory_months
```

## Rates
| Market | Standard | Oversize | Per |
|---|---|---|---|
| US | $0.83/cu ft | $0.53/cu ft | month |
| CA | CAD$40/cbm | CAD$28/cbm | month |
| AU | ~AUD$23/cbm | ~AUD$16/cbm | month |

## Default: avg_inventory_months = 0.5 (15 days)
- Fast-selling products: use 0.25 (7 days)
- Slow/dead stock: use 1.0 (full month)

## CBM → Cubic Feet: × 35.315

## TODO: compare vs actual
- Wait for June settlement (storage posts ~June 10-15)
- Compare estimate vs actual
- Adjust avg_inventory_months based on actual
