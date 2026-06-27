"""
Amazon Product Listing Template — sent to Feishu sub admins for listing creation.
POST /listings/create with filled template fields.
"""
LISTING_TEMPLATE = {
    "store": "CUCZUUS",          # Store: CUCZUUS, BOOLUU, Heliumx
    "sku": "MY-SKU-001",         # Your SKU
    "listing": {
        "productType": "HOME_BATHROOM_MIRROR",  # Amazon product type
        "title": "Product Title — max 200 chars",
        "brand": "CUCZUUS",
        "description": "Product description with bullet points",
        "bulletPoints": [
            "Key feature 1",
            "Key feature 2",
            "Key feature 3",
            "Key feature 4",
            "Key feature 5"
        ],
        "manufacturer": "CUCZUUS",
        "searchTerms": ["mirror", "bathroom", "LED"],
        
        # Pricing
        "standardPrice": 39.99,
        "currency": "USD",
        "quantity": 100,
        
        # Dimensions & Weight
        "packageDimensions": {
            "length": 105,   # cm
            "width": 85,     # cm
            "height": 5,     # cm
            "unit": "cm"
        },
        "packageWeight": {
            "value": 10.3,   # kg
            "unit": "kg"
        },
        
        # Images (URLs to existing Amazon-hosted images)
        "mainImageUrl": "https://m.media-amazon.com/images/I/...",
        "otherImageUrls": [
            "https://m.media-amazon.com/images/I/...",
            "https://m.media-amazon.com/images/I/..."
        ],
        
        # Category
        "category": "Home & Kitchen > Bath > Bathroom Accessories > Mirrors"
    }
}

# --- Example for Feishu ---
FEISHU_TEMPLATE = """
📦 New Product Listing
━━━━━━━━━━━━━━━━━━━━
Store: CUCZUUS
SKU: _______
Product Type: HOME_BATHROOM_MIRROR

Title: _______
Brand: _______
Price (USD): _______
Quantity: _______

📏 Dimensions (cm): L ___ x W ___ x H ___
⚖️ Weight (kg): ___

📝 Bullet Points:
  1. _______
  2. _______
  3. _______
  4. _______
  5. _______

🖼️ Main Image URL: _______

Send this with your data → bot creates listing
"""
