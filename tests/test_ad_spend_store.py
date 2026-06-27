"""广告消耗 aggregate + upsert 单测。

aggregate_ad_spend：解析三项广告费 string → Decimal、缺字段补 0、API 负数(扣款)翻成正数(花费)、
翻后仍为负(退款冲回/credit)触发 warning、order_create_time 按印尼业务日（UTC+7）归集（含跨 UTC
日界样本）、三项与 total 累加、currency 取 statement 级（page 级 `currency`，非交易级）。
upsert_ad_spend_daily：同 scope_key 写两次仅一行、字段被刷新（含 raw_response_id）。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from flows.sync_ad_spend import aggregate_ad_spend as _aggregate_task
from models.base_models import FactAdSpendDaily
from services.ad_spend_store import build_ad_spend_scope_key, upsert_ad_spend_daily

aggregate_ad_spend = _aggregate_task  # 已是普通函数（Prefect 已剥离）


def _ts(dt: datetime) -> int:
    """naive UTC datetime → Unix 秒（order_create_time 的口径）。"""
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _txn(create_dt, *, gmv_max=None, tap=None, affiliate=None):
    """单笔交易：202501 交易级无 currency 字段，currency 在 page(statement) 级。"""
    fee = {}
    if gmv_max is not None:
        fee["gmv_max_ad_fee_amount"] = gmv_max
    if tap is not None:
        fee["tap_shop_ads_commission"] = tap
    if affiliate is not None:
        fee["affiliate_ads_commission_amount"] = affiliate
    return {
        "order_id": "o",
        "order_create_time": _ts(create_dt),
        "fee_tax_breakdown": {"fee": fee},
    }


def _page(transactions, *, currency="IDR", statement_id="s1"):
    """statement 级页：currency 来自 `data.currency`（fetch 透传）。"""
    return {"statement_id": statement_id, "currency": currency, "transactions": transactions}


def test_aggregate_parses_sums_and_total():
    """三项 string→Decimal 累加(API 负数翻正)，total=三项之和，transaction_count 计数。"""
    pages = [
        _page([
            _txn(datetime(2026, 6, 8, 10, 0), gmv_max="-100.50", tap="-20.00", affiliate="-5.25"),
            _txn(datetime(2026, 6, 8, 11, 0), gmv_max="-10.00", tap="-2.00", affiliate="-1.00"),
        ]),
    ]
    rows = aggregate_ad_spend(pages)
    assert len(rows) == 1
    r = rows[0]
    assert r["metric_date"] == date(2026, 6, 8)  # UTC 10/11 时 + 7 = 17/18 时，仍 6/8
    assert r["gmv_max_fee"] == Decimal("110.50")
    assert r["tap_commission"] == Decimal("22.00")
    assert r["affiliate_commission"] == Decimal("6.25")
    assert r["total_ad_spend"] == Decimal("138.75")  # 110.50+22.00+6.25
    assert r["transaction_count"] == 2
    assert r["currency"] == "IDR"


def test_aggregate_missing_fields_default_zero():
    """三项任意缺失补 0；fee 整段缺失也补 0，不报错。"""
    pages = [
        _page([
            _txn(datetime(2026, 6, 8, 9, 0), gmv_max="-50.00"),  # 缺 tap / affiliate
            {"order_create_time": _ts(datetime(2026, 6, 8, 9, 30))},  # 完全无 fee
        ]),
    ]
    rows = aggregate_ad_spend(pages)
    assert len(rows) == 1
    r = rows[0]
    assert r["gmv_max_fee"] == Decimal("50.00")
    assert r["tap_commission"] == Decimal("0")
    assert r["affiliate_commission"] == Decimal("0")
    assert r["total_ad_spend"] == Decimal("50.00")
    assert r["transaction_count"] == 2


def test_aggregate_credit_negative_after_flip_warns(caplog):
    """API 正值（退款冲回/credit）翻符号后为负成本，保留负值并对每项 < 0 触发 warning。"""
    pages = [_page([
        _txn(datetime(2026, 6, 8, 9, 0), gmv_max="30.00", tap="5.00", affiliate="2.50"),
    ])]
    with caplog.at_level(logging.WARNING, logger="flows.sync_ad_spend"):
        rows = aggregate_ad_spend(pages)
    r = rows[0]
    # 翻符号后为负成本，原样保留（不再 abs）
    assert r["gmv_max_fee"] == Decimal("-30.00")
    assert r["tap_commission"] == Decimal("-5.00")
    assert r["affiliate_commission"] == Decimal("-2.50")
    assert r["total_ad_spend"] == Decimal("-37.50")
    # 三项各触发一条 warning
    warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
    assert len(warnings) == 3
    msgs = " ".join(rec.getMessage() for rec in warnings)
    assert "gmv_max_ad_fee_amount" in msgs
    assert "tap_shop_ads_commission" in msgs
    assert "affiliate_ads_commission_amount" in msgs


def test_aggregate_normal_spend_no_warning(caplog):
    """正常广告花费（API 负数=扣款，翻正后正成本）不触发任何 warning。"""
    pages = [_page([
        _txn(datetime(2026, 6, 8, 9, 0), gmv_max="-30.00", tap="-5.00", affiliate="-2.50"),
    ])]
    with caplog.at_level(logging.WARNING, logger="flows.sync_ad_spend"):
        rows = aggregate_ad_spend(pages)
    assert rows[0]["total_ad_spend"] == Decimal("37.50")
    assert not [rec for rec in caplog.records if rec.levelno == logging.WARNING]


def test_aggregate_business_day_boundary_utc7():
    """跨 UTC 日界：UTC 6/8 17:00（印尼 6/9 00:00）应归到印尼 6/9，而非 UTC 当日 6/8。"""
    pages = [_page([
        # UTC 6/8 16:00 → 印尼 6/8 23:00 → 6/8
        _txn(datetime(2026, 6, 8, 16, 0), gmv_max="-11.00"),
        # UTC 6/8 17:00 → 印尼 6/9 00:00 → 6/9（关键跨日样本）
        _txn(datetime(2026, 6, 8, 17, 0), gmv_max="-22.00"),
        # UTC 6/8 23:59 → 印尼 6/9 06:59 → 6/9
        _txn(datetime(2026, 6, 8, 23, 59), gmv_max="-33.00"),
    ])]
    rows = {r["metric_date"]: r for r in aggregate_ad_spend(pages)}
    assert set(rows) == {date(2026, 6, 8), date(2026, 6, 9)}
    assert rows[date(2026, 6, 8)]["gmv_max_fee"] == Decimal("11.00")
    assert rows[date(2026, 6, 9)]["gmv_max_fee"] == Decimal("55.00")  # 22+33
    assert rows[date(2026, 6, 9)]["transaction_count"] == 2


def test_aggregate_skips_txn_without_create_time():
    """缺 order_create_time 的交易无法归日，跳过（不崩、不算入任何桶）。"""
    pages = [_page([
        {"fee_tax_breakdown": {"fee": {"gmv_max_ad_fee_amount": "-99.00"}}},
        _txn(datetime(2026, 6, 8, 9, 0), gmv_max="-1.00"),
    ])]
    rows = aggregate_ad_spend(pages)
    assert len(rows) == 1
    assert rows[0]["gmv_max_fee"] == Decimal("1.00")


def test_aggregate_splits_by_currency():
    """同业务日不同币种（来自不同 statement 的 statement 级 currency）分开成两行（不混算）。"""
    pages = [
        _page([_txn(datetime(2026, 6, 8, 9, 0), gmv_max="-100.00")],
              currency="IDR", statement_id="s-idr"),
        _page([_txn(datetime(2026, 6, 8, 9, 0), gmv_max="-7.00")],
              currency="USD", statement_id="s-usd"),
    ]
    rows = {r["currency"]: r for r in aggregate_ad_spend(pages)}
    assert set(rows) == {"IDR", "USD"}
    assert rows["IDR"]["gmv_max_fee"] == Decimal("100.00")
    assert rows["USD"]["gmv_max_fee"] == Decimal("7.00")


# ── upsert 幂等 ──────────────────────────────────────────────────────────────

def _row(metric_date, *, gmv="10", tap="2", aff="1", total="13", currency="IDR", count=1):
    return {
        "metric_date": metric_date,
        "currency": currency,
        "gmv_max_fee": Decimal(gmv),
        "tap_commission": Decimal(tap),
        "affiliate_commission": Decimal(aff),
        "total_ad_spend": Decimal(total),
        "transaction_count": count,
    }


def test_upsert_idempotent_single_row(session):
    """同 scope_key 写两次仅一行。"""
    md = date(2026, 6, 8)
    n1 = upsert_ad_spend_daily(session, [_row(md)], shop_id="shop-1", raw_response_id=11)
    session.commit()
    n2 = upsert_ad_spend_daily(session, [_row(md)], shop_id="shop-1", raw_response_id=11)
    session.commit()
    assert (n1, n2) == (1, 1)
    assert session.query(FactAdSpendDaily).count() == 1


def test_upsert_updates_fields_and_raw_id(session):
    """重跑时逐字段刷新（含 raw_response_id）。"""
    md = date(2026, 6, 8)
    upsert_ad_spend_daily(
        session, [_row(md, gmv="10", tap="2", aff="1", total="13", count=1)],
        shop_id="shop-1", raw_response_id=100,
    )
    session.commit()
    # 同维度同业务日二次写入，金额/计数/raw_id 全变
    upsert_ad_spend_daily(
        session, [_row(md, gmv="50", tap="5", aff="3", total="58", count=4)],
        shop_id="shop-1", raw_response_id=200,
    )
    session.commit()

    assert session.query(FactAdSpendDaily).count() == 1
    rec = session.query(FactAdSpendDaily).one()
    assert rec.gmv_max_fee == Decimal("50")
    assert rec.tap_commission == Decimal("5")
    assert rec.affiliate_commission == Decimal("3")
    assert rec.total_ad_spend == Decimal("58")
    assert rec.transaction_count == 4
    assert rec.raw_response_id == 200  # raw_response_id 已刷新


def test_upsert_distinct_scope_keys_two_rows(session):
    """不同业务日 / 不同店各自独立行（scope_key 不同 → 不互相覆盖）。"""
    upsert_ad_spend_daily(session, [_row(date(2026, 6, 8))], shop_id="shop-1")
    upsert_ad_spend_daily(session, [_row(date(2026, 6, 9))], shop_id="shop-1")
    upsert_ad_spend_daily(session, [_row(date(2026, 6, 8))], shop_id="shop-2")
    session.commit()
    assert session.query(FactAdSpendDaily).count() == 3


def test_scope_key_stable_and_dimensioned():
    """scope_key 对同维度同业务日稳定一致、随业务日/店变化。"""
    k1 = build_ad_spend_scope_key(platform="tiktok_shop", metric_date=date(2026, 6, 8), shop_id="s1")
    k1b = build_ad_spend_scope_key(platform="tiktok_shop", metric_date=date(2026, 6, 8), shop_id="s1")
    k2 = build_ad_spend_scope_key(platform="tiktok_shop", metric_date=date(2026, 6, 9), shop_id="s1")
    k3 = build_ad_spend_scope_key(platform="tiktok_shop", metric_date=date(2026, 6, 8), shop_id="s2")
    assert k1 == k1b
    assert k1 != k2 and k1 != k3
    assert "ad_spend:2026-06-08" in k1
