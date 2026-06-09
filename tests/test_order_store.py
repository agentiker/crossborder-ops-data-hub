from datetime import date, datetime, timezone

from ai_tools import operations_read  # noqa: F401  (ensures models imported)
from models.base_models import OrderHeader, OrderLineItem
from platforms.tiktok_shop.schemas import OrderSchema
from services import order_metrics
from services.order_store import upsert_orders


def _unix(y, m, d, h=12, mi=0):
    return int(datetime(y, m, d, h, mi, tzinfo=timezone.utc).timestamp())


def _order(order_id, *, paid, total, lines, status="COMPLETED"):
    return OrderSchema.model_validate(
        {
            "id": order_id,
            "status": status,
            "create_time": paid,
            "paid_time": paid,
            "update_time": paid,
            "payment": {"currency": "IDR", "total_amount": total},
            "line_items": lines,
        }
    )


def test_upsert_orders_idempotent(session):
    order = _order(
        "order-1",
        paid=_unix(2026, 6, 1),
        total="100000",
        lines=[
            {"id": "line-1", "sku_id": "sku-A", "product_name": "P-A", "sale_price": "60000"},
            {"id": "line-2", "sku_id": "sku-B", "product_name": "P-B", "sale_price": "40000"},
        ],
    )

    assert upsert_orders(session, [order], country="ID", shop_id="shop-1") == (1, 2)
    # 重跑同一窗口
    assert upsert_orders(session, [order], country="ID", shop_id="shop-1") == (1, 2)
    session.commit()

    assert session.query(OrderHeader).count() == 1
    assert session.query(OrderLineItem).count() == 2
    header = session.query(OrderHeader).one()
    assert float(header.total_amount) == 100000.0
    assert header.currency == "IDR"


def test_upsert_orders_updates_existing_status(session):
    base = dict(paid=_unix(2026, 6, 1), total="50000",
               lines=[{"id": "line-1", "sku_id": "sku-A", "sale_price": "50000"}])
    upsert_orders(session, [_order("order-1", status="AWAITING_SHIPMENT", **base)],
                  country="ID", shop_id="shop-1")
    upsert_orders(session, [_order("order-1", status="COMPLETED", **base)],
                  country="ID", shop_id="shop-1")
    session.commit()

    assert session.query(OrderHeader).count() == 1
    assert session.query(OrderHeader).one().order_status == "COMPLETED"


def test_gmv_summary_paid_window(session, monkeypatch):
    orders = [
        _order("paid-in", paid=_unix(2026, 6, 3), total="100000",
               lines=[{"id": "l1", "sku_id": "sku-A", "sale_price": "100000"}]),
        _order("paid-out", paid=_unix(2026, 5, 1), total="999999",
               lines=[{"id": "l2", "sku_id": "sku-A", "sale_price": "999999"}]),
    ]
    # 一笔未付款订单（paid_time 为空）应被排除
    unpaid = OrderSchema.model_validate({
        "id": "unpaid", "status": "UNPAID", "create_time": _unix(2026, 6, 3),
        "payment": {"currency": "IDR", "total_amount": "777777"},
        "line_items": [{"id": "l3", "sku_id": "sku-A", "sale_price": "777777"}],
    })
    upsert_orders(session, orders + [unpaid], country="ID", shop_id="shop-1")
    session.commit()
    monkeypatch.setattr(order_metrics, "SessionLocal", lambda: session)

    summary = order_metrics.get_gmv_summary(
        start_date=date(2026, 6, 1), end_date=date(2026, 6, 5),
        country="ID", shop_id="shop-1",
    )
    assert summary["gmv"] == 100000.0
    assert summary["order_count"] == 1
    assert summary["units_sold"] == 1
    assert summary["avg_order_value"] == 100000.0


def test_top_skus_ranked_by_units(session, monkeypatch):
    order = _order(
        "order-1", paid=_unix(2026, 6, 3), total="300000",
        lines=[
            {"id": "l1", "sku_id": "sku-A", "product_name": "Hot", "sale_price": "50000"},
            {"id": "l2", "sku_id": "sku-A", "product_name": "Hot", "sale_price": "50000"},
            {"id": "l3", "sku_id": "sku-B", "product_name": "Cold", "sale_price": "200000"},
        ],
    )
    upsert_orders(session, [order], country="ID", shop_id="shop-1")
    session.commit()
    monkeypatch.setattr(order_metrics, "SessionLocal", lambda: session)

    top = order_metrics.get_top_skus(
        start_date=date(2026, 6, 1), end_date=date(2026, 6, 5),
        country="ID", shop_id="shop-1",
    )
    assert [r["sku_id"] for r in top] == ["sku-A", "sku-B"]
    assert top[0]["units_sold"] == 2
    assert top[0]["gmv"] == 100000.0


def test_gmv_trend_fills_empty_days(session, monkeypatch):
    orders = [
        _order("d3", paid=_unix(2026, 6, 3), total="100000",
               lines=[{"id": "l1", "sku_id": "sku-A", "sale_price": "100000"}]),
        _order("d5a", paid=_unix(2026, 6, 5), total="30000",
               lines=[{"id": "l2", "sku_id": "sku-A", "sale_price": "30000"}]),
        _order("d5b", paid=_unix(2026, 6, 5), total="20000",
               lines=[{"id": "l3", "sku_id": "sku-B", "sale_price": "20000"}]),
    ]
    upsert_orders(session, orders, country="ID", shop_id="shop-1")
    session.commit()
    monkeypatch.setattr(order_metrics, "SessionLocal", lambda: session)

    points = order_metrics.get_gmv_trend(
        start_date=date(2026, 6, 3), end_date=date(2026, 6, 6),
        country="ID", shop_id="shop-1",
    )
    # 连续 4 天，6/4 与 6/6 无单补 0
    assert [p["date"] for p in points] == [
        "2026-06-03", "2026-06-04", "2026-06-05", "2026-06-06",
    ]
    by_date = {p["date"]: p for p in points}
    assert by_date["2026-06-03"]["gmv"] == 100000.0
    assert by_date["2026-06-03"]["order_count"] == 1
    assert by_date["2026-06-04"] == {
        "date": "2026-06-04", "gmv": 0.0, "order_count": 0, "units_sold": 0,
    }
    assert by_date["2026-06-05"]["gmv"] == 50000.0
    assert by_date["2026-06-05"]["order_count"] == 2
    assert by_date["2026-06-05"]["units_sold"] == 2


def test_business_day_timezone_boundary(session, monkeypatch):
    """跨日边界：UTC 23:57 的单（印尼 UTC+7 已是次日凌晨）应归到印尼次日，而非 UTC 当日。

    复刻线上那笔 paid_time=2026-06-08 23:57 UTC（印尼 6/9 06:57）被错算到 6/8 的 bug。
    """
    orders = [
        # UTC 6/8 14:00 → 印尼 6/8 21:00 → 印尼日 6/8
        _order("early", paid=_unix(2026, 6, 8, 14, 0), total="46894",
               lines=[{"id": "le", "sku_id": "sku-A", "sale_price": "46894"}]),
        # UTC 6/8 23:57 → 印尼 6/9 06:57 → 印尼日 6/9（关键）
        _order("late", paid=_unix(2026, 6, 8, 23, 57), total="20028",
               lines=[{"id": "ll", "sku_id": "sku-B", "sale_price": "20028"}]),
    ]
    upsert_orders(session, orders, country="ID", shop_id="shop-1")
    session.commit()
    monkeypatch.setattr(order_metrics, "SessionLocal", lambda: session)

    points = order_metrics.get_gmv_trend(
        start_date=date(2026, 6, 8), end_date=date(2026, 6, 9),
        country="ID", shop_id="shop-1",
    )
    by_date = {p["date"]: p for p in points}
    assert by_date["2026-06-08"]["gmv"] == 46894.0
    assert by_date["2026-06-08"]["order_count"] == 1
    assert by_date["2026-06-08"]["units_sold"] == 1
    # 关键断言：23:57 UTC 的单归到印尼 6/9，不再算进 6/8
    assert by_date["2026-06-09"]["gmv"] == 20028.0
    assert by_date["2026-06-09"]["order_count"] == 1
    assert by_date["2026-06-09"]["units_sold"] == 1

    # summary 查印尼 6/9 当天，应只含 late 那笔（窗口边界也按印尼时区）
    summary = order_metrics.get_gmv_summary(
        start_date=date(2026, 6, 9), end_date=date(2026, 6, 9),
        country="ID", shop_id="shop-1",
    )
    assert summary["gmv"] == 20028.0
    assert summary["order_count"] == 1
    assert summary["units_sold"] == 1

    # 查印尼 6/8 当天，应只含 early 那笔
    summary_8 = order_metrics.get_gmv_summary(
        start_date=date(2026, 6, 8), end_date=date(2026, 6, 8),
        country="ID", shop_id="shop-1",
    )
    assert summary_8["gmv"] == 46894.0
    assert summary_8["order_count"] == 1
