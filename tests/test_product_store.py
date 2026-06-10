from models.base_models import Product
from platforms.tiktok_shop.normalize import to_domain_products
from services.product_store import upsert_products


SAMPLE = [
    {
        "id": "P1", "title": "T-Shirt", "status": "ACTIVATE",
        "sales_regions": ["GB"], "create_time": 1700000000, "update_time": 1710000000,
        "skus": [
            {"id": "S1", "price": {"sale_price": "12.50", "currency": "GBP"}},
            {"id": "S2", "price": {"sale_price": "9.99", "currency": "GBP"}},
        ],
    },
    {"id": "P2", "title": "No price", "status": "DRAFT", "skus": []},
    {"title": "Missing id, dropped", "skus": []},
]
# 注：dict→DTO 的清洗正确性（min_price 取最低、丢无 id、sku_count）见 test_tiktok_normalize.py。


def test_upsert_products_idempotent_and_updates(session):
    items = to_domain_products(SAMPLE)

    assert upsert_products(session, items, country="GB", shop_id="shop-1") == 2
    # 重跑同一批不应产生重复
    assert upsert_products(session, items, country="GB", shop_id="shop-1") == 2
    session.commit()
    assert session.query(Product).count() == 2

    # 更新：P2 上架、改价
    updated = to_domain_products([{
        "id": "P2", "title": "No price", "status": "ACTIVATE",
        "skus": [{"id": "S9", "price": {"sale_price": "5.00", "currency": "GBP"}}],
    }])
    upsert_products(session, updated, country="GB", shop_id="shop-1")
    session.commit()

    p2 = session.query(Product).filter_by(product_id="P2").one()
    assert p2.status == "ACTIVATE"
    assert float(p2.min_price) == 5.00
    assert p2.sku_count == 1
    assert session.query(Product).count() == 2  # 仍是 2 行


def test_upsert_products_keeps_shops_distinct(session):
    items = to_domain_products(SAMPLE)
    upsert_products(session, items, country="GB", shop_id="shop-1")
    upsert_products(session, items, country="GB", shop_id="shop-2")
    session.commit()
    # 同 product_id 不同店铺各自一行
    assert session.query(Product).count() == 4
