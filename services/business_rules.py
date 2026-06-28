"""业务规则知识库加载器（plan：看板「问 AI」入口）。

把仓库内 `docs/business-rules.md`（业务口径基线，§1 时区 … §7 告警）解析成
可按章节检索的文本，供 Web 对话的 `ops_business_rules` 工具调用——让 AI 回答
「为什么两个 GMV 不一致 / 结算滞后是什么 / 爆单阈值多少」这类**概念性口径问题**时，
有权威出处可引，而不是凭记忆编规则。

设计取舍：
- **检索粒度 = 二级标题 `##`**（编号 1–7 的大节），`###` 子节随所属大节一并返回。
  大节自带编号（"## 1. 时区与时间口径"），直接拿编号当稳定 id，模型好选、不易漂。
- 文档随仓库走、体量小（~30KB），**进程内读一次缓存**（仿 services/channel_metrics 的
  模块级缓存），按文件 mtime 失效以便本地改文档即时生效，无需重启。
- 文档缺失 / 解析空时**优雅降级**：返回带 available=False 的结构，调用方据此如实告知。
"""

from __future__ import annotations

import re
from pathlib import Path

# services/ 在仓库根下一层 → parents[1] = 仓库根（docs/ 与之同级）。
_DOC_PATH = Path(__file__).resolve().parents[1] / "docs" / "business-rules.md"

# 模块级缓存：(mtime, sections, intro)；mtime 变化即重解析（本地改文档免重启）。
_cache: tuple[float, list[dict], str] | None = None

# 随返回内容一起下发的受众提示：文档是给开发者写的，含表名/字段/代码/命令。提醒调用方
# （对话 AI）转述给老板/运营时只取业务口径、滤掉技术细节。即使对话历史被裁剪、只剩这条
# 工具结果，提示也跟着内容走（与 chat SYSTEM_PROMPT 的受众约束互为冗余）。
_AUDIENCE_NOTE = (
    "以下是给开发者看的内部文档，含数据库表名/字段名/代码路径/命令等技术细节。"
    "回答老板或运营时，只转述业务含义与口径，务必滤掉这些技术名词，用大白话表达。"
)

# 二级标题行：## <编号>. <标题>  或  ## <标题>（无编号时用 slug 兜底 id）
_H2 = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_NUM_PREFIX = re.compile(r"^(?P<num>\d+(?:\.\d+)*)\.?\s+(?P<rest>.+)$")


def _slugify(title: str) -> str:
    """无编号标题的兜底 id：取标题里的中英数字，截断成短 slug。"""
    s = re.sub(r"[^\w一-鿿]+", "-", title).strip("-")
    return s[:32] or "section"


def _parse(text: str) -> tuple[list[dict], str]:
    """切成 [{id,title,body}, ...] + 文档开头（标题前的引言）。

    以 `## ` 行为分界；分界前的内容（# 大标题 + 引言）作为 intro 返回，供「目录」用。
    每节 body 含其下所有 `###` 子节，原样保留 Markdown。
    """
    lines = text.splitlines()
    sections: list[dict] = []
    intro_lines: list[str] = []
    cur: dict | None = None
    seen_ids: set[str] = set()

    for line in lines:
        m = _H2.match(line)
        if m:
            if cur is not None:
                cur["body"] = "\n".join(cur["_buf"]).strip()
                del cur["_buf"]
                sections.append(cur)
            title = m.group("title")
            nm = _NUM_PREFIX.match(title)
            sid = nm.group("num") if nm else _slugify(title)
            # 去重兜底（理论上编号唯一）
            base, n = sid, 2
            while sid in seen_ids:
                sid = f"{base}-{n}"
                n += 1
            seen_ids.add(sid)
            cur = {"id": sid, "title": title, "_buf": []}
        elif cur is not None:
            cur["_buf"].append(line)
        else:
            intro_lines.append(line)

    if cur is not None:
        cur["body"] = "\n".join(cur["_buf"]).strip()
        del cur["_buf"]
        sections.append(cur)

    return sections, "\n".join(intro_lines).strip()


def _load() -> tuple[list[dict], str]:
    """读+解析文档，按 mtime 缓存。文档缺失返回空。"""
    global _cache
    try:
        mtime = _DOC_PATH.stat().st_mtime
    except OSError:
        return [], ""
    if _cache is not None and _cache[0] == mtime:
        return _cache[1], _cache[2]
    text = _DOC_PATH.read_text(encoding="utf-8")
    sections, intro = _parse(text)
    _cache = (mtime, sections, intro)
    return sections, intro


def list_sections() -> list[dict]:
    """章节目录：[{id, title}, ...]，供工具 schema 描述 + 「不传 section」时返回。"""
    sections, _ = _load()
    return [{"id": s["id"], "title": s["title"]} for s in sections]


def get_section_ids() -> list[str]:
    """所有章节 id（供工具 spec 的 enum 用）。"""
    return [s["id"] for s in list_sections()]


def get_rules(section: str | None = None) -> dict:
    """取业务规则文本。

    - section 指定且命中 → 返回该节全文（含子节）。
    - section 为空 / 未命中 → 返回引言 + 章节目录（让模型据 title 二次选节或直接概览）。
    - 文档缺失 → available=False，调用方如实告知「暂无业务规则文档」。
    """
    sections, intro = _load()
    if not sections:
        return {"available": False, "note": "业务规则文档暂不可用"}

    if section:
        for s in sections:
            if s["id"] == section:
                return {
                    "available": True,
                    "section": {"id": s["id"], "title": s["title"]},
                    "content": s["body"],
                    "audience_note": _AUDIENCE_NOTE,
                }
        # 未命中：落到目录，不报错（模型可据 toc 改选）
    return {
        "available": True,
        "intro": intro,
        "toc": [{"id": s["id"], "title": s["title"]} for s in sections],
        "note": "未指定或未匹配章节，返回引言与目录；如需某节细则，按 toc 的 id 再次调用并传 section。",
        "audience_note": _AUDIENCE_NOTE,
    }
