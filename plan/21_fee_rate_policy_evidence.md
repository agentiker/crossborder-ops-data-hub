# plan/21 — 费率告警参考依据增强

> status: active  
> 2026-07-18 定。目标：在现有“及时费率/扣点率异常”告警触发后，给飞书老板消息补充可信依据：先证明“官方费用项确实上涨”，再尽力匹配 TikTok 官方公开政策/学院文章。外部依据只增强置信度，不参与核心告警判定。

## 背景

现有费率监控已经完成两条链路：

- 看板：`frontend/src/pages/BoardPage.tsx` 消费 `/board/data` 的 `fee_rate`，展示当前预估费率、历史基准、趋势和分项归因。
- 告警：`flows/scan_fulfillment_alerts.py` 中 `_scan_fee_rate` / `_scan_unsettled_fee_rate` 复用 `services/fee_rate_metrics.py` 与 `services/fee_rate_alerts.py`，通过飞书卡片直投。

prod 现状：`data-sync-unsettled-fees.timer` 正常跑，`data-scan-alerts.timer` 当前未启用。7 月 15 日手动跑过一次，及时费率规则可执行，但未达异常阈值。

用户新需求：当系统发现费率上升并提醒老板时，希望自动找 TikTok 大学文章/通知等官方依据，一并发给老板，让告警更可信。

## 核心决策

### 证据分级

**A级：内部官方费用证据（必做）**

来自已授权 TikTok Finance API 和本地落库：

- `finance/202507/orders/unsettled`：未结算预估费用，能提前看到官方预估的 `est_fee_tax_amount` 与 `fee_tax_breakdown`。
- `finance/202501/.../statement_transactions`：结算后的 SKU/订单级费用明细，包含 `dynamic_commission_amount` 等费项。

这类证据用于证明“我们看到 TikTok 官方费用字段变高”，可信度最高，也是飞书文案的主证据。

**B级：TikTok 官方公开政策/帮助文章（增强）**

来自公开网页搜索/抓取，例如 TikTok Shop Academy Indonesia / Policy Center / TikTok Business Help。仅在国家、费项关键词、发布日期或内容语义有匹配时附上，文案表述为“参考资料/可能相关依据”，不写成“已确认调佣原因”。

**C级：无匹配**

外部搜索失败、无强匹配、网页不可访问时，不影响告警发送。卡片可只展示 A 级内部证据，或补一句“未匹配到近期官方公开公告”。

### API 能力判断

本地 `docs/tiktok-shop-openapi-index.json` / `docs/tiktok-shop-openapi-reference.md` 未看到公告、通知、政策中心、TikTok 大学文章类 OpenAPI。`event` 只有 webhook 配置与业务事件，`seller` 只有店铺/权限。因此不要把“政策公告检索”设计成 TikTok OpenAPI 调用；OpenAPI 只承担内部费用事实证据。

## 落地方案

### W1. 结构化内部证据

改造 `services/fee_rate_alerts.py` 的判定输出，或新增轻量结构：

```python
{
  "evidence": {
    "source": "tiktok_finance_api",
    "confidence": "high",
    "currency": "IDR",
    "eval_window": "7/15~7/17",
    "baseline_window": "6/6~7/5",
    "fee_items": [
      {
        "key": "dynamic_commission_amount",
        "name": "动态佣金",
        "from": 0.081,
        "to": 0.112,
        "delta": 0.031,
        "basis": "attribution"
      }
    ]
  }
}
```

原则：

- 不改变 `should_alert` 判定条件。
- 复用现有 components / attributions 逻辑，避免重新算一套口径。
- 没有交集归因时，降级展示当前主要费用构成，不强行解释为涨幅来源。

### W2. 官方公开资料检索抽象

新增 `services/tiktok_policy_evidence.py`，职责只做“输入告警上下文，真实搜索官方公开网页并输出候选公开依据”，不碰告警判定。

输入：

- country / market：当前先支持 `ID`。
- fee item keys：如 `dynamic_commission_amount`、`affiliate_ads_commission_amount`、`gmv_max_ad_fee_amount`。
- alert window end date。
- language preference：优先 `id-ID`，可回退 `en`。

输出：

```python
[
  {
    "title": "...",
    "url": "...",
    "source": "TikTok Shop Academy",
    "published_at": "2026-07-01",
    "matched_terms": ["commission", "dynamic commission"],
    "confidence": "medium",
    "summary": "..."
  }
]
```

实现边界：

- 第一版用搜索引擎 HTML 结果做实时检索；真实网络失败时返回空。
- 不使用固定死的资料池作为默认来源；单测可注入候选资料保持离线。
- 只允许官方域名白名单：`seller-id.tiktok.com`、`seller-*.tiktok.com`、`ads.tiktok.com`、`newsroom.tiktok.com`。
- 结果最多 2 条，防止飞书消息过长。
- 网络请求要有超时；错误只打日志，不阻塞告警。

### W3. 缓存与去重

新增缓存表或轻量 JSON 缓存，建议表名 `policy_evidence_cache`：

- `query_key`：country + fee_keys + month。
- `source_url` / `title` / `published_at` / `summary` / `confidence`。
- `fetched_at` / `expires_at`。

第一版先不建缓存表，因为搜索只在“告警触发并通过同窗口去重后”执行；同一范围同一评估窗口最多发送一次，不会每 30 分钟反复搜索。若后续收件人/范围增多，再加 `policy_evidence_cache`。

### W4. 飞书卡片与纯文本 fallback

改 `web/alert_card_builder.py::build_fee_rate_card`：

- 增加“检测依据”：展示内部费用证据，重点说清哪个官方费用项涨了。
- 增加“参考资料”：展示最多 2 条官方链接。
- 没有外部资料时，不显示空区块。

改 `services/fee_rate_alerts.py` 文本文案：

- openclaw 纯文本 fallback 也能带内部证据。
- 飞书私聊不使用表格，保持短列表。

### W5. prod 启用顺序

1. 本地单测通过。
2. 在 prod 手动 dry-run：`uv run python -m flows.scan_fulfillment_alerts --dry-run`。
3. 检查飞书卡片 JSON 预览或日志，不实发。
4. 用户确认后启用：`systemctl --user enable --now data-scan-alerts.timer`。
5. 观察一到两轮：`journalctl --user -u data-scan-alerts -n 80 --no-pager`。

## 测试计划

- `tests/test_fee_rate_alerts.py`：告警触发时 evidence 字段完整；无归因时降级合理；不触发时不产生误导性 evidence。
- `tests/test_alert_card_builder.py`：费率卡展示检测依据、参考资料；无参考资料时不出现空标题。
- 新增 `tests/test_tiktok_policy_evidence.py`：白名单域名、关键词评分、真实搜索入口可替换、网络失败返回空、泛 GMV Max 概念页过滤。
- 可选：`tests/test_fee_rate_monitor.py` 不需要改，除非看板也展示证据。

交付前跑 `uv run pytest`。如果真实网络搜索需要集成测试，必须单独标记，默认测试套件保持离线。

## 本轮不做

- 不让外部文章决定是否告警。
- 不抓取 Seller Center 登录后通知。
- 不接 RPA 登录 TikTok 后台。
- 不做类目级独立触发。
- 不把 LLM 放进确定性告警判定链路。

## 验收

当费率异常触发时，老板收到的飞书消息应包含：

- 费率升高事实：当前窗口、历史基准、升幅。
- 内部检测依据：具体 TikTok 官方费用项和占比变化，不展示程序字段名。
- 如有匹配：1-2 条 TikTok 官方公开参考链接。
- 如无匹配：告警仍正常发送，不编造政策原因。

prod 启用前，必须先 dry-run 给用户确认文案。
