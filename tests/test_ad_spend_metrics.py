"""ad_metrics 取数 + ROAS 单测。

get_ad_spend_summary：按日期范围 + shop_ids 过滤聚合三项 + total + currency。
get_roas：有 gmv+spend 算出正确 roas；spend=0 → roas None（不臆造）。

数据落 sqlite，monkeypatch ad_metrics.SessionLocal 指向内存 session。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from models.base_models import FactAdSpendDaily
from services import ad_metrics


def _spend_row(session, md, *, shop_id="shop-1", gmv="100", tap="20", aff="5",
               total="125", currency="IDR", platform="tiktok_shop", country="ID"):
    from services.ad_spend_store import build_ad_spend_scope_key

    session.add(FactAdSpendDaily(
        metric_date=md,
        platform=platform,
        country=country,
        shop_id=shop_id,
        scope_key=build_ad_spend_scope_key(
            platform=platform, metric_date=md, country=country, shop_id=shop_id,
        ),
        currency=currency,
        gmv_max_fee=Decimal(gmv),
        tap_commission=Decimal(tap),
        affiliate_commission=Decimal(aff),
        total_ad_spend=Decimal(total),
        transaction_count=1,
    ))


def test_summary_aggregates_window_and_shop(session, monkeypatch):
    monkeypatch.setattr(ad_metrics, "SessionLocal", lambda: session)
    # 窗口内两天 + 窗口外一天 + 别的店一天
    _spend_row(session, date(2026, 6, 8), shop_id="shop-1", gmv="100", tap="20", aff="5", total="125")
    _spend_row(session, date(2026, 6, 9), shop_id="shop-1", gmv="200", tap="30", aff="10", total="240")
    _spend_row(session, date(2026, 6, 1), shop_id="shop-1", gmv="999", tap="0", aff="0", total="999")  # 窗口外
    _spend_row(session, date(2026, 6, 8), shop_id="shop-9", gmv="888", tap="0", aff="0", total="888")  # 别的店
    session.commit()

    out = ad_metrics.get_ad_spend_summary(
        start_date=date(2026, 6, 8), end_date=date(2026, 6, 10),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"],
    )
    assert out["total_ad_spend"] == 365.0   # 125 + 240
    assert out["gmv_max_fee"] == 300.0       # 100 + 200
    assert out["tap_commission"] == 50.0     # 20 + 30
    assert out["affiliate_commission"] == 15.0  # 5 + 10
    assert out["currency"] == "IDR"
    assert out["start_date"] == "2026-06-08"
    assert out["end_date"] == "2026-06-10"


def test_summary_empty_returns_zeroes(session, monkeypatch):
    monkeypatch.setattr(ad_metrics, "SessionLocal", lambda: session)
    out = ad_metrics.get_ad_spend_summary(
        start_date=date(2026, 6, 8), end_date=date(2026, 6, 10), shop_ids=["nope"],
    )
    assert out["total_ad_spend"] == 0.0
    assert out["gmv_max_fee"] == 0.0
    assert out["currency"] is None


def test_get_roas_computes_ratio(session, monkeypatch):
    monkeypatch.setattr(ad_metrics, "SessionLocal", lambda: session)
    _spend_row(session, date(2026, 6, 8), shop_id="shop-1", gmv="0", tap="0", aff="0", total="100")
    session.commit()
    # GMV 由 get_gmv_summary 提供，这里直接 stub 掉（口径另有专测）
    monkeypatch.setattr(
        ad_metrics, "get_gmv_summary",
        lambda **kw: {"gmv": 500.0, "order_count": 5, "units_sold": 5, "avg_order_value": 100.0},
    )
    out = ad_metrics.get_roas(
        start_date=date(2026, 6, 8), end_date=date(2026, 6, 8),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"],
    )
    assert out["gmv"] == 500.0
    assert out["ad_spend"] == 100.0
    assert out["roas"] == 5.0  # 500 / 100


def test_get_roas_none_when_spend_zero(session, monkeypatch):
    monkeypatch.setattr(ad_metrics, "SessionLocal", lambda: session)
    # 无任何 spend 行 → total_ad_spend=0
    monkeypatch.setattr(
        ad_metrics, "get_gmv_summary",
        lambda **kw: {"gmv": 500.0, "order_count": 5, "units_sold": 5, "avg_order_value": 100.0},
    )
    out = ad_metrics.get_roas(
        start_date=date(2026, 6, 8), end_date=date(2026, 6, 8), shop_ids=["shop-1"],
    )
    assert out["ad_spend"] == 0.0
    assert out["roas"] is None  # spend=0 → 不臆造，None
