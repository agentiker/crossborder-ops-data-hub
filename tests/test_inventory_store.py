from core.domain import DomainInventoryItem
from models.base_models import Inventory
from services.inventory_store import upsert_inventory_items


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
