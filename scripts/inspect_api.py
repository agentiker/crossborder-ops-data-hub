"""Inspect TikTok Shop API response structures for table design."""
import json
from platforms.tiktok_shop.client import TikTokShopClient


def inspect(name: str, data: dict):
    """Print response structure."""
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    print(json.dumps(data, indent=2, default=str)[:8000])


def main():
    client = TikTokShopClient()

    # 1. Get shops - to find shop_cipher
    try:
        result = client.get("/authorization/202309/shops")
        inspect("Shops", result)
    except Exception as e:
        print(f"Shops failed: {e}")

    # 2. Search orders
    try:
        result = client.post("/order/202309/orders/search", data={
            "page_size": 2,
        })
        inspect("Order Search", result)
    except Exception as e:
        print(f"Order search failed: {e}")

    # 3. Search products
    try:
        result = client.post("/product/202309/products/search", data={
            "page_size": 2,
            "status": "ACTIVATE",
        })
        inspect("Product Search", result)
    except Exception as e:
        print(f"Product search failed: {e}")


if __name__ == "__main__":
    main()
