"""list_product_costs 列表 + 商品图关联 + 账户隔离回归。

成本按 (account_id, platform, seller_sku) 存；商品图按 platform 分派：tiktok_shop 走
seller_sku→sku_variants→products.main_image_url（马帮无图）。缺变体/缺图的 SKU 仍应列出。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from models.base_models import Product, ProductCost, SkuVariant
from services.product_cost_store import list_product_costs


def _seed_cost(session, *, sku, cost, account="ecom-app", note=None, updated=None):
    session.add(
        ProductCost(
            account_id=account,
            platform="tiktok_shop",
            seller_sku=sku,
            unit_cost_rmb=Decimal(cost),
            note=note,
            updated_at=updated,
        )
    )


def test_list_joins_image_and_title(session):
    # 组合 SKU 有变体 + 主图；单件 SKU 有变体但商品无图；孤儿 SKU 无变体。
    _seed_cost(session, sku="809-KH-L", cost="15.78", note="马帮组合",
               updated=datetime(2026, 7, 10, 8, 0))
    _seed_cost(session, sku="XGN396-XL", cost="13.96", note="马帮统一成本价",
               updated=datetime(2026, 7, 12, 8, 0))
    _seed_cost(session, sku="ORPHAN-SKU", cost="9.90", updated=datetime(2026, 7, 11, 8, 0))
    # 变体：seller_sku → product_id
    session.add(SkuVariant(account_id="ecom-app", platform="tiktok_shop",
                           idempotency_key="v1", sku_id="s1", product_id="P1",
                           seller_sku="809-KH-L", product_name="809 连衣裙"))
    session.add(SkuVariant(account_id="ecom-app", platform="tiktok_shop",
                           idempotency_key="v2", sku_id="s2", product_id="P2",
                           seller_sku="XGN396-XL", product_name="396 卫衣"))
    # 商品：P1 有主图，P2 无图
    session.add(Product(account_id="ecom-app", platform="tiktok_shop",
                        idempotency_key="p1", product_id="P1", title="809 连衣裙",
                        main_image_url="https://cdn.example/p1.jpg"))
    session.add(Product(account_id="ecom-app", platform="tiktok_shop",
                        idempotency_key="p2", product_id="P2", title="396 卫衣",
                        main_image_url=None))
    session.commit()

    rows = list_product_costs(account_id="ecom-app", session=session)
    by = {r["seller_sku"]: r for r in rows}

    # 组合：图 + 款号都关联上
    assert by["809-KH-L"]["image_url"] == "https://cdn.example/p1.jpg"
    assert by["809-KH-L"]["product_title"] == "809 连衣裙"
    assert by["809-KH-L"]["unit_cost_rmb"] == 15.78  # Decimal → float
    assert by["809-KH-L"]["note"] == "马帮组合"
    # 单件：有款号但商品无图 → image_url None
    assert by["XGN396-XL"]["product_title"] == "396 卫衣"
    assert by["XGN396-XL"]["image_url"] is None
    # 孤儿：无变体 → 图/款号均 None，但仍列出
    assert by["ORPHAN-SKU"]["image_url"] is None
    assert by["ORPHAN-SKU"]["product_title"] is None
    assert by["ORPHAN-SKU"]["unit_cost_rmb"] == 9.90

    # 默认按 updated_at 倒序：XGN396(7/12) > ORPHAN(7/11) > 809(7/10)
    assert [r["seller_sku"] for r in rows] == ["XGN396-XL", "ORPHAN-SKU", "809-KH-L"]


def test_list_isolates_by_account(session):
    _seed_cost(session, sku="MINE-1", cost="10")
    _seed_cost(session, sku="THEIRS-1", cost="20", account="other-app")
    session.commit()

    skus = {r["seller_sku"] for r in list_product_costs(account_id="ecom-app", session=session)}
    assert skus == {"MINE-1"}


def test_list_no_images_for_other_platform(session):
    # shopee 等未接商品主数据的平台：不报错、图恒 None。
    session.add(
        ProductCost(account_id="ecom-app", platform="shopee",
                    seller_sku="SHP-1", unit_cost_rmb=Decimal("5"))
    )
    session.commit()

    rows = list_product_costs(account_id="ecom-app", platform="shopee", session=session)
    assert len(rows) == 1
    assert rows[0]["image_url"] is None
