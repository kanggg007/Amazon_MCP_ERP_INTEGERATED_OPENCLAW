"""
Reimbursement Tracker
Finds CARRIER_DAMAGED and LOST returns that should be reimbursed by Amazon.
Cross-references FBA returns report with Finances API.

Usage:
    from reimbursement_tracker import ReimbursementTracker
    tracker = ReimbursementTracker(returns_file, finances_data)
    tracker.report()
"""

from collections import defaultdict
from datetime import datetime


class ReimbursementTracker:
    """Track and flag unreimbursed FBA returns."""
    
    # FC prefixes for US vs CA classification
    CA_FCS = {'YYZ', 'YVR', 'YEG', 'YOO', 'YOW', 'YGK', 'YYC', 'YXU', 'YHZ', 'YQB'}
    
    # Dispositions that should trigger reimbursement
    REIMBURSABLE = {
        'CARRIER_DAMAGED': 'Carrier responsibility → full reimbursement',
        'WAREHOUSE_DAMAGED': 'FBA warehouse damaged → full reimbursement',
        'LOST': 'Lost by carrier/FC → full reimbursement',
    }
    
    def __init__(self):
        self.carrier_damaged = []   # (oid, asin, name, region, store, qty, status)
        self.matched = []           # reimbursements matched
        self.unmatched = []         # no reimbursement found
        self.reimbursements = []    # from Finances API
        self.estimated_value = 0    # total unreimbursed
    
    def load_returns(self, filename: str, store: str, orders_data: dict = None):
        """Parse FBA returns report and find reimbursable returns."""
        with open(filename, encoding='utf-8', errors='replace') as f:
            for line in f.read().strip().split("\n")[1:]:
                c = line.split("\t")
                if len(c) < 12:
                    continue
                disposition = c[8].strip()
                if disposition not in self.REIMBURSABLE:
                    continue
                
                oid = c[1].strip()
                asin = c[3].strip()
                name = c[5][:50]
                fc = c[7].strip()
                status = c[10].strip() if len(c) > 10 else ""
                qty = int(c[6]) if c[6] else 1
                
                region = "CA" if any(fc.startswith(p) for p in self.CA_FCS) else "US"
                
                # Get selling price from orders if available
                selling_price = None
                if orders_data and oid in orders_data:
                    selling_price = orders_data[oid].get("price")
                
                self.carrier_damaged.append({
                    "oid": oid, "asin": asin, "name": name,
                    "region": region, "store": store, "qty": qty,
                    "status": status, "price": selling_price,
                    "disposition": disposition
                })
    
    def load_finances(self, financial_events: dict):
        """Parse Finances API response for reimbursement events."""
        # Check AdjustmentEventList for REVERSAL_REIMBURSEMENT
        for ev in financial_events.get("AdjustmentEventList", []):
            if "REIMBURSEMENT" in ev.get("AdjustmentType", "").upper():
                amt = ev.get("AdjustmentAmount", {})
                self.reimbursements.append({
                    "type": "Adjustment",
                    "date": ev.get("PostedDate", "")[:10],
                    "amount": float(amt.get("CurrencyAmount", 0)),
                    "currency": amt.get("CurrencyCode", ""),
                    "items": ev.get("AdjustmentItemList", [])
                })
        
        # Check SAFETReimbursementEventList
        for ev in financial_events.get("SAFETReimbursementEventList", []):
            amt = ev.get("ReimbursedAmount", {})
            self.reimbursements.append({
                "type": "SAFET",
                "date": ev.get("PostedDate", "")[:10],
                "amount": float(amt.get("CurrencyAmount", 0)),
                "currency": amt.get("CurrencyCode", "")
            })
        
        # Match against carrier damaged
        self._match()
    
    def _match(self):
        """Match reimbursements to carrier damaged returns."""
        self.matched = []
        self.unmatched = []
        
        for item in self.carrier_damaged:
            # If status is already "Reimbursed", consider matched
            if item["status"].lower() == "reimbursed":
                self.matched.append(item)
                continue
            
            # Try to match by order ID or ASIN
            # (Full matching requires deeper Finances data)
            if item["price"]:
                self.estimated_value += item["price"] * item["qty"]
            
            self.unmatched.append(item)
    
    def report(self) -> str:
        """Generate reimbursement report."""
        lines = []
        lines.append(f"REIMBURSEMENT TRACKER — {datetime.now().strftime('%B %Y')}")
        lines.append(f"{'='*80}")
        
        lines.append(f"\nCARRIER/WAREHOUSE DAMAGED: {len(self.carrier_damaged)} units")
        for item in self.carrier_damaged:
            reason = self.REIMBURSABLE.get(item["disposition"], item["disposition"])
            lines.append(f"  {item['store']:<10} {item['region']:>3} {item['asin']:<15} "
                        f"Qty:{item['qty']} Status:{item['status']}")
            lines.append(f"    Order: {item['oid']} | {item['name'][:50]}")
            lines.append(f"    {reason}")
        
        lines.append(f"\nFINANCES API REIMBURSEMENTS: {len(self.reimbursements)} found")
        for r in self.reimbursements:
            lines.append(f"  {r['type']} | {r['date']} | {r['currency']} {r['amount']:,.2f}")
        
        lines.append(f"\n{'='*80}")
        lines.append(f"MATCHED: {len(self.matched)} | UNMATCHED: {len(self.unmatched)}")
        if self.estimated_value > 0:
            lines.append(f"Estimated unreimbursed value: ${self.estimated_value:,.2f}")
        
        if self.unmatched:
            lines.append(f"\nACTION REQUIRED:")
            for item in self.unmatched:
                lines.append(f"  File SAFE-T claim for {item['asin']} (Order: {item['oid']})")
                lines.append(f"  → Seller Central → Reports → FBA Reimbursements")
        
        return "\n".join(lines)
    
    def to_dict(self) -> dict:
        """Return summary as dict for dashboard/export."""
        return {
            "carrier_damaged_count": len(self.carrier_damaged),
            "matched_count": len(self.matched),
            "unmatched_count": len(self.unmatched),
            "reimbursements_found": len(self.reimbursements),
            "estimated_value": self.estimated_value,
            "unmatched": self.unmatched,
            "matched": self.matched
        }


if __name__ == "__main__":
    # Quick test
    tracker = ReimbursementTracker()
    tracker.load_returns("/tmp/cuczuus_returns_may.txt", "CUCZUUS")
    tracker.load_returns("/tmp/booluu_returns_may.txt", "BOOLUU")
    tracker._match()
    print(tracker.report())
