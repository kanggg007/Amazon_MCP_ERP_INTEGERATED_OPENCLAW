"""
Listing Image Upload — Accepts Feishu image URLs, stores in DB, passes to SP-API
When sub admin sends photos on Feishu, bot extracts Feishu CDN URLs and creates listing.
"""
import os, httpx, json, logging
from datetime import datetime

logger = logging.getLogger("amazon.listings.images")

class ListingCreator:
    """Create Amazon listing with images from Feishu."""
    
    def __init__(self):
        pass
    
    def create_from_feishu(self, store: str, sku: str, text_data: dict, image_urls: list):
        """
        Create a full listing from Feishu input.
        
        text_data: {
            "title", "brand", "manufacturer", "productType",
            "price", "quantity",
            "bulletPoints": [...5 items],
            "description",
            "packageLength", "packageWidth", "packageHeight", "packageWeight",
            "itemLength", "itemWidth", "itemHeight",
            "searchTerms": [...],
            "category"
        }
        image_urls: ["https://open.feishu.cn/...", "https://open.feishu.cn/..."]
        """
        
        # Build listing payload
        listing = {
            "sku": sku,
            "title": text_data.get("title", ""),
            "brand": text_data.get("brand", ""),
            "manufacturer": text_data.get("manufacturer", ""),
            "productType": text_data.get("productType", "HOME_BATHROOM_MIRROR"),
            "standardPrice": float(text_data.get("price", 0)),
            "quantity": int(text_data.get("quantity", 0)),
            "currency": "USD",
            "bulletPoints": text_data.get("bulletPoints", [])[:5],
            "description": text_data.get("description", ""),
            "packageDimensions": {
                "length": float(text_data.get("packageLength", 0)),
                "width": float(text_data.get("packageWidth", 0)),
                "height": float(text_data.get("packageHeight", 0)),
                "unit": "cm"
            },
            "packageWeight": {
                "value": float(text_data.get("packageWeight", 0)),
                "unit": "kg"
            },
            "itemDimensions": {
                "length": float(text_data.get("itemLength", 0)),
                "width": float(text_data.get("itemWidth", 0)),
                "height": float(text_data.get("itemHeight", 0)),
                "unit": "cm"
            },
            "mainImageUrl": image_urls[0] if image_urls else "",
            "otherImageUrls": image_urls[1:8] if len(image_urls) > 1 else [],
            "searchTerms": text_data.get("searchTerms", []),
            "category": text_data.get("category", ""),
        }
        
        # Call SP-API listings endpoint
        return self._create_via_sp_api(store, sku, listing)
    
    def _create_via_sp_api(self, store_id: str, sku: str, listing: dict):
        """Send listing to SP-API Listings API."""
        from auth.token_manager import TokenManager
        from auth.aws_signer import AWSSigner
        from auth.store_registry import get_store_registry
        
        registry = get_store_registry()
        store = registry.get_store(store_id)
        tm = TokenManager(registry)
        token = tm.get_token_sync(store_id) if hasattr(tm, 'get_token_sync') else "TODO"
        signer = AWSSigner(store)
        
        seller_id = store.seller_id if hasattr(store, 'seller_id') else "TODO"
        host = "sellingpartnerapi-na.amazon.com"
        path = f"/listings/2021-08-01/items/{seller_id}/{sku}"
        
        headers = {
            "x-amz-access-token": token,
            "host": host,
            "user-agent": "AmazonOpsIntel/1.0",
            "Content-Type": "application/json"
        }
        
        signed = signer.sign("PUT", path, {}, headers)
        
        r = httpx.put(f"https://{host}{path}", headers=signed, json=listing, timeout=30)
        
        if r.status_code in (200, 201, 202):
            logger.info(f"Listing created: {store_id}/{sku}")
            return {"status": "ok", "sku": sku, "detail": r.json()}
        else:
            logger.error(f"Listing FAIL: {r.status_code} {r.text[:300]}")
            return {"status": "error", "code": r.status_code, "detail": r.text[:500]}


# --- Feishu integration ---
def parse_feishu_listing(text: str, image_urls: list = None) -> dict:
    """
    Parse a listing from Feishu message text.
    Expected format: key: value, one per line.
    Photos are attached separately.
    """
    lines = text.strip().split("\n")
    data = {}
    bullets = []
    in_bullets = False
    
    for line in lines:
        line = line.strip()
        if not line: continue
        
        if "bullet" in line.lower() or line.startswith(("1.", "2.", "3.", "4.", "5.")):
            in_bullets = True
            bullet = line.split(".", 1)[-1].strip()
            if bullet: bullets.append(bullet)
            continue
        elif in_bullets and ":" in line:
            in_bullets = False
        
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip().lower().replace(" ", "_")
            value = value.strip()
            
            # Map common Feishu field names
            key_map = {
                "store": "store", "sku": "sku", "title": "title", "brand": "brand",
                "price": "price", "quantity": "quantity", "description": "description",
                "package_length": "packageLength", "package_width": "packageWidth",
                "package_height": "packageHeight", "weight": "packageWeight",
                "item_length": "itemLength", "item_width": "itemWidth", "item_height": "itemHeight",
                "category": "category", "product_type": "productType",
            }
            
            mapped = key_map.get(key, key)
            if value: data[mapped] = value
    
    if bullets: data["bulletPoints"] = bullets[:5]
    if image_urls: data["image_urls"] = image_urls
    
    return data
