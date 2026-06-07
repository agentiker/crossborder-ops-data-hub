from models.base_models import Product
from platforms.tiktok_shop.schemas import ProductItem, normalize_products
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


def test_normalize_products_extracts_min_price_and_sku_count():
    rows = normalize_products(SAMPLE)
    assert len(rows) == 2  # 缺 id 的被丢弃
    p1 = rows[0]
    assert p1["product_id"] == "P1"
    assert p1["min_price"] == 9.99
    assert p1["currency"] == "GBP"
    assert p1["sku_count"] == 2
    assert p1["sales_regions"] == ["GB"]
    assert rows[1]["min_price"] is None  # 无 SKU/价格


def test_upsert_products_idempotent_and_updates(session):
    items = [ProductItem.model_validate(r) for r in normalize_products(SAMPLE)]

    assert upsert_products(session, items, country="GB", shop_id="shop-1") == 2
    # 重跑同一批不应产生重复
    assert upsert_products(session, items, country="GB", shop_id="shop-1") == 2
    session.commit()
    assert session.query(Product).count() == 2

    # 更新：P2 上架、改价
    updated = ProductItem.model_validate(
        normalize_products([{
            "id": "P2", "title": "No price", "status": "ACTIVATE",
            "skus": [{"id": "S9", "price": {"sale_price": "5.00", "currency": "GBP"}}],
        }])[0]
    )
    upsert_products(session, [updated], country="GB", shop_id="shop-1")
    session.commit()

    p2 = session.query(Product).filter_by(product_id="P2").one()
    assert p2.status == "ACTIVATE"
    assert float(p2.min_price) == 5.00
    assert p2.sku_count == 1
    assert session.query(Product).count() == 2  # 仍是 2 行


def test_upsert_products_keeps_shops_distinct(session):
    items = [ProductItem.model_validate(r) for r in normalize_products(SAMPLE)]
    upsert_products(session, items, country="GB", shop_id="shop-1")
    upsert_products(session, items, country="GB", shop_id="shop-2")
    session.commit()
    # 同 product_id 不同店铺各自一行
    assert session.query(Product).count() == 4
