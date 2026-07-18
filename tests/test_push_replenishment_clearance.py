"""push_replenishment 清仓嫌疑分析块单测：_llm_clearance_text 的 LLM→确定性降级链，
以及 _clearance_advice 的过滤/异常保护/多变体聚合。LLM 与 DB 均用 monkeypatch 替身，不真连。

查询正确性（signals 的 SQL）与 LLM 真实输出质量靠 prod 真实数据验证，不在此重复。
"""
from __future__ import annotations

import flows.push_replenishment as mod
from flows.push_replenishment import _clearance_advice, _llm_clearance_text


_SUSPECTS = [{"skus": ["SS-1", "SS-2"], "reason": "折扣加深 +12pp（50%→65%）"}]


class _FakeScope:
    platform = "tiktok_shop"
    country = "ID"
    shop_ids = ["123"]


def _patch_llm(monkeypatch, *, complete_ret=None, complete_exc=None, provider_ok=True):
    """替身 LLM 层：get_provider 控未配置，complete_text 控返值/抛异常。"""
    if provider_ok:
        monkeypatch.setattr(mod, "get_provider", lambda: object())
    else:
        def _raise():
            raise RuntimeError("LLM 未配置")
        monkeypatch.setattr(mod, "get_provider", _raise)
    if complete_exc is not None:
        def _boom(*a, **kw):
            raise complete_exc
        monkeypatch.setattr(mod, "complete_text", _boom)
    else:
        monkeypatch.setattr(mod, "complete_text", lambda *a, **kw: complete_ret)


# ── _llm_clearance_text：LLM→确定性降级链 ─────────────────────────────────

def test_llm_text_uses_llm_output(monkeypatch):
    """LLM 正常返回 → 带 ⚠️ 前缀的 LLM 文案。"""
    _patch_llm(monkeypatch, complete_ret="SS-1 款折扣持续加深，疑似清尾货。")
    out = _llm_clearance_text(_SUSPECTS)
    assert out.startswith("⚠️ 疑似清仓")
    assert "SS-1 款折扣持续加深" in out


def test_llm_text_fallback_when_provider_unconfigured(monkeypatch):
    """get_provider 抛（未配置 api_key/model）→ 确定性 • 列表，不抛。"""
    _patch_llm(monkeypatch, provider_ok=False)
    out = _llm_clearance_text(_SUSPECTS)
    assert "⚠️ 疑似清仓" in out
    assert "• SS-1/SS-2：折扣加深 +12pp" in out  # 多 seller_sku 用 / 连 + reason


def test_llm_text_fallback_on_exception(monkeypatch):
    """complete_text 抛（网络/鉴权/非 200）→ 降级确定性文案。"""
    _patch_llm(monkeypatch, complete_exc=RuntimeError("timeout"))
    out = _llm_clearance_text(_SUSPECTS)
    assert "• SS-1/SS-2" in out


def test_llm_text_fallback_on_empty(monkeypatch):
    """complete_text 返空 → 降级（不输出空分析块）。"""
    _patch_llm(monkeypatch, complete_ret="   ")
    out = _llm_clearance_text(_SUSPECTS)
    assert "• SS-1/SS-2" in out


# ── _clearance_advice：过滤/异常保护/聚合 ──────────────────────────────────

def test_advice_none_when_no_product_ids():
    """rows 无 product_id → 直接 None，不查库。"""
    rows = [{"product_id": None, "seller_sku": "SS-1"}]
    assert _clearance_advice(rows, scope=_FakeScope(), session=object()) is None


def test_advice_none_when_no_suspect(monkeypatch):
    """全非嫌疑 → None（卡片不加块）。"""
    monkeypatch.setattr(
        mod, "compute_clearance_signals",
        lambda pids, **kw: {p: {"suspect": False, "reason": ""} for p in pids},
    )
    rows = [{"product_id": "P1", "seller_sku": "SS-1"}]
    assert _clearance_advice(rows, scope=_FakeScope(), session=object()) is None


def test_advice_none_when_signal_raises(monkeypatch):
    """compute_clearance_signals 抛 → 返 None，清仓异常不阻断补货推送。"""
    def _boom(*a, **kw):
        raise RuntimeError("DB down")
    monkeypatch.setattr(mod, "compute_clearance_signals", _boom)
    rows = [{"product_id": "P1", "seller_sku": "SS-1"}]
    assert _clearance_advice(rows, scope=_FakeScope(), session=object()) is None


def test_advice_routes_suspects_and_aggregates_variants(monkeypatch):
    """有嫌疑 → 过滤出 suspect 款喂 LLM；同款多变体 seller_sku 聚合为一个标识。"""
    monkeypatch.setattr(
        mod, "compute_clearance_signals",
        lambda pids, **kw: {
            "P1": {"suspect": True, "reason": "折扣加深 +12pp（50%→65%）"},
            "P2": {"suspect": False, "reason": ""},  # 非嫌疑应被过滤掉
        },
    )
    captured = {}

    def _fake_llm(suspects):
        captured["suspects"] = suspects
        return "LLM 文案"

    monkeypatch.setattr(mod, "_llm_clearance_text", _fake_llm)
    rows = [
        {"product_id": "P1", "seller_sku": "SS-1"},
        {"product_id": "P1", "seller_sku": "SS-1B"},  # 同款另一变体
        {"product_id": "P2", "seller_sku": "SS-2"},   # 非嫌疑
    ]
    assert _clearance_advice(rows, scope=_FakeScope(), session=object()) == "LLM 文案"
    assert len(captured["suspects"]) == 1                      # 只有 P1
    assert captured["suspects"][0]["skus"] == ["SS-1", "SS-1B"]  # 两变体聚合
