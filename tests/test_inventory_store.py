from core.domain import DomainInventoryItem
from models.base_models import Inventory
from services.inventory_store import prune_inventory_not_in, upsert_inventory_items


def _inv(sku_id, product_id, *, warehouse_id="wh-1", stock=10):
    return DomainInventoryItem(
        sku_id=sku_id,
        product_id=product_id,
        product_name=f"P-{product_id}",
        sku_name=f"S-{sku_id}",
        available_stock=stock,
        reserved_stock=0,
        warehouse_id=warehouse_id,
    )


def test_upsert_inventory_items_updates_existing_row_without_duplicate(session):
    original = DomainInventoryItem(
        sku_id="sku-1",
        product_id="product-1",
        product_name="Old Product",
        sku_name="Old SKU",
        available_stock=10,
        reserved_stock=1,
        warehouse_id="warehouse-1",
    )
    updated = DomainInventoryItem(
        sku_id="sku-1",
        product_id="product-1",
        product_name="New Product",
        sku_name="New SKU",
        available_stock=3,
        reserved_stock=2,
        warehouse_id="warehouse-1",
    )

    assert upsert_inventory_items(session, [original]) == 1
    assert upsert_inventory_items(session, [updated]) == 1
    session.commit()

    rows = session.query(Inventory).all()
    assert len(rows) == 1
    assert rows[0].available_stock == 3
    assert rows[0].reserved_stock == 2
    assert rows[0].product_name == "New Product"


def test_upsert_inventory_items_keeps_warehouses_distinct(session):
    items = [
        DomainInventoryItem(
            sku_id="sku-1",
            product_id="product-1",
            product_name="Product",
            sku_name="SKU",
            available_stock=10,
            reserved_stock=1,
            warehouse_id="warehouse-1",
        ),
        DomainInventoryItem(
            sku_id="sku-1",
            product_id="product-1",
            product_name="Product",
            sku_name="SKU",
            available_stock=20,
            reserved_stock=0,
            warehouse_id="warehouse-2",
        ),
    ]

    upsert_inventory_items(session, items)
    session.commit()

    assert session.query(Inventory).count() == 2


def test_prune_inventory_removes_stale_sku_and_dropped_variant(session):
    # 库里：商品 P1 两个变体(s1,s2) + 草稿商品 P2 的 s3。
    upsert_inventory_items(
        session,
        [_inv("s1", "P1"), _inv("s2", "P1"), _inv("s3", "P2")],
        shop_id="shop-1", account_id="acct-A",
    )
    session.commit()

    # 本次只返回 P1 的 s1（P2 整个下架；P1 的 s2 变体被删）。
    removed = prune_inventory_not_in(
        session, [_inv("s1", "P1")], shop_id="shop-1", account_id="acct-A",
    )
    session.commit()

    assert removed == 2  # s2 + s3
    remaining = {r.sku_id for r in session.query(Inventory).all()}
    assert remaining == {"s1"}


def test_prune_inventory_empty_items_is_noop_guard(session):
    upsert_inventory_items(
        session, [_inv("s1", "P1")], shop_id="shop-1", account_id="acct-A",
    )
    session.commit()

    assert prune_inventory_not_in(
        session, [], shop_id="shop-1", account_id="acct-A",
    ) == 0
    session.commit()
    assert session.query(Inventory).count() == 1


def test_prune_inventory_only_touches_target_store(session):
    # 真实多租户：店各异（同 shop_id 跨 account 会撞 idempotency_key、不可能共存）。
    for acct, shop in [("acct-A", "shop-1"), ("acct-A", "shop-2"), ("acct-B", "shop-3")]:
        upsert_inventory_items(
            session, [_inv("s1", "P1"), _inv("s2", "P1")],
            shop_id=shop, account_id=acct,
        )
    session.commit()

    # 只清退 (acct-A, shop-1)，本次仅 s1 → 删该店 s2。
    removed = prune_inventory_not_in(
        session, [_inv("s1", "P1")], shop_id="shop-1", account_id="acct-A",
    )
    session.commit()

    assert removed == 1
    target = session.query(Inventory).filter_by(
        account_id="acct-A", shop_id="shop-1").all()
    assert {r.sku_id for r in target} == {"s1"}
    for acct, shop in [("acct-A", "shop-2"), ("acct-B", "shop-3")]:
        rows = session.query(Inventory).filter_by(account_id=acct, shop_id=shop).all()
        assert {r.sku_id for r in rows} == {"s1", "s2"}
