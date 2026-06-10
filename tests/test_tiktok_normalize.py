"""TikTok 原始 dict → 平台中立领域模型 的转换正确性（normalize 边界安全网）。

这是把"平台数据怪癖"（金额字符串、Unix 秒、库存嵌套、currency fallback）从 store/flows
收敛到 platforms/tiktok_shop/normalize.py 后的回归锁。store 现在只认 core.domain DTO，
所以这些转换若退化，必须在这里先红。
"""
from datetime import datetime, timezone
from decimal import Decimal

from platforms.tiktok_shop import normalize as nz


def _epoch(y, m, d, h=12, mi=0, s=0):
    """naive 视角的 (y,m,d,h,mi,s) 当作 UTC → Unix 秒。"""
    return int(datetime(y, m, d, h, mi, s, tzinfo=timezone.utc).timestamp())


# ── 金额 str → Decimal（含容错）──────────────────────────────────────────────

def test_to_decimal_parses_and_tolerates_garbage():
    assert nz._to_decimal("100000") == Decimal("100000")
    assert nz._to_decimal("9.99") == Decimal("9.99")
    assert nz._to_decimal(None) == Decimal("0")
    assert nz._to_decimal("") == Decimal("0")
    assert nz._to_decimal("abc") == Decimal("0")
    # 精度：必须从字符串构造，不能经 float
    assert nz._to_decimal("0.1") == Decimal("0.1")


# ── Unix 秒 → naive UTC datetime（含 0/None → None）────────────────────────────

def test_epoch_to_dt_naive_utc_and_sentinels():
    assert nz._epoch_to_dt(0) is None
    assert nz._epoch_to_dt(None) is None
    assert nz._epoch_to_dt("") is None
    out = nz._epoch_to_dt(_epoch(2024, 6, 1, 12, 17, 0))
    assert out is not None and out.tzinfo is None  # naive
    assert out == datetime(2024, 6, 1, 12, 17, 0)


def test_epoch_to_dt_crossday_no_shift():
    """UTC 23:57 的单：转换只是 unix→naive UTC，不做任何时区偏移（归日交给 order_metrics）。"""
    assert nz._epoch_to_dt(_epoch(2026, 6, 8, 23, 57)) == datetime(2026, 6, 8, 23, 57)


# ── 订单：拆 payment、str→Decimal、currency fallback ─────────────────────────

PAID_EPOCH = _epoch(2024, 6, 1, 12, 17, 0)


def _order_page(line_items, payment=None):
    return [{"orders": [{
        "id": "o1", "status": "PAID",
        "create_time": PAID_EPOCH, "paid_time": PAID_EPOCH, "update_time": PAID_EPOCH,
        "payment": payment if payment is not None else {"currency": "IDR", "total_amount": "201776"},
        "line_items": line_items,
    }]}]


def test_to_domain_orders_amounts_and_currency_fallback():
    pages = _order_page([
        {"id": "l1", "sku_id": "s1", "sale_price": "99999"},                     # 无 currency → fallback IDR
        {"id": "l2", "sku_id": "s1", "sale_price": "99999", "currency": "USD"},   # 有 currency → 不覆盖
    ])
    order = nz.to_domain_orders(pages)[0]
    assert order.total_amount == Decimal("201776")
    assert isinstance(order.total_amount, Decimal)
    assert order.currency == "IDR"
    assert order.line_items[0].sale_price == Decimal("99999")
    assert order.line_items[0].currency == "IDR"   # fallback
    assert order.line_items[1].currency == "USD"   # 保留 line 自带
    assert order.paid_time == datetime(2024, 6, 1, 12, 17, 0)  # naive UTC


def test_to_domain_orders_skips_bad_record_without_aborting(capsys):
    pages = [{"orders": [
        {"bad": "no id"},                       # 缺必填 id → 校验失败跳过
        {"id": "ok", "line_items": []},         # 正常
    ]}]
    orders = nz.to_domain_orders(pages)
    assert [o.order_id for o in orders] == ["ok"]
    assert "订单校验失败" in capsys.readouterr().out


# ── 库存：嵌套展平两分支 + product_titles 回填 ────────────────────────────────

def test_to_domain_inventory_flattens_warehouses():
    inventory = [{
        "product_id": "P1",
        "skus": [{
            "id": "SK1", "seller_sku": "SELLER-1",
            "warehouse_inventory": [
                {"warehouse_id": "W1", "available_quantity": 6, "committed_quantity": 1},
                {"warehouse_id": "W2", "available_quantity": 20, "committed_quantity": 0},
            ],
        }],
    }]
    items = nz.to_domain_inventory(inventory, {"P1": "Spring Bed"})
    assert len(items) == 2  # 每个 SKU×仓库一行
    assert {i.warehouse_id for i in items} == {"W1", "W2"}
    assert items[0].product_name == "Spring Bed"  # title 回填
    assert items[0].available_stock == 6 and items[0].reserved_stock == 1


def test_to_domain_inventory_fallback_when_no_warehouse():
    inventory = [{
        "product_id": "P2",
        "skus": [{
            "id": "SK2", "seller_sku": "SELLER-2",
            "total_available_quantity": 15, "total_committed_quantity": 3,
        }],
    }]
    items = nz.to_domain_inventory(inventory)
    assert len(items) == 1
    assert items[0].warehouse_id is None          # 跨仓汇总
    assert items[0].available_stock == 15 and items[0].reserved_stock == 3


# ── 商品：取最低价、sku_count、丢无 id ────────────────────────────────────────

SAMPLE_PRODUCTS = [
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


def test_to_domain_products_min_price_and_drop_no_id():
    items = nz.to_domain_products(SAMPLE_PRODUCTS)
    assert len(items) == 2  # 缺 id 的被丢弃
    p1 = items[0]
    assert p1.product_id == "P1"
    assert p1.min_price == Decimal("9.99")  # 取最低（9.99 < 12.50），Decimal 精度
    assert isinstance(p1.min_price, Decimal)
    assert p1.currency == "GBP"
    assert p1.sku_count == 2
    assert p1.sales_regions == ["GB"]
    assert items[1].min_price is None  # 无 SKU/价格
    assert items[1].sku_count == 0
