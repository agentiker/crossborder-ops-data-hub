from datetime import datetime
from decimal import Decimal

import pytest

from models.base_models import PendingFulfillment
from services import fulfillment_metrics

# 固定"现在"= 印尼时间无关的 naive UTC 基准
NOW = datetime(2026, 6, 12, 12, 0)


def _add(session, order_id, *, sla, shop_id="shop-1", country="ID", item_count=1,
         product="P", synced_at=None):
    session.add(
        PendingFulfillment(
            platform="tiktok_shop",
            country=country,
            shop_id=shop_id,
            idempotency_key=f"k-{shop_id}-{order_id}",
            order_id=order_id,
            order_status="AWAITING_SHIPMENT",
            tts_sla_time=sla,
            total_amount=Decimal("100000"),
            currency="IDR",
            item_count=item_count,
            first_product_name=product,
            create_time=datetime(2026, 6, 10, 12, 0),
            synced_at=synced_at,
        )
    )


@pytest.fixture()
def patched(session, monkeypatch):
    monkeypatch.setattr(fulfillment_metrics, "SessionLocal", lambda: session)
    monkeypatch.setattr(fulfillment_metrics, "_now", lambda: NOW)
    return session


def _call(**kw):
    return fulfillment_metrics.get_pending_fulfillments(country="ID", **kw)


def test_buckets_classification(patched):
    from datetime import timedelta

    _add(patched, "overdue", sla=NOW - timedelta(hours=1))
    _add(patched, "critical", sla=NOW + timedelta(hours=5))
    _add(patched, "normal", sla=NOW + timedelta(hours=48))
    _add(patched, "unknown", sla=None)
    patched.commit()

    res = _call(warning_hours=24)
    assert res["buckets"] == {"overdue": 1, "critical": 1, "normal": 1, "unknown": 1, "total": 4}


def test_bucket_boundaries(patched):
    from datetime import timedelta

    _add(patched, "at_now", sla=NOW)  # 恰好 now → 未超时、距截止 0h < 24h → critical
    _add(patched, "at_warn", sla=NOW + timedelta(hours=24))  # 恰好 now+24h → normal
    _add(patched, "just_over", sla=NOW - timedelta(seconds=1))  # 刚过 → overdue
    patched.commit()

    res = _call(warning_hours=24)
    by_id = {i["order_id"]: i["bucket"] for i in res["items"]}
    assert by_id["at_now"] == "critical"
    assert by_id["at_warn"] == "normal"
    assert by_id["just_over"] == "overdue"


def test_warning_hours_param(patched):
    from datetime import timedelta

    _add(patched, "o1", sla=NOW + timedelta(hours=5))
    patched.commit()

    # 默认窗口 24h：距截止 5h → critical
    assert _call(warning_hours=24)["buckets"]["critical"] == 1
    # 收窄到 2h：5h 在窗口外 → normal
    assert _call(warning_hours=2)["buckets"]["normal"] == 1


def test_unknown_has_null_hours_left(patched):
    _add(patched, "o1", sla=None)
    patched.commit()
    res = _call()
    assert res["items"][0]["bucket"] == "unknown"
    assert res["items"][0]["hours_left"] is None


def test_by_shop_aggregation(patched):
    from datetime import timedelta

    _add(patched, "a1", sla=NOW - timedelta(hours=1), shop_id="shop-1")  # overdue
    _add(patched, "a2", sla=NOW + timedelta(hours=48), shop_id="shop-1")  # normal
    _add(patched, "b1", sla=NOW + timedelta(hours=5), shop_id="shop-2")  # critical
    patched.commit()

    res = _call(warning_hours=24)
    by_shop = {s["shop_id"]: s for s in res["by_shop"]}
    assert by_shop["shop-1"]["total"] == 2
    assert by_shop["shop-1"]["overdue"] == 1
    assert by_shop["shop-1"]["normal"] == 1
    assert by_shop["shop-2"]["critical"] == 1
    assert by_shop["shop-2"]["total"] == 1


def test_sorting_overdue_first_unknown_last(patched):
    from datetime import timedelta

    _add(patched, "normal", sla=NOW + timedelta(hours=48))
    _add(patched, "unknown", sla=None)
    _add(patched, "overdue", sla=NOW - timedelta(hours=2))
    _add(patched, "critical", sla=NOW + timedelta(hours=5))
    patched.commit()

    res = _call(warning_hours=24)
    assert [i["order_id"] for i in res["items"]] == ["overdue", "critical", "normal", "unknown"]


def test_local_time_is_utc_plus_7(patched):
    # SLA = UTC 6/12 17:30 → 印尼当地 6/13 00:30
    _add(patched, "o1", sla=datetime(2026, 6, 12, 17, 30))
    patched.commit()
    res = _call()
    assert res["items"][0]["sla_time_local"] == "2026-06-13T00:30:00"


def test_snapshot_at_is_max_synced_at(patched):
    _add(patched, "o1", sla=NOW, synced_at=datetime(2026, 6, 12, 4, 0))
    _add(patched, "o2", sla=NOW, synced_at=datetime(2026, 6, 12, 5, 0))
    patched.commit()
    res = _call()
    # max(synced_at)=UTC 05:00 → 印尼 12:00
    assert res["snapshot_at"] == "2026-06-12T12:00:00"
