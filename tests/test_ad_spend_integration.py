"""端到端：假 client 喂预置结算/交易页 → flow 的 fetch+aggregate+save 写 sqlite →
get_ad_spend_summary 读回，金额闭环验证。

隔离点：
  - flows.sync_ad_spend.TikTokShopClient 换成 FakeClient（不发网络）；
  - flows.sync_ad_spend.SessionLocal 与 ad_metrics.SessionLocal 都指向同一内存 session。
flow 内已是普通函数（Prefect 已剥离），直接调用。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import flows.sync_ad_spend as flow_mod
from services import ad_metrics


def _ts(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _txn(txn_id, create_dt, gmv_max, tap, affiliate):
    # 202501 交易级无 currency 字段；currency 在 statement(page) 级。
    return {
        "id": txn_id,
        "order_id": "o",
        "order_create_time": _ts(create_dt),
        "settlement_amount": "200.00",
        "fee_tax_breakdown": {"fee": {
            "gmv_max_ad_fee_amount": gmv_max,
            "tap_shop_ads_commission": tap,
            "affiliate_ads_commission_amount": affiliate,
            "platform_commission_amount": "15.00",  # 非广告费 → 验证费用表全字段
        }},
    }


class FakeClient:
    """构造签名兼容真 client 的最小桩；返回两页 statements、每个 statement 一页交易。"""

    def __init__(self, **kwargs):
        pass

    def iter_statements(self, **kwargs):
        yield {"statements": [{"id": "S1"}], "next_page_token": "P2"}
        yield {"statements": [{"id": "S2"}]}

    def iter_statement_transactions(self, statement_id, **kwargs):
        # currency 在 statement 级 `data.currency`（真 client 直接 yield data）。
        if statement_id == "S1":
            yield {"currency": "IDR", "transactions": [
                _txn("t1", datetime(2026, 6, 8, 10, 0), "100.00", "20.00", "5.00"),
            ]}
        else:  # S2
            yield {"currency": "IDR", "transactions": [
                _txn("t2", datetime(2026, 6, 8, 11, 0), "10.00", "2.00", "1.00"),
                # 跨日：UTC 6/8 17:30 → 印尼 6/9
                _txn("t3", datetime(2026, 6, 8, 17, 30), "50.00", "0.00", "0.00"),
            ]}


def test_flow_end_to_end_writes_and_reads_back(session, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    TestSession = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(flow_mod, "TikTokShopClient", FakeClient)
    monkeypatch.setattr(flow_mod, "SessionLocal", TestSession)
    monkeypatch.setattr(ad_metrics, "SessionLocal", lambda: session)

    ge, lt = 1000, 2000
    pages = flow_mod.fetch_ad_spend(
        statement_time_ge=ge, statement_time_lt=lt,
        country="ID", shop_id="shop-1",
    )
    rows = flow_mod.aggregate_ad_spend(pages)
    fee_rows = flow_mod.parse_order_fees_task(pages)
    count = flow_mod.save_ad_spend_to_db(
        pages, rows, fee_rows, statement_time_ge=ge, statement_time_lt=lt,
        country="ID", shop_id="shop-1",
    )
    # 两个业务日分组：6/8 与 6/9
    assert count == 2

    # 交易级费用表：3 笔交易 → 3 行，全字段写入（含非广告费的 platform_commission）
    from models.base_models import FactFinanceTransaction
    txns = session.query(FactFinanceTransaction).order_by(FactFinanceTransaction.transaction_id).all()
    assert [t.transaction_id for t in txns] == ["t1", "t2", "t3"]
    assert txns[0].metric_date == date(2026, 6, 8)
    assert txns[0].settlement_amount == Decimal("200.00")
    assert txns[0].platform_commission_amount == Decimal("15.00")
    assert txns[0].gmv_max_fee == Decimal("100.00")
    assert txns[2].metric_date == date(2026, 6, 9)  # 跨日那笔

    # 6/8：S1(100/20/5) + S2 第1笔(10/2/1) = 110/22/6, total 138
    s8 = ad_metrics.get_ad_spend_summary(
        start_date=date(2026, 6, 8), end_date=date(2026, 6, 8),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"],
    )
    assert s8["gmv_max_fee"] == 110.0
    assert s8["tap_commission"] == 22.0
    assert s8["affiliate_commission"] == 6.0
    assert s8["total_ad_spend"] == 138.0
    assert s8["currency"] == "IDR"

    # 6/9：S2 跨日那笔 50/0/0
    s9 = ad_metrics.get_ad_spend_summary(
        start_date=date(2026, 6, 9), end_date=date(2026, 6, 9),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"],
    )
    assert s9["total_ad_spend"] == 50.0
    assert s9["gmv_max_fee"] == 50.0

    # 整窗 6/8~6/9 合计 188
    s_all = ad_metrics.get_ad_spend_summary(
        start_date=date(2026, 6, 8), end_date=date(2026, 6, 9),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"],
    )
    assert s_all["total_ad_spend"] == 188.0


def test_flow_save_is_idempotent(session, monkeypatch):
    """同窗口跑两次 → 仍只两行（scope_key upsert 幂等）。"""
    from sqlalchemy.orm import sessionmaker

    from models.base_models import FactAdSpendDaily, FactFinanceTransaction

    TestSession = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(flow_mod, "TikTokShopClient", FakeClient)
    monkeypatch.setattr(flow_mod, "SessionLocal", TestSession)

    for _ in range(2):
        pages = flow_mod.fetch_ad_spend(
            statement_time_ge=1000, statement_time_lt=2000, country="ID", shop_id="shop-1",
        )
        rows = flow_mod.aggregate_ad_spend(pages)
        fee_rows = flow_mod.parse_order_fees_task(pages)
        flow_mod.save_ad_spend_to_db(
            pages, rows, fee_rows, statement_time_ge=1000, statement_time_lt=2000,
            country="ID", shop_id="shop-1",
        )
    # 日级 2 行 + 交易级 3 行，两次跑均幂等
    assert session.query(FactAdSpendDaily).count() == 2
    assert session.query(FactFinanceTransaction).count() == 3
