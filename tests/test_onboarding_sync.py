"""onboarding 文案双源同步校验。

文案有两份且必须逐字一致：
- 权威源：openclaw-plugins/crossborder-onboarding/index.js 的 ONBOARDING_ZH
  （/start 确定性命令直出，绕开 LLM）。
- 兜底副本：openclaw-skills/crossborder-ops-data/SKILL.md 的
  ===ONBOARDING_BEGIN===...===ONBOARDING_END=== 之间（用户手打裸词时 LLM 复述）。

手工同步迟早漂移，这里把"改一处要同步另一处"的约定变成自动校验。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_JS = REPO_ROOT / "openclaw-plugins" / "crossborder-onboarding" / "index.js"
SKILL_MD = REPO_ROOT / "openclaw-skills" / "crossborder-ops-data" / "SKILL.md"


def _extract_plugin_text() -> str:
    """取 index.js 里 const ONBOARDING_ZH = `...`; 模板字符串的内容。"""
    src = PLUGIN_JS.read_text(encoding="utf-8")
    m = re.search(r"const ONBOARDING_ZH = `(.*?)`;", src, re.DOTALL)
    assert m, "index.js 未找到 ONBOARDING_ZH 模板字符串（常量被改名/改写法？）"
    return m.group(1)


def _extract_skill_text() -> str:
    """取 SKILL.md 里 ===ONBOARDING_BEGIN=== 与 ===ONBOARDING_END=== 之间的内容。"""
    src = SKILL_MD.read_text(encoding="utf-8")
    m = re.search(
        r"===ONBOARDING_BEGIN===\n(.*?)\n===ONBOARDING_END===", src, re.DOTALL
    )
    assert m, "SKILL.md 未找到 ONBOARDING_BEGIN/END 标记块（边界标记被改？）"
    return m.group(1)


def test_onboarding_text_matches_between_plugin_and_skill():
    """两份文案逐字一致（含 emoji、**加粗**、中文书名号、空行）。"""
    plugin_text = _extract_plugin_text()
    skill_text = _extract_skill_text()
    assert plugin_text == skill_text, (
        "onboarding 文案漂移：index.js 的 ONBOARDING_ZH 与 SKILL.md 的 "
        "ONBOARDING_BEGIN/END 块不再逐字一致，改一处必须同步另一处。\n"
        f"--- index.js ---\n{plugin_text!r}\n--- SKILL.md ---\n{skill_text!r}"
    )


@pytest.mark.parametrize("extractor", [_extract_plugin_text, _extract_skill_text])
def test_onboarding_hard_constraints(extractor):
    """SKILL.md 的 onboarding 硬约束 1/2：首字符必须是 👋，末行固定。"""
    text = extractor()
    assert text.startswith("👋"), "onboarding 第一个字符必须是 👋（硬约束1）"
    last_line = text.rstrip("\n").splitlines()[-1]
    assert last_line == (
        "想重新开始发 /new 清空会话（不会清空聊天记录），再看一次指引发「/start」。"
    ), "onboarding 最后一行必须是固定的「/new 清空会话」句（硬约束2）"
