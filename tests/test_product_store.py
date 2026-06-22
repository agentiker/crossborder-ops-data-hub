from models.base_models import Product
from platforms.tiktok_shop.normalize import to_domain_products
from services.product_store import prune_products_not_in, upsert_products


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


def _make_products(ids):
    return to_domain_products(
        [{"id": pid, "title": f"T-{pid}", "status": "ACTIVATE", "skus": []} for pid in ids]
    )


def test_prune_products_removes_stale_keeps_active(session):
    # 库里已有 P1/P2/P3；本次只返回 P1/P3（P2 已变草稿/下架）。
    upsert_products(
        session, _make_products(["P1", "P2", "P3"]),
        country="GB", shop_id="shop-1", account_id="acct-A",
    )
    session.commit()

    removed = prune_products_not_in(
        session, ["P1", "P3"],
        country="GB", shop_id="shop-1", account_id="acct-A",
    )
    session.commit()

    assert removed == 1
    remaining = {p.product_id for p in session.query(Product).all()}
    assert remaining == {"P1", "P3"}


def test_prune_products_empty_active_is_noop_guard(session):
    # 零数据护栏：本次返回空（API 异常）绝不清空整店。
    upsert_products(
        session, _make_products(["P1", "P2"]),
        country="GB", shop_id="shop-1", account_id="acct-A",
    )
    session.commit()

    assert prune_products_not_in(
        session, [], country="GB", shop_id="shop-1", account_id="acct-A",
    ) == 0
    session.commit()
    assert session.query(Product).count() == 2


def test_prune_products_only_touches_target_store(session):
    # 真实多租户：每个店只属一个租户、shop_id 各异（idempotency_key 不含 account_id，
    # 同 shop_id 跨 account 在 schema 层就撞唯一键、不可能共存）。
    upsert_products(session, _make_products(["P1", "P2"]),
                    country="GB", shop_id="shop-1", account_id="acct-A")
    upsert_products(session, _make_products(["P1", "P2"]),
                    country="GB", shop_id="shop-2", account_id="acct-A")
    upsert_products(session, _make_products(["P1", "P2"]),
                    country="GB", shop_id="shop-3", account_id="acct-B")
    session.commit()

    # 只对 (acct-A, shop-1) 清退，active 仅 P1 → 删掉它的 P2。
    removed = prune_products_not_in(
        session, ["P1"], country="GB", shop_id="shop-1", account_id="acct-A",
    )
    session.commit()

    assert removed == 1
    target = session.query(Product).filter_by(
        account_id="acct-A", shop_id="shop-1").all()
    assert {p.product_id for p in target} == {"P1"}
    # 同租户另一店、另一租户的店都原封不动（各 2 行）
    for acct, shop in [("acct-A", "shop-2"), ("acct-B", "shop-3")]:
        rows = session.query(Product).filter_by(account_id=acct, shop_id=shop).all()
        assert {p.product_id for p in rows} == {"P1", "P2"}
