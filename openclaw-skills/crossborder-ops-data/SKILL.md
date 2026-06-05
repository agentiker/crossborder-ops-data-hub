---
name: crossborder-ops-data
description: "查询跨境电商经营概览、库存、利润、GMV、订单销量和告警等只读运营数据；通过本机 Data Hub HTTP API 获取结果。"
version: 1.0.0
user-invocable: true
metadata:
  openclaw:
    requires:
      env:
        - DATA_HUB_URL
        - DATA_HUB_TOKEN
      tools:
        - http_get
---

# 跨境电商运营数据查询

## 触发时机

当用户询问跨境电商运营数据、经营概览、库存、低库存、缺货、利润、毛利、GMV、订单数、销量、广告花费、利润率、运营告警或风险提示时，使用此 Skill。

当用户要求修改订单、商品、库存、价格、广告、告警状态，或要求查看原始 API payload、数据库凭据、平台 token、买家身份信息时，不使用本 Skill 执行写操作或敏感查询；应说明当前 Skill 只支持只读经营指标查询。

## 前置检查

执行前确认以下环境可用：

- `DATA_HUB_URL`: Data Hub 本机 HTTP 地址，通常为 `http://127.0.0.1:8000`。
- `DATA_HUB_TOKEN`: Data Hub 内部只读接口 token。
- `http_get`: openclaw 可用的 HTTP GET 工具。

如果缺少环境变量或 HTTP 工具不可用，直接告诉用户“数据查询配置不可用”，不要猜测业务数据。

## 请求规则

所有请求必须满足：

- 只调用 `GET {{DATA_HUB_URL}}/api/data/*`。
- 请求头必须携带 `X-Internal-Token: {{DATA_HUB_TOKEN}}`。
- 不得在回复、日志摘要或错误解释中暴露 `DATA_HUB_TOKEN`。
- 用户指定平台、国家或店铺时，透传为查询参数：
  - `platform`: 平台标识，如 `tiktok_shop`、`shopee`、`amazon`。
  - `country`: 国家或地区，如 `ID`、`GLOBAL`。
  - `shop_id`: 店铺 ID。
- 日期参数使用 `YYYY-MM-DD`。如果用户使用“今天、昨天、最近 7 天”等相对日期，先换算为明确日期后再请求。

## 意图路由

经营概览、总体表现、老板日报、今天/最近整体情况：

```bash
GET {{DATA_HUB_URL}}/api/data/overview
Header: X-Internal-Token: {{DATA_HUB_TOKEN}}
Query: platform?, country?, shop_id?
```

库存、低库存、缺货、SKU 库存、仓库库存：

```bash
GET {{DATA_HUB_URL}}/api/data/inventory
Header: X-Internal-Token: {{DATA_HUB_TOKEN}}
Query: platform?, country?, shop_id?, low_stock_threshold?
```

利润、GMV、毛利、广告花费、订单数、销量、利润率：

```bash
GET {{DATA_HUB_URL}}/api/data/profit/summary
Header: X-Internal-Token: {{DATA_HUB_TOKEN}}
Query: start_date?, end_date?, platform?, country?, shop_id?
```

告警、异常、风险、需要关注的问题：

```bash
GET {{DATA_HUB_URL}}/api/data/alerts
Header: X-Internal-Token: {{DATA_HUB_TOKEN}}
Query: platform?, country?, shop_id?, limit?
```

如果用户的问题横跨多个主题，例如“今天整体怎么样，有没有库存风险”，先查 `/overview`，必要时再查 `/inventory` 或 `/alerts` 补充明细。

## 结果解释

- 以 HTTP API 返回值为唯一事实来源。
- 不得自行重算利润、ROI、退款率、库存覆盖等核心指标。
- 可以做展示层处理，例如排序、筛选、截断长表格、四舍五入显示、把 `profit_margin` 加 `%`。
- 如果用户询问接口未返回的指标，明确说明“当前数据接口暂未提供该指标”，并给出已返回的相关指标。
- 如果结果为空，说明当前筛选条件下暂无数据，不要臆测原因。
- 对库存问题，优先展示 `low_stock_items`，再展示总体 SKU 数和库存明细。
- 对告警问题，优先展示 `severity=critical` 或更高风险的告警，再展示普通告警。
- 对利润问题，必须展示统计周期 `start_date` 到 `end_date`。

## 分析职责

调用数据接口后，Agent 必须把结果组织为运营分析，而不是只复述 JSON。回答应覆盖：

1. **事实摘要**：用接口返回值说明当前经营状态，例如 GMV、毛利、订单数、销量、库存、低库存 SKU、告警数量。
2. **异常识别**：
   - `/api/data/alerts` 或 `/api/data/overview.alerts` 返回的内容称为“正式告警”。
   - Agent 可基于多接口数据提出“观察到的风险”或“疑似异常”，例如库存低但销量高、GMV 有表现但毛利偏弱、告警数量上升等。
   - AI 推断的异常必须明确标注为“基于当前数据观察”，不得称为系统已生成告警。
3. **原因解释**：只能基于已返回字段解释可能原因；如果缺少订单明细、流量、广告 ROI、退款率等必要数据，必须说明当前接口不足以确认原因。
4. **决策建议**：主动给出下一步运营动作建议，按优先级排序。建议应具体、可执行，但不得承诺已执行任何业务操作。
5. **置信边界**：当数据为空、字段缺失、时间范围过短或接口未提供关键指标时，必须降低语气并说明限制。

## 回答结构

默认按以下结构输出：

### 结论

用 1-3 句话说明最重要的经营判断。

### 关键事实

用表格或短列表列出接口返回的核心数据，必须包含统计周期或筛选条件。

### 异常与风险

先列正式告警，再列 AI 基于数据观察到的疑似风险。两者必须区分。

### 建议动作

给出 1-5 条运营建议，按优先级排序。每条建议应包含：

- 建议动作
- 数据依据
- 预期目的
- 需要人工确认的信息，如适用

### 数据限制

如存在接口缺失、无数据、时间范围不足或无法确认原因，简短说明。

## 分析约束

- 不得编造接口未返回的数据。
- 不得自行计算核心公式，包括利润、ROI、退款率、库存覆盖等；这些指标必须由服务端返回后才能引用。
- 可以做非核心展示层处理，例如排序、筛选、分组、截断长列表、金额格式化。
- 对 AI 推断内容必须使用“可能、建议关注、基于当前数据观察”等措辞。
- 对正式告警必须引用接口中的 `severity`、`alert_type`、`title` 或 `message`。
- 不得输出买家 PII、token、数据库凭据、原始 API payload。
- 不得执行或声称执行补货、调价、投放、下架、改库存等写操作。

## 异常处理

- 401：说明内部数据接口鉴权失败，需要检查 Data Hub token 配置。
- 503：说明服务端内部 token 未配置或数据服务暂不可用。
- 404：说明请求的指标接口不存在，可能是 Skill 文档与服务端版本不一致。
- 连接失败或超时：说明本机 Data Hub 服务不可达，需要检查服务是否启动及 `DATA_HUB_URL`。
- 返回字段缺失：说明接口契约与 Skill 不一致，不要补造字段。

## 输出与安全约束

- 默认使用简洁中文回答，先给结论，再给关键数据。
- 明细数据优先用 Markdown 表格；长列表默认展示最重要的 10 条，并说明总数。
- 金额保留 2 位小数；库存、订单数、销量展示为整数。
- 严禁暴露任何买家的手机号、姓名、完整收货地址等个人可识别信息（PII）。如果未来接口返回此类字段，必须先脱敏。
- 严禁暴露 `DATA_HUB_TOKEN`、平台 token、数据库地址、数据库账号密码或原始 API payload。
- 不要承诺已经修改任何业务数据。本 Skill 只做只读查询和解释。

## 参考契约

接口字段与响应示例见 `references/api-contract.md`。当服务端 `web/routes/data.py` 变更时，必须同步更新该契约。
