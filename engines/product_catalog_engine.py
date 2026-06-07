"""
Master Product Catalog — Single Source of Truth
=================================================
One product definition → feeds all engines automatically.
"""

import logging
from decimal import Decimal

from db.connection import get_session
from db.models import MasterProduct, ProductStoreMapping, Store
from auth.store_registry import StoreRegistry

logger = logging.getLogger(__name__)


class ProductCatalogEngine:
    """Single source of truth for all products across stores and markets."""

    def __init__(self):
        pass

    # ─── MASTER PRODUCT CRUD ───

    def upsert_product(self, sku: str, product_name: str = None,
                       category: str = None,
                       length_cm: float = None, width_cm: float = None,
                       height_cm: float = None, weight_kg: float = None,
                       manufacturing_cny: float = None,
                       packaging_cny: float = None,
                       inspection_cny: float = None,
                       exchange_rate: float = None) -> dict:
        """Create or update a master product."""
        session = get_session()
        try:
            prod = session.query(MasterProduct).filter(
                MasterProduct.sku == sku).first()
            if not prod:
                prod = MasterProduct(sku=sku)
                session.add(prod)

            if product_name is not None: prod.product_name = product_name
            if category is not None: prod.category = category
            if length_cm is not None: prod.length_cm = length_cm
            if width_cm is not None: prod.width_cm = width_cm
            if height_cm is not None: prod.height_cm = height_cm
            if weight_kg is not None: prod.weight_kg = weight_kg
            if manufacturing_cny is not None: prod.manufacturing_cost_cny = manufacturing_cny
            if packaging_cny is not None: prod.packaging_cost_cny = packaging_cny
            if inspection_cny is not None: prod.inspection_cost_cny = inspection_cny
            if exchange_rate is not None: prod.exchange_rate = exchange_rate

            session.commit()
            return self._product_to_dict(prod)
        except Exception as e:
            session.rollback()
            return {"error": str(e)}
        finally:
            session.close()

    def get_product(self, sku: str) -> dict:
        """Get master product details with all store mappings."""
        session = get_session()
        try:
            prod = session.query(MasterProduct).filter(
                MasterProduct.sku == sku).first()
            if not prod:
                return {"sku": sku, "configured": False}
            
            result = self._product_to_dict(prod)
            
            # Get all store mappings
            mappings = session.query(ProductStoreMapping).filter(
                ProductStoreMapping.sku == sku,
                ProductStoreMapping.is_active == True).all()
            
            result["store_mappings"] = [
                self._mapping_to_dict(m) for m in mappings
            ]
            result["store_count"] = len(mappings)
            return result
        finally:
            session.close()

    def list_products(self, active_only: bool = True) -> list:
        """List all master products."""
        session = get_session()
        try:
            q = session.query(MasterProduct)
            if active_only:
                q = q.filter(MasterProduct.is_active == True)
            return [self._product_to_dict(p) for p in q.order_by(MasterProduct.sku).all()]
        finally:
            session.close()

    # ─── STORE MAPPING CRUD ───

    def add_mapping(self, sku: str, store_id: str, marketplace_id: str,
                    marketplace_name: str, asin: str,
                    selling_price: float = 0, currency: str = "USD",
                    freight_per_unit_cny: float = 0,
                    cbm_rate_cny: float = 0) -> dict:
        """Map a product to a store + marketplace."""
        session = get_session()
        try:
            mapping = session.query(ProductStoreMapping).filter(
                ProductStoreMapping.store_id == store_id,
                ProductStoreMapping.marketplace_id == marketplace_id,
                ProductStoreMapping.asin == asin).first()
            if not mapping:
                mapping = ProductStoreMapping(
                    sku=sku, store_id=store_id,
                    marketplace_id=marketplace_id,
                    marketplace_name=marketplace_name,
                    asin=asin)
                session.add(mapping)

            mapping.sku = sku
            if selling_price: mapping.selling_price = selling_price
            if currency: mapping.currency = currency
            if freight_per_unit_cny: mapping.freight_per_unit_cny = freight_per_unit_cny
            if cbm_rate_cny: mapping.cbm_rate_cny = cbm_rate_cny

            session.commit()
            return self._mapping_to_dict(mapping)
        except Exception as e:
            session.rollback()
            return {"error": str(e)}
        finally:
            session.close()

    def get_mappings(self, store_id: str = None) -> list:
        """Get all mappings, optionally filtered by store."""
        session = get_session()
        try:
            q = session.query(ProductStoreMapping)
            if store_id:
                q = q.filter(ProductStoreMapping.store_id == store_id)
            return [self._mapping_to_dict(m) for m in q.all()]
        finally:
            session.close()

    def bulk_import(self, products: list[dict]) -> dict:
        """Bulk import products + mappings from a list."""
        success, errors = 0, 0
        for item in products:
            try:
                self.upsert_product(
                    sku=item.get("sku", ""),
                    product_name=item.get("product_name"),
                    length_cm=item.get("length_cm"),
                    width_cm=item.get("width_cm"),
                    height_cm=item.get("height_cm"),
                    weight_kg=item.get("weight_kg"),
                    manufacturing_cny=item.get("manufacturing_cost_cny"),
                    packaging_cny=item.get("packaging_cost_cny"),
                )
                if item.get("store_id") and item.get("asin"):
                    self.add_mapping(
                        sku=item["sku"],
                        store_id=item["store_id"],
                        marketplace_id=item.get("marketplace_id", "ATVPDKIKX0DER"),
                        marketplace_name=item.get("marketplace_name", "US"),
                        asin=item["asin"],
                        selling_price=item.get("selling_price", 0),
                        freight_per_unit_cny=item.get("freight_per_unit_cny", 0),
                        cbm_rate_cny=item.get("cbm_rate_cny", 0),
                    )
                success += 1
            except Exception as e:
                errors += 1
                logger.error(f"Import error: {e}")
        return {"imported": success, "errors": errors}

    def get_true_cost(self, sku: str, store_id: str = None,
                      marketplace: str = "US") -> dict:
        """Get true per-unit cost in USD for a product in a specific market."""
        session = get_session()
        try:
            prod = session.query(MasterProduct).filter(
                MasterProduct.sku == sku).first()
            if not prod:
                return {"error": "Product not found"}

            base_cost_cny = (float(prod.manufacturing_cost_cny or 0) +
                             float(prod.packaging_cost_cny or 0) +
                             float(prod.inspection_cost_cny or 0))
            rate = float(prod.exchange_rate or 0.14)
            base_cost_usd = base_cost_cny * rate

            # Get freight for this store/market
            mkt_id_map = {"US": "ATVPDKIKX0DER", "CA": "A2EUQ1WTGCTBG2",
                         "JP": "A1VC38T7YXB528", "AU": "A39IBJ37TRP1C6",
                         "EU": "A1PA6795UKMFR9"}
            mkt_cols = {"US": "freight_us_slow_cny", "CA": "freight_canada_cny",
                       "JP": "freight_japan_cny", "AU": "freight_australia_cny",
                       "EU": "freight_europe_cny"}

            freight_cny = 0
            if store_id:
                mapping = session.query(ProductStoreMapping).filter(
                    ProductStoreMapping.sku == sku,
                    ProductStoreMapping.store_id == store_id).first()
                if mapping:
                    freight_cny = float(mapping.freight_per_unit_cny or 0)

            total_usd = base_cost_usd + (freight_cny * rate)
            return {
                "sku": sku,
                "base_cost_cny": base_cost_cny,
                "freight_cny": freight_cny,
                "total_cny": base_cost_cny + freight_cny,
                "exchange_rate": rate,
                "total_cost_usd": round(total_usd, 2),
            }
        finally:
            session.close()

    def get_margin(self, sku: str, selling_price: float,
                   store_id: str = None) -> dict:
        """Get true profit margin for a product."""
        cost = self.get_true_cost(sku, store_id=store_id)
        if "error" in cost:
            return cost
        total = cost["total_cost_usd"]
        if total == 0 or selling_price == 0:
            return {"error": "Missing cost or price"}
        margin = selling_price - total
        margin_pct = (margin / selling_price) * 100
        return {
            "sku": sku,
            "selling_price": selling_price,
            "total_cost_usd": total,
            "margin_usd": round(margin, 2),
            "margin_pct": round(margin_pct, 2),
        }

    # ─── HELPERS ───

    def _product_to_dict(self, p: MasterProduct) -> dict:
        cbm = 0
        if p.length_cm and p.width_cm and p.height_cm:
            cbm = float(p.length_cm * p.width_cm * p.height_cm) / 1000000
        return {
            "sku": p.sku,
            "product_name": p.product_name,
            "category": p.category,
            "dimensions_cm": {"length": float(p.length_cm or 0), "width": float(p.width_cm or 0),
                              "height": float(p.height_cm or 0)},
            "weight_kg": float(p.weight_kg or 0),
            "cbm": round(cbm, 6),
            "costs_cny": {"manufacturing": float(p.manufacturing_cost_cny or 0),
                          "packaging": float(p.packaging_cost_cny or 0),
                          "inspection": float(p.inspection_cost_cny or 0)},
            "exchange_rate": float(p.exchange_rate or 0.14),
            "is_active": p.is_active,
        }

    def _mapping_to_dict(self, m: ProductStoreMapping) -> dict:
        return {
            "id": m.id,
            "sku": m.sku,
            "store_id": m.store_id,
            "marketplace": m.marketplace_name,
            "asin": m.asin,
            "selling_price": float(m.selling_price or 0),
            "currency": m.currency,
            "freight_per_unit_cny": float(m.freight_per_unit_cny or 0),
            "cbm_rate_cny": float(m.cbm_rate_cny or 0),
        }
