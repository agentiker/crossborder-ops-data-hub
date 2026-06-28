"""业务规则知识库（services/business_rules）+ ops_business_rules 工具 单测。

覆盖：分节解析正确、按编号取节、缺/错节降级、文档缺失降级、工具分支早返回不碰 scope。
"""

import pytest

from services import business_rules
from services.user_authz import UserPermission
from web.agent_tools import TOOL_NAMES, run_tool

PERM = UserPermission(
    open_id="ou_rules", role="boss", allowed_scope_key=None,
    channel="feishu", account_id="ecom-app",
)


def test_list_sections_covers_numbered_chapters():
    secs = business_rules.list_sections()
    ids = [s["id"] for s in secs]
    # 文档有编号 1–7 的大节（§1 时区 … §7 告警）
    for n in ("1", "2", "3", "4", "5", "6", "7"):
        assert n in ids, f"缺章节 {n}：{ids}"
    # 每节都有非空标题
    assert all(s["title"].strip() for s in secs)


def test_get_rules_by_section_returns_body():
    r = business_rules.get_rules("2")
    assert r["available"] is True
    assert r["section"]["id"] == "2"
    assert "GMV" in r["section"]["title"]
    # 含子节 §2.1（预估利润卡 GMV ≠ 经营概览 GMV）
    assert "预估利润" in r["content"]
    assert len(r["content"]) > 50


def test_get_rules_no_section_returns_toc():
    r = business_rules.get_rules()
    assert r["available"] is True
    assert "toc" in r and len(r["toc"]) >= 7
    assert "intro" in r


def test_get_rules_unknown_section_falls_back_to_toc():
    # 未命中不报错，落到目录让模型改选
    r = business_rules.get_rules("99")
    assert r["available"] is True
    assert "toc" in r


def test_get_rules_missing_doc_degrades(monkeypatch, tmp_path):
    # 文档缺失 → available=False，不抛异常
    monkeypatch.setattr(business_rules, "_DOC_PATH", tmp_path / "nope.md")
    monkeypatch.setattr(business_rules, "_cache", None)
    r = business_rules.get_rules("1")
    assert r["available"] is False


def test_get_rules_carries_audience_note():
    # 受众提示随内容下发：提醒 AI 滤掉技术细节、用老板能懂的话转述
    r = business_rules.get_rules("2")
    assert "audience_note" in r
    assert "老板" in r["audience_note"] or "运营" in r["audience_note"]
    # 目录路径也带
    assert "audience_note" in business_rules.get_rules()


def test_tool_registered():
    assert "ops_business_rules" in TOOL_NAMES


def test_run_tool_business_rules_no_scope_needed(monkeypatch):
    # 该工具纯文档检索，提前返回，不应触碰 resolve_authorized_scope
    def _boom(_p):
        raise AssertionError("ops_business_rules 不应解析 scope")

    monkeypatch.setattr("web.agent_tools.resolve_authorized_scope", _boom)
    out = run_tool("ops_business_rules", {"section": "6"}, PERM)
    assert out["available"] is True
    assert out["section"]["id"] == "6"
    # §6 = 广告 / ROAS 口径
    assert "ROAS" in out["section"]["title"] or "广告" in out["section"]["title"]


def test_run_tool_business_rules_empty_args(monkeypatch):
    monkeypatch.setattr("web.agent_tools.resolve_authorized_scope",
                        lambda _p: (_ for _ in ()).throw(AssertionError("不应解析 scope")))
    out = run_tool("ops_business_rules", {}, PERM)
    assert out["available"] is True
    assert "toc" in out
