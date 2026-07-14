"""产品成本历史化回归锁：as-of 三级兜底 + append-on-change 去重。

覆盖：按生效日取当日成本、记录前日期回落最早已知、历史空时回落 product_costs 当前值；
导入 append-on-change（涨价追加新行、未变不追加、同日改价更新当日行）。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from models.base_models import ProductCost, ProductCostHistory
from services.product_cost_store import (
    get_cost_map_asof,
    import_costs_from_rows,
    record_cost_history,
)

ACC, PLAT, SKU = "t1", "tiktok_shop", "809-KH-L"


def _hist_count(session, sku=SKU):
    return (
        session.query(ProductCostHistory)
        .filter_by(account_id=ACC, platform=PLAT, seller_sku=sku)
        .count()
    )


def test_asof_picks_cost_effective_at_date(session):
    d1, d2 = date(2026, 6, 1), date(2026, 7, 1)
    record_cost_history(session, [{"seller_sku": SKU, "unit_cost_rmb": Decimal("10")}],
                        account_id=ACC, platform=PLAT, effective_from=d1)
    record_cost_history(session, [{"seller_sku": SKU, "unit_cost_rmb": Decimal("12")}],
                        account_id=ACC, platform=PLAT, effective_from=d2)
    session.commit()

    def asof(d):
        return get_cost_map_asof(account_id=ACC, platform=PLAT, metric_date=d, session=session)[SKU]

    assert asof(date(2026, 5, 1)) == Decimal("10")   # 早于最早行 → 最早已知
    assert asof(d1) == Decimal("10")                  # 恰在 d1
    assert asof(date(2026, 6, 15)) == Decimal("10")   # d1~d2 之间 → 仍 d1 价
    assert asof(d2) == Decimal("12")                  # 恰在 d2 → 新价
    assert asof(date(2026, 7, 20)) == Decimal("12")   # 晚于 d2 → 新价


def test_asof_falls_back_to_product_costs_when_no_history(session):
    # 只填 product_costs（历史表空）→ 任意日期回落当前快照
    session.add(ProductCost(account_id=ACC, platform=PLAT, seller_sku="X-1",
                            unit_cost_rmb=Decimal("5.5")))
    session.commit()
    m = get_cost_map_asof(account_id=ACC, platform=PLAT, metric_date=date(2026, 1, 1), session=session)
    assert m["X-1"] == Decimal("5.5")


def test_append_on_change(session):
    rows = lambda c: [{"seller_sku": SKU, "unit_cost_rmb": Decimal(c)}]
    # d1 首次 → 1 行
    import_costs_from_rows(session, rows("10"), account_id=ACC, platform=PLAT, effective_from=date(2026, 6, 1))
    assert _hist_count(session) == 1
    # 更晚日期、同价 → 不追加
    import_costs_from_rows(session, rows("10"), account_id=ACC, platform=PLAT, effective_from=date(2026, 6, 10))
    assert _hist_count(session) == 1
    # 更晚日期、涨价 → 追加
    import_costs_from_rows(session, rows("12"), account_id=ACC, platform=PLAT, effective_from=date(2026, 7, 1))
    assert _hist_count(session) == 2
    # 再更晚、同新价 → 不追加
    import_costs_from_rows(session, rows("12"), account_id=ACC, platform=PLAT, effective_from=date(2026, 7, 5))
    assert _hist_count(session) == 2
    session.commit()


def test_same_day_reimport_updates_row(session):
    import_costs_from_rows(session, [{"seller_sku": SKU, "unit_cost_rmb": Decimal("10")}],
                          account_id=ACC, platform=PLAT, effective_from=date(2026, 6, 1))
    # 同一生效日再导入不同价 → 更新当日行,不新增
    import_costs_from_rows(session, [{"seller_sku": SKU, "unit_cost_rmb": Decimal("11")}],
                          account_id=ACC, platform=PLAT, effective_from=date(2026, 6, 1))
    session.commit()
    assert _hist_count(session) == 1
    got = get_cost_map_asof(account_id=ACC, platform=PLAT, metric_date=date(2026, 6, 1), session=session)[SKU]
    assert got == Decimal("11")


def test_import_still_upserts_current_snapshot(session):
    # 历史化不破坏 product_costs 当前快照写入
    res = import_costs_from_rows(session, [{"seller_sku": SKU, "unit_cost_rmb": Decimal("9.9")}],
                                account_id=ACC, platform=PLAT, effective_from=date(2026, 6, 1))
    session.commit()
    assert res["inserted"] == 1 and res["errors"] == []
    cur = session.query(ProductCost).filter_by(account_id=ACC, platform=PLAT, seller_sku=SKU).one()
    assert cur.unit_cost_rmb == Decimal("9.9")
