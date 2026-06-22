# 业务规则与数据口径

> 经营报告 / 数据同步 / 告警 的**业务口径基线**，方便回溯"这个数字为什么这么算"。
> 多数规则是踩坑后定下来的，每条尽量附**为什么**。改口径前先读本文，改完回写本文。
>
> 平台：TikTok Shop · 市场：印尼（IDR）· 服务器 `yamk`（`ssh hp`）

---

## 1. 时区与时间口径

| 项 | 口径 | 说明 |
|----|------|------|
| 业务时区 | **印尼 = UTC+7** | 老板视角的"今天/昨天"一律按印尼自然日 |
| MySQL 存储 | **UTC（naive）** | 库里 `create_time` / `paid_time` 等都是 UTC 裸时间，无 tzinfo |
| 印尼自然日 → UTC 区间 | `[当日-1 17:00, 当日 17:00)` UTC | 例：印尼 6/20 全天 = UTC `[06-19 17:00, 06-20 17:00)` |
| `utcnow()` 截断点 | 即"印尼此刻" | intraday 报告用它做"截至此刻"的 cutoff |

**坑**：`paid_time` 是 naive UTC，比较时不要带 tzinfo 直接比；展示给老板要 +7 转印尼。

---

## 2. GMV 口径

- **GMV = 已付款订单的买家实付总额**（`orders.total_amount` = payment.total_amount），**含运费 / 税 / 优惠，非平台结算口径**。
- 只统计已付款（`paid_time` 非空 / 对应状态）的订单。
- 报告里 GMV 缩写展示（`Rp xxK/M/B`）只在前端展示层，后端返回原值。

---

## 3. 订单同步（增量）

代码：`flows/sync_orders.py`

- **增量策略**：按订单 `create_time` 窗口拉取。游标 `sync_cursors.window_end` 记录上次窗口结束；下次从 `window_end - 1h`（overlap 缓冲防边界漏单）拉到 `now`。
- **幂等**：重复窗口靠 `order_id` / `line_item_id` upsert，不产生重复。
- **调度**：systemd user timer，每小时 `xx:23` 跑（详见 `docs/proactive-push-ops.md`）。
- **出口 IP**：必须直连命中 TikTok 白名单（hp 出口 `220.198.249.121`）。

### ⚠️ `orders.synced_at` 语义坑（2026-06-20 纠错）

`orders.synced_at = Column(DateTime, server_default=func.now(), onupdate=func.now())`
→ 它是**"这一行最后一次被 UPDATE 的时间"，不是首次入库时间**。订单首次写入后，待发货快照 replace / 复跑 upsert 等任何 touch 都会把它刷新到当前时刻。

- **不要**用 `synced_at` 判断同步延迟（曾据此误判"07:59 的单 12 小时才入库、白天没抓到"，实际它在 UTC 01:23 / 印尼 08:23 就抓到了，延迟仅 ~24 分）。
- **判首次入库 / 同步延迟的正确方法**：查 `raw_api_responses`（`resource='orders'`）——对照每条的 `request_body.create_time_ge/lt` 窗口 + `response_payload.pages[].orders[]` 是否含目标 `order_id` + `fetched_at`（MySQL=UTC）。每小时一条、窗口连续 overlap 1h，是判"哪次抓到/有没有断档"的铁证。

### 商品 / 库存同步口径：只入库 ACTIVATE（2026-06-22 上线）

代码：`flows/sync_inventory.py`、`platforms/tiktok_shop/client.py`、`services/{product,inventory}_store.py`

- **在售口径 = 仅 `ACTIVATE`**：`products`/`inventory` 业务表只保留在售商品。草稿（`DRAFT`）、下架（`SELLER_DEACTIVATED` / `PLATFORM_DEACTIVATED`）、冻结、待审、已删等一律不进表（否则虚增商品数、压低动销率分母、误触断货告警）。需要审计非在售商品时查 `raw_api_responses`。
- **源头过滤**：`products/search` 请求体传 `{"status": "ACTIVATE"}`（`client.iter_products` 默认值；传 `status=None` 才拉全量，仅排查用）。TikTok 后台默认也不显示草稿，故"后台商品数"应与表内 `ACTIVATE` 数一致。
- **清退（prune）防僵尸**：只过滤不删会留"僵尸"——商品下架后 API 不再返回，旧行会永远停在 `ACTIVATE`+旧库存。故每次 sync 在 upsert 后清退本次未返回的旧行：`prune_products_not_in`（按 product_id 集合）、`prune_inventory_not_in`（按 SKU `idempotency_key` 集合，连带清退活跃商品被删的旧变体）。
- **零数据护栏**：本次返回为空（API 异常）时 **跳过 prune**，绝不清空整店（对齐报告侧零数据/低单量护栏思路）。
- **多租户安全**：prune 删除显式带全 scope 列（platform/account_id/shop_id/seller_id/country）锁定"本店"，不依赖 ORM 自动隔离兜底。

---

## 4. 经营报告口径（重点）

代码：`web/routes/report.py`

### 4.1 版型（按时间窗自动判定）

| 时间窗 | 版型 | 趋势 | 环比基准 |
|--------|------|------|----------|
| 单日 = 今天 | 日报 | 近 7 天迷你背景图 | **近 7 天同期均值** |
| 单日 = 过去某天 | 日报 | 近 7 天迷你背景图 | 较前一日（整天） |
| 多日区间 | 区间报 | 完整趋势 | 较上期（紧邻等长窗口） |

### 4.2 环比基准：当日用"同期对比"，不用"半天比全天"

- **当日（数据不全）**：环比走 **"截至此刻 vs 近 7 天同期均值（每天截至同一时刻）"**。
  - 早期版本是"今日截至 now vs 昨日同一时刻"；后改为**近 7 天同期均值**，以摊平昨日爆单等单日异常（单看昨天会被爆单日带偏）。
  - **绝不**用"今日半天 vs 昨日全天"，否则必出假暴跌。
- **过去某天 / 区间**：整天对整天 / 紧邻等长窗口对比。
- intraday 报告必须标注"数据截至 HH:MM（印尼时间）"，因为订单每小时才同步一次。

### 4.3 ⚠️ 低单量护栏（2026-06-20 上线）

**问题**：单量个位数时，环比百分比 = 除以一个接近 0 的小基准 / 小样本，被放大成噪声。
例：今日 1 单 vs 近 7 天同期均值 0.3 单 → `↑250%`；GMV Rp 79K vs 被昨日爆单污染的均值 Rp 50K → `↑57.7%`。这些百分比对老板是误导。

**规则**：当 **当期或基准单量为个位数**（`cur_orders < 10 or prev_orders < 10`）时触发护栏：

1. **GMV / 订单 KPI 卡片**：不显示环比百分比（`change=None`），改显示**绝对基准对比** ——
   `GMV: Rp 79K · vs 近 7 天同期均值 Rp 50K`、`订单数: 1 · vs 近 7 天同期均值 0.3 单`。
2. **AI 洞察**：`change=None` 会让喂给 LLM 的「环比%」自动变空，AI 拿不到噪声值、不会复述成"增长 X%"；同时 prompt 里带"低单量提示"，禁止用骤降/暴跌措辞、禁止当严重问题，改陈述绝对值。
3. ROAS / 广告不在此护栏内（广告另有"=0 是未接通"逻辑）。

> 设计原则（老板视角）：**结论先行、问题导向、不误导**。宁可隐藏不可靠的百分比，也不把噪声摆在老板面前。

### 4.4 Top5 爆款 / 断货预警

- **Top5 占比** = 单品 GMV / 当期总 GMV（商品行口径近似，guard 除零）。
- **断货风险计数（KPI）**：统一走**销速模型**（库存 ÷ 日均销速 = 可售天数），只算真实风险桶（断货 + 告急 + 预警）。
  - **不用** overview 的静态"库存<10"计数——后者含卖不动的滞销死货，会和"按可售天数"的口径自相矛盾。

#### ⚠️ 断货预警「两套口径」（2026-06-21 上线）

销速模型会把**近 N 天零销量的 SKU 整个排除**（`if units == 0: continue`），初衷是"只盯卖得动快断货的、不被滞销死货打扰"。但**低销量店铺**几乎所有 SKU 零销量 → 断货预警表常年空。根因是**零销量 SKU 不参与判断**，**不是阈值**（调 `critical_days`/`warning_days` 对零销量 SKU 无效）。故拆成两套口径，由 `get_stock_risk(include_all=...)` 控制（`services/stock_metrics.py`）：

- **监控告警**（`include_all=False`，默认）：保持原口径——只列**有销量且落风险桶**的 SKU，按可售天数升序。半夜推送不被滞销货刷屏。
- **报告展示**（`include_all=True`，日报/周报的断货预警卡）：列**全部在库 SKU**，按可售天数升序——断货（库存 0）最前 → 告急/预警 → 充足（`ok`）→ **近期无销量（`idle`）排末尾**，`idle` 段内按库存升序（让低库存的先冒头）。可售天数对 `idle` 为 `None`（展示"—"）。
- **不变量**：无论哪种口径，`buckets` 计数恒为真实风险桶（与告警一致），"断货风险数"KPI 不被"充足/无销量"灌水。

### 4.5 AI 洞察

代码：`web/routes/report.py` 的 `_INSIGHT_*`

- 渲染时服务端调 LLM、前端渐进加载；**当天缓存**（`_INSIGHT_CACHE`，进程内内存，key 含 `business_today()`）；优雅降级，绝不阻塞主报告。
- 文案末尾标注"🤖 由 <模型> 总结，仅供参考"。
- **改 prompt / 口径后**：缓存是进程内内存，**部署重启服务即自动清空**，无需手动清。

---

## 5. 发货 SLA 字段

- 待发货分桶以 **`tts_sla_time`** 为准（后台主考核线，两次核对一致）。
- `rts`（ready-to-ship）早几小时、不考核；`shipping_due`=发货取消线、`collection_due`=揽收取消线（先发后揽、顺序稳定）。
- 后台只显示就近未过的取消线。
- 待发货走**快照表** `pending_fulfillments`（每次同步全量覆盖，天然无"幽灵单"），与 `orders` 增量表解耦。

---

## 6. 广告 / ROAS 口径

- **广告消耗 = 结算口径**，含 GMV Max / TAP / 联盟三项拆分（在 TikTok Shop Finance API 结算费用拆项里，如 `gmv_max_ad_fee_amount`）。
- **广告消耗 = 0 通常是数据尚未接通**（缺 Finance 授权 scope），**不是"没投广告"**——报告 / AI 不要当作问题、不要建议投放。
- 接通前提：Partner Center 加 Finance 权限 + 店铺重授权，再写 finance sync + ROAS + 预警。

---

## 7. 告警规则

详见 `docs/proactive-push-ops.md`。两条确定性规则同一 scan flow：
1. **待发货超时**（按 `tts_sla_time`）。
2. **低库存 / 断货**（按可售天数 = 销速模型）。

走 Data Hub 确定性判定 + `openclaw message send` 直投（0-LLM），与日报（过 LLM）分离。

> 低库存告警用**告警口径**（`get_stock_risk` 默认 `include_all=False`，只推卖得动快断货的）；
> 日报/周报里的断货预警卡用**展示口径**（`include_all=True`，全量按可售天数升序）。详见 §4.4。

---

## 变更记录

| 日期 | 变更 | 文件 |
|------|------|------|
| 2026-06-20 | 低单量护栏：KPI 卡片隐藏噪声环比%、改示绝对基准对比；AI 同步不复述噪声 | `web/routes/report.py` |
| 2026-06-20 | 记录 `orders.synced_at` 是 onupdate 语义、判延迟须查 `raw_api_responses` | 本文 §3 |
| 2026-06-20 | 当日环比基准改"近 7 天同期均值"（摊平爆单日偏差） | `web/routes/report.py` |
| 2026-06-21 | 经营周报 `weekly_review` 上线（商品健康度视角 + 两种触发：定时 last_week 整周 / 实时 this_week intraday 周对周） | `web/routes/report.py`、`services/order_metrics.py` |
| 2026-06-21 | 断货预警拆「两套口径」：告警仍销速模型；报告展示 `include_all=True` 全量按可售天数升序、无销量排末尾（见 §4.4） | `services/stock_metrics.py`、`web/routes/report.py` |
| 2026-06-21 | 报告链接 TTL 默认延长到 7 天（纯链接推送场景），有效期文案按天/小时/分钟显示 | `.env`、`web/routes/data.py` |
| 2026-06-22 | 商品/库存同步只入库 `ACTIVATE`（源头 status 过滤 + prune 清退非在售，防草稿/僵尸污染），见 §3 | `flows/sync_inventory.py`、`platforms/tiktok_shop/client.py`、`services/{product,inventory}_store.py` |
