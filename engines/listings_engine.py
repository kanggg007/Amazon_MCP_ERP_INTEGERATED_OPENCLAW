"""
SP-API Listings Engine — Create, read, update, delete Amazon product listings.
"""
import httpx, json, logging
from typing import Optional, Dict
from auth.token_manager import get_access_token
from auth.aws_signer import get_aws_signer

logger = logging.getLogger("amazon.listings")

class ListingsEngine:
    """Manage Amazon product listings via SP-API."""
    
    def __init__(self):
        self.base = "https://sellingpartnerapi-na.amazon.com"
    
    def _headers(self, store_id: str, method: str, path: str, body: Optional[Dict] = None):
        """Get signed headers for SP-API request."""
        from auth.store_registry import get_store_registry
        registry = get_store_registry()
        store = registry.get_store(store_id)
        if not store:
            raise ValueError(f"Store {store_id} not found")
        
        token = get_access_token(store)
        signer = get_aws_signer(store)
        
        # Minimal headers for signing
        headers = {
            "x-amz-access-token": token,
            "host": "sellingpartnerapi-na.amazon.com",
            "user-agent": "AmazonOpsIntel/1.0"
        }
        
        signed = signer.sign(method, path, headers, body)
        signed["Content-Type"] = "application/json"
        return signed
    
    def _request(self, store_id: str, method: str, path: str, body: Optional[Dict] = None):
        """Make SP-API request with retry."""
        headers = self._headers(store_id, method, path, body)
        url = self.base + path
        
        for attempt in range(3):
            r = httpx.request(method, url, headers=headers, json=body, timeout=30)
            if r.status_code in (200, 201, 202):
                return r.json()
            if r.status_code == 429:
                import time; time.sleep(10 * (2**attempt))
                continue
            raise Exception(f"SP-API {r.status_code}: {r.text[:300]}")
        raise Exception("Rate limited after retries")
    
    def get_listing(self, store_id: str, sku: str) -> Dict:
        """Get listing details for a SKU."""
        seller_id = self._get_seller_id(store_id)
        path = f"/listings/2021-08-01/items/{seller_id}/{sku}"
        return self._request(store_id, "GET", path)
    
    def create_or_update(self, store_id: str, sku: str, data: Dict) -> Dict:
        """Create or update a listing."""
        seller_id = self._get_seller_id(store_id)
        path = f"/listings/2021-08-01/items/{seller_id}/{sku}"
        return self._request(store_id, "PUT", path, data)
    
    def delete_listing(self, store_id: str, sku: str) -> Dict:
        """Delete a listing."""
        seller_id = self._get_seller_id(store_id)
        path = f"/listings/2021-08-01/items/{seller_id}/{sku}"
        return self._request(store_id, "DELETE", path)
    
    def _get_seller_id(self, store_id: str) -> str:
        """Get seller ID from store config."""
        from auth.store_registry import get_store_registry
        registry = get_store_registry()
        store = registry.get_store(store_id)
        return store.seller_id if hasattr(store, 'seller_id') else store.merchant_id
