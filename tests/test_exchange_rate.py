"""中行外汇牌价：解析纯函数 / 幂等 upsert / fx_rate 查表折算 的单测。

不打中行网络（那属集成）。parse 用真实抓取的 HTML 片段；upsert/查表用内存 sqlite。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.db import Base
from models.base_models import FactExchangeRate
from services.exchange_rate_store import parse_boc_html, upsert_exchange_rates

# 真实中行页面截取的两行（表头 + 印尼卢比 + 美元），保真到 td 属性/class/换行缩进。
BOC_HTML_SNIPPET = """
<table cellpadding="0" align="left" cellspacing="0" width="100%">
<thead>
  <tr>
    <th>货币名称</th><th>现汇买入价</th><th>现钞买入价</th><th>现汇卖出价</th>
    <th>现钞卖出价</th><th>中行折算价</th><th>发布日期</th><th>发布时间</th>
  </tr>
</thead>
<tbody>
  <tr data-currency='印尼卢比'>
    <td>印尼卢比</td><td>0.0373</td><td>0.0373</td><td>0.0383</td>
    <td>0.0383</td><td>0.0379</td><td class="pjrq">2026/07/02 00:03:29</td><td>00:03:29</td>
  </tr>
  <tr data-currency='美元'>
    <td>美元</td><td>678.5</td><td>673.1</td><td>681.5</td>
    <td>681.5</td><td>680.67</td><td class="pjrq">2026/07/02 00:03:29</td><td>00:03:29</td>
  </tr>
  <tr>
    <td>&nbsp;</td><td colspan="7">【点击进入历史牌价检索】</td>
  </tr>
</tbody>
</table>
"""


@pytest.fixture()
def fx_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


# ── 1. 解析纯函数 ────────────────────────────────────────────────────────────
def test_parse_boc_html_extracts_currencies():
    rows = parse_boc_html(BOC_HTML_SNIPPET)
    by_code = {r["currency_code"]: r for r in rows}
    # 提示行（点击进入历史牌价）被跳过，只留两个真实币种
    assert set(by_code) == {"IDR", "USD"}

    idr = by_code["IDR"]
    assert idr["currency_name"] == "印尼卢比"
    assert idr["unit"] == 100
    assert idr["rate_middle"] == Decimal("0.0379")
    assert idr["spot_buy"] == Decimal("0.0373")
    assert idr["metric_date"] == date(2026, 7, 2)
    assert idr["published_at"] == datetime(2026, 7, 2, 0, 3, 29)
    # 单位口径校验：折算价/unit ≈ 0.00045 同量级
    assert Decimal("0.0003") < idr["rate_middle"] / idr["unit"] < Decimal("0.0006")


def test_parse_boc_html_empty_on_garbage():
    assert parse_boc_html("<html>no table here</html>") == []


# ── 2. 幂等 upsert（唯一键 source+currency_code+published_at）──────────────────
def test_upsert_same_published_at_is_idempotent(fx_session):
    """同一发布时刻重复抓（published_at 相同）→ 不新增行、值更新。"""
    rows = parse_boc_html(BOC_HTML_SNIPPET)
    n1 = upsert_exchange_rates(fx_session, rows)
    fx_session.commit()
    assert n1 == 2
    assert fx_session.query(FactExchangeRate).count() == 2

    rows2 = parse_boc_html(BOC_HTML_SNIPPET)  # 同 published_at
    next(r for r in rows2 if r["currency_code"] == "IDR")["rate_middle"] = Decimal("0.0400")
    upsert_exchange_rates(fx_session, rows2)
    fx_session.commit()
    assert fx_session.query(FactExchangeRate).count() == 2  # 仍 2 行（去重）
    idr = fx_session.query(FactExchangeRate).filter_by(currency_code="IDR").one()
    assert idr.rate_middle == Decimal("0.0400")  # 值已更新


def test_upsert_new_published_at_adds_row(fx_session):
    """中行日内真更新（新 published_at）→ 当天多存一行样本。"""
    rows = parse_boc_html(BOC_HTML_SNIPPET)
    upsert_exchange_rates(fx_session, rows)
    fx_session.commit()

    # 模拟第二次抓取：同日、不同发布时刻、不同折算价
    rows2 = parse_boc_html(BOC_HTML_SNIPPET)
    for r in rows2:
        r["published_at"] = datetime(2026, 7, 2, 15, 33, 10)
        if r["currency_code"] == "IDR":
            r["rate_middle"] = Decimal("0.0385")
    upsert_exchange_rates(fx_session, rows2)
    fx_session.commit()

    # IDR 当天现在有两行样本（0.0379 早盘 + 0.0385 午后）
    idr_rows = fx_session.query(FactExchangeRate).filter_by(currency_code="IDR").all()
    assert len(idr_rows) == 2
    assert {r.rate_middle for r in idr_rows} == {Decimal("0.0379"), Decimal("0.0385")}


def test_upsert_skips_rows_without_published_at(fx_session):
    rows = [{"currency_code": "IDR", "currency_name": "印尼卢比", "unit": 100,
             "rate_middle": Decimal("0.0379"), "metric_date": date(2026, 7, 2),
             "published_at": None}]
    assert upsert_exchange_rates(fx_session, rows) == 0
    assert fx_session.query(FactExchangeRate).count() == 0


# ── 3. fx_rate 查表折算（当日均值 / 回退最近交易日 / 回落）──────────────────
def _seed_idr(session, metric_date, rate_middle="0.0379", unit=100, hour=10):
    """插一条 IDR 样本；published_at 用 metric_date + hour 保证唯一。"""
    session.add(FactExchangeRate(
        metric_date=metric_date, source="boc", currency_code="IDR",
        currency_name="印尼卢比", unit=unit, rate_middle=Decimal(rate_middle),
        published_at=datetime(metric_date.year, metric_date.month, metric_date.day, hour, 33),
    ))
    session.commit()


def test_get_idr_to_rmb_averages_same_day_samples(fx_session, monkeypatch):
    """当天多样本 → 取简单平均。"""
    import services.fx_rate as fx

    fx.clear_rate_cache()
    _seed_idr(fx_session, date(2026, 7, 2), rate_middle="0.0379", hour=10)
    _seed_idr(fx_session, date(2026, 7, 2), rate_middle="0.0385", hour=15)
    _seed_idr(fx_session, date(2026, 7, 2), rate_middle="0.0391", hour=22)
    monkeypatch.setattr("core.db.SessionLocal", lambda: fx_session)

    # 均值 = (0.0379+0.0385+0.0391)/3 = 0.0385，再 /100
    rate = fx.get_idr_to_rmb(date(2026, 7, 2))
    assert rate == Decimal("0.0385") / Decimal("100")


def test_get_idr_to_rmb_falls_back_to_recent_trading_day(fx_session, monkeypatch):
    """当日无样本 → 回退 ≤on_date 最近交易日的均值，不越界拿更晚的。"""
    import services.fx_rate as fx

    fx.clear_rate_cache()
    _seed_idr(fx_session, date(2026, 7, 1), rate_middle="0.0370", hour=10)
    _seed_idr(fx_session, date(2026, 7, 1), rate_middle="0.0372", hour=15)  # 7/1 均值 0.0371
    _seed_idr(fx_session, date(2026, 7, 2), rate_middle="0.0379", hour=10)
    monkeypatch.setattr("core.db.SessionLocal", lambda: fx_session)

    # 查 7/3（周末）→ 取 ≤7/3 最近的 7/2（单样本 0.0379）
    assert fx.get_idr_to_rmb(date(2026, 7, 3)) == Decimal("0.0379") / Decimal("100")
    # 查 7/1 → 取 7/1 两样本均值 0.0371，不越界拿 7/2
    fx.clear_rate_cache()
    assert fx.get_idr_to_rmb(date(2026, 7, 1)) == Decimal("0.0371") / Decimal("100")


def test_get_idr_to_rmb_falls_back_when_no_row(fx_session, monkeypatch):
    import services.fx_rate as fx
    from core.config import settings

    fx.clear_rate_cache()
    # 表空（无 IDR 行）→ 回落固定值
    monkeypatch.setattr("core.db.SessionLocal", lambda: fx_session)
    assert fx.get_idr_to_rmb(date(2026, 7, 2)) == Decimal(str(settings.idr_to_rmb))


def test_get_idr_to_rmb_no_date_uses_fixed(monkeypatch):
    import services.fx_rate as fx
    from core.config import settings

    fx.clear_rate_cache()
    # 不传 on_date → 直接固定值，不查库
    assert fx.get_idr_to_rmb() == Decimal(str(settings.idr_to_rmb))


# ── 4. 汇率走势序列（services/fx_series，供 /board/fx 页面）──────────────────────
def _seed_fx(session, metric_date, code="IDR", rate_middle="0.0379", unit=100, hour=10):
    """插一条任意币种样本；published_at 用 metric_date + hour 保证唯一。"""
    session.add(FactExchangeRate(
        metric_date=metric_date, source="boc", currency_code=code,
        currency_name="印尼卢比" if code == "IDR" else code,
        unit=unit, rate_middle=Decimal(rate_middle),
        published_at=datetime(metric_date.year, metric_date.month, metric_date.day, hour, 33),
    ))
    session.commit()


def test_fx_series_averages_same_day_and_orders(fx_session, monkeypatch):
    """当天多样本取均值、多日按日期升序、change_pct=区间涨跌幅（口径同 fx_rate）。"""
    from datetime import timedelta

    import services.fx_series as fxs

    today = date.today()
    d0, d1 = today - timedelta(days=2), today - timedelta(days=1)
    _seed_fx(fx_session, d0, rate_middle="0.0370", hour=10)
    _seed_fx(fx_session, d0, rate_middle="0.0380", hour=15)  # d0 均值 0.0375 → /100
    _seed_fx(fx_session, d1, rate_middle="0.0400", hour=10)  # d1 单样本 0.0400 → /100
    monkeypatch.setattr("services.fx_series.SessionLocal", lambda: fx_session)

    s = fxs.get_fx_series("IDR", 90)
    assert [p["date"] for p in s["points"]] == [d0.isoformat(), d1.isoformat()]
    assert s["points"][0]["rate"] == pytest.approx(0.0375 / 100)
    assert s["points"][1]["rate"] == pytest.approx(0.0400 / 100)
    assert s["latest"] == pytest.approx(0.0400 / 100)
    assert s["start_rate"] == pytest.approx(0.0375 / 100)
    # 涨跌幅 = (0.0400-0.0375)/0.0375*100 = 6.67%
    assert s["change_pct"] == pytest.approx(6.67, abs=0.01)


def test_fx_series_filters_currency_and_window(fx_session, monkeypatch):
    """只取指定币种、且窗口外（早于 days）的样本不进序列。"""
    from datetime import timedelta

    import services.fx_series as fxs

    today = date.today()
    _seed_fx(fx_session, today - timedelta(days=5), code="USD", rate_middle="680.0", hour=10)
    _seed_fx(fx_session, today - timedelta(days=5), code="IDR", rate_middle="0.0379", hour=10)
    _seed_fx(fx_session, today - timedelta(days=100), code="IDR", rate_middle="0.0300", hour=10)  # 窗口外
    monkeypatch.setattr("services.fx_series.SessionLocal", lambda: fx_session)

    usd = fxs.get_fx_series("USD", 30)
    assert len(usd["points"]) == 1 and usd["points"][0]["rate"] == pytest.approx(6.8)
    idr = fxs.get_fx_series("IDR", 30)  # 30 天窗口排除 100 天前那条
    assert len(idr["points"]) == 1


def test_fx_series_empty_when_no_data(fx_session, monkeypatch):
    """无数据 → 空序列、latest/change 为 None，不抛错（前端走空态）。"""
    import services.fx_series as fxs

    monkeypatch.setattr("services.fx_series.SessionLocal", lambda: fx_session)
    s = fxs.get_fx_series("IDR", 90)
    assert s["points"] == [] and s["latest"] is None and s["change_pct"] is None


def test_fx_list_currencies_only_present(fx_session, monkeypatch):
    """下拉只列库里有数据的常用币种，IDR 恒在、CNY 作对照保留。"""
    from datetime import timedelta

    import services.fx_series as fxs

    _seed_fx(fx_session, date.today() - timedelta(days=1), code="USD", rate_middle="680.0")
    monkeypatch.setattr("services.fx_series.SessionLocal", lambda: fx_session)
    codes = [c["code"] for c in fxs.list_currencies()]
    assert "USD" in codes and "IDR" in codes and "CNY" in codes
    assert "MYR" not in codes  # 未入库不进下拉
    # 每项带中文名
    usd = next(c for c in fxs.list_currencies() if c["code"] == "USD")
    assert usd["name"] == "美元"
