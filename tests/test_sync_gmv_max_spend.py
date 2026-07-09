"""GMV Max 花费 sync flow 回归锁：分片 + 编排 + 幂等（假 client，不发网络）。

坐实点：
- 长区间按 ≤30 天分片（含 stat_time_day 时官方窗口上限）
- 逐 (advertiser, store) 循环（store_ids 单次≤1）
- 解析后写 fact_gmv_max_spend_daily，重复跑幂等（scope_key 去重）
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import sessionmaker

import flows.sync_gmv_max_spend as flow_mod
from models.base_models import FactGmvMaxSpendDaily


def test_shard_within_30_days():
    # 单片：<30 天不切
    shards = list(flow_mod.iter_date_shards(date(2025, 7, 1), date(2025, 7, 7)))
    assert shards == [(date(2025, 7, 1), date(2025, 7, 7))]


def test_shard_splits_long_range():
    # 90 天 → 多片，每片 ≤30 天，闭区间无缝衔接、不重叠
    shards = list(flow_mod.iter_date_shards(date(2025, 1, 1), date(2025, 3, 31)))
    assert len(shards) >= 3
    for s, e in shards:
        assert (e - s).days <= flow_mod.MAX_WINDOW_DAYS
    # 衔接：下一片起点 = 上一片终点 + 1 天
    for (s1, e1), (s2, e2) in zip(shards, shards[1:]):
        assert (s2 - e1).days == 1
    # 覆盖完整
    assert shards[0][0] == date(2025, 1, 1)
    assert shards[-1][1] == date(2025, 3, 31)


class _FakeClient:
    """按 (adv, store) 返回预置报表；记录被请求的窗口，验证分片真的逐片调。"""

    def __init__(self):
        self.access_token = "TOK"
        self.report_calls = []

    def get_advertisers(self):
        return [{"advertiser_id": "adv1"}]

    def get_gmv_max_stores(self, advertiser_id):
        return [{"store_id": "store9", "is_gmv_max_available": True}]

    def get_gmv_max_report(self, advertiser_id, store_id, start_date, end_date, **kw):
        self.report_calls.append((advertiser_id, store_id, start_date, end_date))
        return {
            "list": [
                {"dimensions": {"stat_time_day": start_date},
                 "metrics": {"cost": "1000.00", "net_cost": "900.00",
                             "gross_revenue": "5000.00", "orders": "10",
                             "roi": "5.00", "currency": "IDR"}},
            ],
            "page_info": {"total_number": 1},
        }


def test_flow_end_to_end(session, monkeypatch):
    TestSession = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    fake = _FakeClient()

    written = flow_mod.sync_gmv_max_spend_flow(
        account_id="ecom-app",
        start_date=date(2025, 7, 1),
        end_date=date(2025, 7, 5),
        client=fake,
        session_factory=TestSession,
    )
    assert written == 1
    # 请求确实按 (adv1, store9) 发出
    assert fake.report_calls[0][0] == "adv1"
    assert fake.report_calls[0][1] == "store9"

    rows = session.query(FactGmvMaxSpendDaily).all()
    assert len(rows) == 1
    assert rows[0].cost == 1000
    assert rows[0].currency == "IDR"
    assert rows[0].seller_id == "adv1"   # advertiser_id 落 seller_id 槽
    assert rows[0].shop_id == "store9"


def test_flow_idempotent(session, monkeypatch):
    TestSession = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    fake = _FakeClient()
    for _ in range(2):
        flow_mod.sync_gmv_max_spend_flow(
            account_id="ecom-app",
            start_date=date(2025, 7, 1), end_date=date(2025, 7, 3),
            client=fake, session_factory=TestSession,
        )
    # 同一天重复跑不产生重复行
    rows = session.query(FactGmvMaxSpendDaily).all()
    assert len(rows) == 1


def test_flow_long_range_shards_multiple_calls(session):
    TestSession = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    fake = _FakeClient()
    flow_mod.sync_gmv_max_spend_flow(
        account_id="ecom-app",
        start_date=date(2025, 1, 1), end_date=date(2025, 3, 31),
        client=fake, session_factory=TestSession,
    )
    # 90 天 → 多次报表调用（分片生效）
    assert len(fake.report_calls) >= 3
