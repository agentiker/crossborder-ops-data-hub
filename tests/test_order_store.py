from datetime import date, datetime
from decimal import Decimal

from ai_tools import operations_read  # noqa: F401  (ensures models imported)
from core.domain import DomainOrder, DomainOrderLineItem
from models.base_models import OrderHeader, OrderLineItem
from services import order_metrics
from services.order_store import upsert_orders


def _dt(y, m, d, h=12, mi=0):
    """naive UTC datetime（与 normalize 的时间口径一致）。"""
    return datetime(y, m, d, h, mi)


def _order(order_id, *, paid, total, lines, status="COMPLETED"):
    """构造平台中立 DomainOrder（store 现在只认它）。line dict 仍用旧 key 方便书写。"""
    return DomainOrder(
        order_id=order_id,
        order_status=status,
        currency="IDR",
        total_amount=Decimal(total),
        create_time=paid,
        paid_time=paid,
        update_time=paid,
        line_items=tuple(
            DomainOrderLineItem(
                line_item_id=ln["id"],
                sku_id=ln.get("sku_id"),
                product_name=ln.get("product_name"),
                sale_price=Decimal(ln.get("sale_price", "0")),
                currency="IDR",
            )
            for ln in lines
        ),
    )


def test_upsert_orders_idempotent(session):
    order = _order(
        "order-1",
        paid=_dt(2026, 6, 1),
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
    base = dict(paid=_dt(2026, 6, 1), total="50000",
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
        _order("paid-in", paid=_dt(2026, 6, 3), total="100000",
               lines=[{"id": "l1", "sku_id": "sku-A", "sale_price": "100000"}]),
        _order("paid-out", paid=_dt(2026, 5, 1), total="999999",
               lines=[{"id": "l2", "sku_id": "sku-A", "sale_price": "999999"}]),
    ]
    # 一笔未付款订单（paid_time 为空）应被排除
    unpaid = DomainOrder(
        order_id="unpaid", order_status="UNPAID", currency="IDR",
        total_amount=Decimal("777777"), create_time=_dt(2026, 6, 3), paid_time=None,
        line_items=(DomainOrderLineItem(line_item_id="l3", sku_id="sku-A",
                                        sale_price=Decimal("777777"), currency="IDR"),),
    )
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
        "order-1", paid=_dt(2026, 6, 3), total="300000",
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
        _order("d3", paid=_dt(2026, 6, 3), total="100000",
               lines=[{"id": "l1", "sku_id": "sku-A", "sale_price": "100000"}]),
        _order("d5a", paid=_dt(2026, 6, 5), total="30000",
               lines=[{"id": "l2", "sku_id": "sku-A", "sale_price": "30000"}]),
        _order("d5b", paid=_dt(2026, 6, 5), total="20000",
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
        "date": "2026-06-04", "label": None, "gmv": 0.0, "order_count": 0, "units_sold": 0,
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
        _order("early", paid=_dt(2026, 6, 8, 14, 0), total="46894",
               lines=[{"id": "le", "sku_id": "sku-A", "sale_price": "46894"}]),
        # UTC 6/8 23:57 → 印尼 6/9 06:57 → 印尼日 6/9（关键）
        _order("late", paid=_dt(2026, 6, 8, 23, 57), total="20028",
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


# ── 逐小时趋势（单天 granularity="hour"）──────────────────────────────────────


def test_gmv_trend_hourly_past_day_full_24(session, monkeypatch):
    """过去某天逐小时：返回 24 个点（00:00–23:00），按印尼当地小时归桶，无单的小时补 0。"""
    orders = [
        # UTC 6/8 02:00 → 印尼 6/8 09:00 桶
        _order("h9", paid=_dt(2026, 6, 8, 2, 0), total="100000",
               lines=[{"id": "l1", "sku_id": "sku-A", "sale_price": "100000"}]),
        # UTC 6/8 06:30 → 印尼 6/8 13:30 → 13:00 桶（两笔同小时）
        _order("h13a", paid=_dt(2026, 6, 8, 6, 30), total="30000",
               lines=[{"id": "l2", "sku_id": "sku-A", "sale_price": "30000"}]),
        _order("h13b", paid=_dt(2026, 6, 8, 6, 59), total="20000",
               lines=[{"id": "l3", "sku_id": "sku-B", "sale_price": "10000"},
                      {"id": "l4", "sku_id": "sku-C", "sale_price": "10000"}]),
    ]
    upsert_orders(session, orders, country="ID", shop_id="shop-1")
    session.commit()
    monkeypatch.setattr(order_metrics, "SessionLocal", lambda: session)
    # 钉死「今天」在 6/9，使 6/8 走「过去某天补满 24 格」分支。
    monkeypatch.setattr(order_metrics, "business_today", lambda: date(2026, 6, 9))

    points = order_metrics.get_gmv_trend(
        start_date=date(2026, 6, 8), end_date=date(2026, 6, 8),
        country="ID", shop_id="shop-1", granularity="hour",
    )
    assert len(points) == 24
    assert [p["label"] for p in points] == [f"{h:02d}:00" for h in range(24)]
    # date 全部等于当天 ISO（逐小时点不污染 date 字段语义）
    assert {p["date"] for p in points} == {"2026-06-08"}
    by_h = {p["label"]: p for p in points}
    assert by_h["09:00"]["gmv"] == 100000.0
    assert by_h["09:00"]["order_count"] == 1
    assert by_h["09:00"]["units_sold"] == 1
    # 13:00 桶聚两笔：GMV 30000+20000、2 单、3 个 line_item
    assert by_h["13:00"]["gmv"] == 50000.0
    assert by_h["13:00"]["order_count"] == 2
    assert by_h["13:00"]["units_sold"] == 3
    # 无单的小时补 0
    assert by_h["00:00"] == {
        "date": "2026-06-08", "label": "00:00", "gmv": 0.0, "order_count": 0, "units_sold": 0,
    }


def test_gmv_trend_hourly_today_truncated_to_current_hour(session, monkeypatch):
    """选「今天」逐小时：只画到当前印尼小时，不画未来空格。"""
    orders = [
        # UTC 6/9 02:00 → 印尼 6/9 09:00 桶（已过去，应出现）
        _order("now9", paid=_dt(2026, 6, 9, 2, 0), total="80000",
               lines=[{"id": "l1", "sku_id": "sku-A", "sale_price": "80000"}]),
    ]
    upsert_orders(session, orders, country="ID", shop_id="shop-1")
    session.commit()
    monkeypatch.setattr(order_metrics, "SessionLocal", lambda: session)
    monkeypatch.setattr(order_metrics, "business_today", lambda: date(2026, 6, 9))
    # 当前印尼此刻 = 6/9 13:42 → 截断到 13:00（画 00:00–13:00 共 14 格）。
    monkeypatch.setattr(
        order_metrics, "business_hour_now", lambda: datetime(2026, 6, 9, 13, 0, 0)
    )

    points = order_metrics.get_gmv_trend(
        start_date=date(2026, 6, 9), end_date=date(2026, 6, 9),
        country="ID", shop_id="shop-1", granularity="hour",
    )
    assert len(points) == 14  # 00:00 … 13:00
    assert points[-1]["label"] == "13:00"
    by_h = {p["label"]: p for p in points}
    assert by_h["09:00"]["gmv"] == 80000.0
    # 未来小时（14:00+）不出现
    assert "14:00" not in by_h


def test_gmv_trend_hourly_timezone_boundary(session, monkeypatch):
    """逐小时也无 UTC/CST 漂移：UTC 6/8 23:57 的单（印尼 6/9 06:57）归到 6/9 06:00 桶。"""
    orders = [
        _order("late", paid=_dt(2026, 6, 8, 23, 57), total="20028",
               lines=[{"id": "ll", "sku_id": "sku-B", "sale_price": "20028"}]),
    ]
    upsert_orders(session, orders, country="ID", shop_id="shop-1")
    session.commit()
    monkeypatch.setattr(order_metrics, "SessionLocal", lambda: session)
    monkeypatch.setattr(order_metrics, "business_today", lambda: date(2026, 6, 10))

    points = order_metrics.get_gmv_trend(
        start_date=date(2026, 6, 9), end_date=date(2026, 6, 9),
        country="ID", shop_id="shop-1", granularity="hour",
    )
    by_h = {p["label"]: p for p in points}
    assert by_h["06:00"]["gmv"] == 20028.0
    assert by_h["06:00"]["order_count"] == 1
    # 印尼 6/9 其它小时无单
    assert by_h["07:00"]["gmv"] == 0.0


def test_gmv_trend_day_default_unchanged(session, monkeypatch):
    """回归：不传 granularity 仍逐日，结构含 label=None、数值与原口径一致。"""
    orders = [
        _order("d3", paid=_dt(2026, 6, 3), total="100000",
               lines=[{"id": "l1", "sku_id": "sku-A", "sale_price": "100000"}]),
    ]
    upsert_orders(session, orders, country="ID", shop_id="shop-1")
    session.commit()
    monkeypatch.setattr(order_metrics, "SessionLocal", lambda: session)

    points = order_metrics.get_gmv_trend(
        start_date=date(2026, 6, 3), end_date=date(2026, 6, 3),
        country="ID", shop_id="shop-1",
    )
    assert points == [
        {"date": "2026-06-03", "label": None, "gmv": 100000.0, "order_count": 1, "units_sold": 1},
    ]


def test_display_units_exclude_cancelled_unpaid_gmv_and_orders_keep(session, monkeypatch):
    """展示口径（display=True）：销量（件）排除取消/未付款，GMV/订单数仍含取消（2026-07-02）。

    对齐后台——GMV/订单数含取消（后台 GMV/订单管理），销量对齐 Analytics 的 Items sold（已付款口径）。
    """
    # 正常已付款单：2 件（多 line_item 逐件展开）
    paid = _order("ok", paid=_dt(2026, 6, 3), total="100000", status="COMPLETED",
                  lines=[{"id": "l1", "sku_id": "sku-A", "sale_price": "60000"},
                         {"id": "l2", "sku_id": "sku-A", "sale_price": "40000"}])
    # 取消单：1 件（有 create_time、也有 paid_time——买家付了又取消）
    cancelled = _order("cxl", paid=_dt(2026, 6, 3), total="50000", status="CANCELLED",
                       lines=[{"id": "l3", "sku_id": "sku-B", "sale_price": "50000"}])
    # 未付款单：1 件（paid_time 为空）
    unpaid = DomainOrder(
        order_id="unp", order_status="UNPAID", currency="IDR",
        total_amount=Decimal("30000"), sub_total=Decimal("30000"),
        create_time=_dt(2026, 6, 3), paid_time=None,
        line_items=(DomainOrderLineItem(line_item_id="l4", sku_id="sku-C",
                                        sale_price=Decimal("30000"), currency="IDR"),),
    )
    upsert_orders(session, [paid, cancelled, unpaid], country="ID", shop_id="shop-1")
    session.commit()
    monkeypatch.setattr(order_metrics, "SessionLocal", lambda: session)

    summary = order_metrics.get_gmv_summary(
        start_date=date(2026, 6, 3), end_date=date(2026, 6, 3),
        country="ID", shop_id="shop-1", display=True,
    )
    # GMV/订单数：3 单全含（含取消 + 未付款）
    assert summary["order_count"] == 3
    # 销量（件）：只算已付款正常单的 2 件（取消 1 + 未付款 1 被排除）
    assert summary["units_sold"] == 2
    # 已取消单数（灰字标注用）：cxl 一单
    assert summary["cancelled_count"] == 1
    # 未付款单数（灰字标注用）：unp 一单
    assert summary["unpaid_count"] == 1

    # 趋势 units 桶同口径：当日 order_count=3、units_sold=2
    points = order_metrics.get_gmv_trend(
        start_date=date(2026, 6, 3), end_date=date(2026, 6, 3),
        country="ID", shop_id="shop-1", display=True,
    )
    day = next(p for p in points if p["date"] == "2026-06-03")
    assert day["order_count"] == 3 and day["units_sold"] == 2


def test_nondisplay_units_unchanged_by_status_filter(session, monkeypatch):
    """回归护栏：非展示口径（付款口径，display=False）不受新增状态过滤影响。

    付款口径本就靠 paid_time 非空隐含排除未付款；此处确认取消单（有 paid_time）在付款口径下
    仍按原行为计入 units（新加的 _units_status_filter 只在 display=True 生效，不误伤付款口径）。"""
    cancelled = _order("cxl2", paid=_dt(2026, 6, 3), total="50000", status="CANCELLED",
                       lines=[{"id": "m1", "sku_id": "sku-B", "sale_price": "50000"}])
    upsert_orders(session, [cancelled], country="ID", shop_id="shop-1")
    session.commit()
    monkeypatch.setattr(order_metrics, "SessionLocal", lambda: session)

    # 付款口径（默认 display=False, by_create=False）：取消单有 paid_time，仍计入
    summary = order_metrics.get_gmv_summary(
        start_date=date(2026, 6, 3), end_date=date(2026, 6, 3),
        country="ID", shop_id="shop-1",
    )
    assert summary["order_count"] == 1 and summary["units_sold"] == 1
    # 非展示口径不算取消单数（恒 0，前端据此不显灰字）
    assert summary["cancelled_count"] == 0
