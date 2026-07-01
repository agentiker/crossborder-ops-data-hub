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

- **展示 GMV = 下单订单的商品小计**（`orders.sub_total` = payment.sub_total，**不含运费/税/优惠**），按 `create_time` 归日、**含所有订单状态（不排除 CANCELLED）**、含货到付款（COD）在途单。与 **TikTok 后台 GMV 精确一致**（实测 6/29：我们 195.8M vs 后台 196.2M，差 -0.2%；6/30：178.2M vs 178.8M，差 -0.3%）。
  - **为什么这样定（2026-07-01 两轮对账钉死）**：
    1. 第一轮：本店（COD 主导店）约 **77% 是 COD 单**，下单时 `paid_time` 为空、收货才回填。旧的**付款口径**（`paid_time` 非空归日）会把当天 COD 单漏到几周后 → 日报 GMV 只有后台 ~1/5。改成 `create_time` 下单口径后量级对上。
    2. 第二轮：客户反馈「后台 GMV 不会减少」→ 直打 `orders/search` 多口径反推确认，后台 GMV 用的是 **`sub_total`（商品小计）+ 含所有状态（不扣取消）**，不是我们当时用的 `total_amount`（买家实付含运费税）+ 排除取消。两处口径错误（字段偏高 ~4% / 排除取消偏低）方向相反、部分抵消，净差看着像 2.5% 噪声，实为口径错。故改用 `sub_total` + 含取消，精确对齐。
- **只有 GMV 总额 + 趋势 + 订单数 + 销量**用展示口径。**爆款榜/新品/SKU 拆分口径不同**（行级 `sale_price` + 排除取消），前端 tooltip 已标注差异。
- 报告里 GMV 缩写展示（`Rp xxK/M/B`）只在前端展示层，后端返回原值。
- 代码开关：`services/order_metrics.py` 三套口径正交开关 `_time_filter(by_create, display)` / `_time_col(by_create, display)`——见 §2.2 表。展示端点（`data.py` overview/orders/trend、`board.py`、`report.py` 日报周报）钉 `display=True`。回填期老单 `sub_total` 为 NULL 时聚合用 `coalesce(sub_total, total_amount)` 兜底（GMV 平滑收敛不暴跌）。

### 2.1 「过去某天的 GMV 为什么还在变 / 后台为什么不减少」（2026-07-01 逐单对账钉死）

客户说「后台 GMV 不会减少」——**对的，因为后台按下单锁定商品小计、取消不回扣**。

- **含所有状态的下单 GMV 是稳定锚**：`create_time` 是历史事实，6/30 下单 1393 单、`sub_total` 合计 178.2M，今天打下周打都一样。直打 `orders/search` 与 prod 库**逐单、逐分钱 100% 一致**（`total_count`/差集/金额全等）——数据同步零丢失。
- **之前我们的数会「变小」的根源**是当时错误地**排除了 CANCELLED**：订单状态持续流转（未付款单挂到期被平台自动取消、买家取消），看得越晚累积取消越多、去 CANCELLED 后 GMV 越小，故显得「我们比后台少、还在降」。**改成含取消后这个问题消失**——和后台一样，取消单锁定不回扣。
- **退货**：`RETURN`/`REFUND` 收货后才发生，下单口径 GMV 本就不含退货（退货体现在结算/利润，不在 GMV），故不影响展示 GMV。
- 残差 ±0.3% = `sub_total` 反推的截图取整 + 秒级边界，属正常对账噪声。

### 2.2 三套 GMV 口径并存（正交开关，勿混用）

| 口径 | 用途 | 归日 | 取消单 | 金额列 | 开关 |
|---|---|---|---|---|---|
| **展示** | 日报/周报/看板 GMV 总额·趋势·订单数·销量 | `create_time` | **含** | **`sub_total`** | `display=True` |
| **利润** | 预估利润 GMV（与扣点同队列） | `create_time` | 排除 | `total_amount` | `by_create=True` |
| **ROAS/付款** | ROAS 分子 GMV | `paid_time` | （付款即非取消）| `total_amount` | 都 False（默认）|

- **ROAS 分子**（`services/ad_metrics.py`）保留**付款口径**：分母广告消耗是结算口径（按结算日），分子需同口径；改展示口径会把未付款 COD / 取消单算进分子 → ROAS 虚高。故 ROAS 卡 GMV **小于**经营概览 GMV，有意为之、非 bug。
- **利润卡 GMV**（`services/profit_summary.py:get_profit_card` ← `flows/aggregate_profit`，`by_create=True`）：`total_amount` + 排除取消。会**小于**展示 GMV（展示用 sub_total 且含取消），差异 = 运费税 −取消单，属口径差、非 bug。
- **覆盖天数护栏**：利润卡读预聚合表，若 `aggregate_profit` 漏跑某天会静默少算。`get_profit_card` 返回 `coverage_complete`；前端 false 时显横幅。根治靠 `aggregate_profit` timer 常态跑 + 回填（`uv run python -m flows.aggregate_profit --days 30`）。

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

#### 看板环比同款处理（2026-06-28 上线）

看板默认窗口结束在今天（`_resolve_window` 默认 `ed=business_today()`），原 `overview.change` 按"当期整窗 vs 上期整窗"算 → 当期含半天今天被拉低、显示**假暴跌**（窗口越短越夸张，单日 today 可达 −40%）。

- 修法：`web/routes/board.py:_overview_window_and_gmv`，**窗口结束=今天时** cur/prev 都用 `get_gmv_summary_intraday_range` 钉"截至此刻"（与日报同款），不含今日则整天对整天。差别全在上期被截到同一时刻（当期今天本就没有未来单，full 与 intraday 相等）。
- 后端下发顶层 `window{start,end,includes_today,as_of_label}`；前端经营概览标题显"数据截至 MM-DD HH:MM·今日为当日累计"徽章，利润卡加"今日为当日累计、次日凌晨定稿"提示（利润卡读 `fact_profit_daily` 快照、无法 intraday 切，故只提示不重算）。
- **广告/ROAS 环比不做 intraday**（结算口径 statement_time 滞后数日，今日近零、cur/prev 对称无假跌）。

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

#### 看板「爆款商品」卡：商品级聚合 + 单品渠道构成（2026-06-28 上线）

代码：`services/order_metrics.py:get_top_products`（榜单）、`services/product_channel_metrics.py`（渠道）、`web/routes/board.py`（`top` 直调 + `/board/product-detail` 懒加载端点，返渠道 `channels`粗+`fine`细 + 各 SKU 占比）。点击爆款行弹出详情弹窗（大图可点开看原图 / 渠道环图可切粗细 / SKU 明细默认收起）。

- **榜单按 `product_id` 聚合**（非 SKU）：客户「爆款**商品**」语义是商品级，且单品渠道拆分也按 product_id。带 `seller_sku`（款号，多 SKU 取一）/`sku_count`（>1 前端显「N 个规格」）/`main_image_url`（主图，LEFT JOIN `products`）。旧 `get_top_skus`（SKU 粒度）保留供 MCP/报告。
- **商品小图**：`get_product().main_images[0].thumb_urls[0]`（300×300，CDN 无防盗链可直接 `<img>`），随库存同步顺手入库到 `products.main_image_url`；取图偶发失败时**保留旧图不抹**（`product_store` 仅在新值非空时覆盖）。URL 带签名 query，靠每次同步刷新兜过期。
- **⚠️ 渠道「两根正交轴」口径**（客户原话「达人/自营素材/直播/商品卡」混了两轴）：
  - `直播/视频/商品卡` = **content_type**（销售内容形式，三者和=100%，店铺级见 §阶段5 `channel_metrics`）。
  - `达人/自营素材` = **account_type**（谁带货：affiliate 达人 / seller 自营，横切视频·直播，商品卡无达人概念）。
  - 单品级用 `GET /analytics/202605/shop_products/performance`（已交叉拆好 `affiliate/seller × live/video/product_card + shop_tab`）。**粗分 4**：达人(affiliate_total) / 自营素材(seller_live+seller_video) / 商品卡(seller_product_card) / 店铺页(shop_tab)；**细分**再把 达人→直播/视频/其它(残差)、自营→直播/视频 拆开（前端环图「粗分/细分」可切）。
  - **⚠️ 真打修正（2026-06-28 prod 真值，沙箱/hp 店全 0 验不出）**：① 各块 GMV 字段名**不统一**——`affiliate_live`=`live_attributed_gmv`、`affiliate_video`=`attributed_video_gmv`、`shop_tab`=`shop_tab_gmv`，其余才是 `attributed_gmv`（原统一读 attributed_gmv 把 店铺页/达人直播/达人视频 恒读成 0）。② 渠道是**多触点归因可重叠**，4 渠道之和 ≠ total（实测 431M vs 389M），`affiliate_total` > live+video（差额=「达人其它」残差桶，保证细分∑=粗分）。故 donut 是**"渠道构成占比"**（各切片之和归一），**非真 GMV 分割**——别再写"4 分=总 GMV"。
  - 降级：沙箱/无 analytics、或近 ~3-5 天 analytics 滞后致短近窗口返空 → `available=False`，前端显「该商品暂无渠道数据」，不阻断卡片其它信息。
- **断货风险计数（KPI）**：统一走**销速模型**（库存 ÷ 日均销速 = 可售天数），只算真实风险桶（断货 + 告急 + 预警）。
  - **不用** overview 的静态"库存<10"计数——后者含卖不动的滞销死货，会和"按可售天数"的口径自相矛盾。

#### 看板「近 N 天新品」卡 + 爆单提醒（2026-06-28 上线）

代码：`services/order_metrics.py:get_new_product_trends`（卡片取数）、`get_new_product_ids`（告警标注用）、`web/routes/board.py:/board/new-products`（懒加载端点）、前端 `BoardPage.tsx:NewProducts`。

- **新品口径 = 近 N 天上线**：`Product.source_create_time` 落 `[as_of-(N-1), as_of]` 且 `status='ACTIVATE'`（在售口径，见 §3）。**N = `settings.new_product_lookback_days`（默认 60）**——看板卡、端点、爆单告警🌟标注三处共用同一配置（改一处全局一致；前端文案随端点 `window.lookback_days` 动态显示）。客户口头叫「本周新品」，但统计口径取「近一个月起、可配」，命名据实显天数。
- **只展示已起量的**：窗口内 `total_units>0` 才进卡（测款未起量的不刷屏）；按「爆单优先 → 总销量降序」排。
- **销量曲线**：下单口径（`create_time` 归日、排除 CANCELLED，含 COD 在途，见 §2）按印尼业务日（`to_business_day`）归日的每日 line_item 条数，与 `get_gmv_trend` 同口径。画布从「上线业务日」（或窗口起，取较晚者）连续补零到 as_of，诚实反映「上线即起跑」。
- **爆单判定**：曲线峰值单日销量 ≥ `settings.hotsell_daily_units_threshold`（默认 **50**），与飞书爆单告警**同阈同口径**。界面爆单徽章由端点确定性计算、**不依赖告警 timer**（看板打开即算）；飞书侧见 §7 规则 5 的新品标注。
- 降级：无 `Product` 数据 / 取数异常 → 端点 `available=False`，前端显「新品数据暂不可用」，不阻断看板其它卡。

#### ⚠️ 断货预警「两套口径」（2026-06-21 上线）

销速模型会把**近 N 天零销量的 SKU 整个排除**（`if units == 0: continue`），初衷是"只盯卖得动快断货的、不被滞销死货打扰"。但**低销量店铺**几乎所有 SKU 零销量 → 断货预警表常年空。根因是**零销量 SKU 不参与判断**，**不是阈值**（调 `critical_days`/`warning_days` 对零销量 SKU 无效）。故拆成两套口径，由 `get_stock_risk(include_all=...)` 控制（`services/stock_metrics.py`）：

- **监控告警**（`include_all=False`，默认）：保持原口径——只列**有销量且落风险桶**的 SKU，按可售天数升序。半夜推送不被滞销货刷屏。
- **报告展示**（`include_all=True`，日报/周报的断货预警卡）：列**全部在库 SKU**，按可售天数升序——断货（库存 0）最前 → 告急/预警 → 充足（`ok`）→ **近期无销量（`idle`）排末尾**，`idle` 段内按库存升序（让低库存的先冒头）。可售天数对 `idle` 为 `None`（展示"—"）。
- **不变量**：无论哪种口径，`buckets` 计数恒为真实风险桶（与告警一致），"断货风险数"KPI 不被"充足/无销量"灌水。

#### 看板「库存健康」卡：健康度口径 + 商品明细（2026-07-01 优化）

代码：`services/stock_metrics.py:get_stock_risk`（取数）、前端 `BoardPage.tsx:InventoryHealth`。

- **健康度定义**：`健康度 = 不缺货的在售商品占比 = (总在售 SKU − 风险 SKU) / 总在售 SKU`。「风险」= 断货（库存 0）+ 告急（可售 < `critical_days`，默认 3 天）+ 偏低（可售 < `warning_days`，默认 7 天）；近期无销量（`idle`）不参与风险判断。可售天数 = 当前库存 ÷ 日均销量（见上「销速模型」）。
- **日均销量（销售速度）窗口**：`日均销量 = 近 velocity_window_days 天的已付款销量 ÷ velocity_window_days`，`velocity_window_days = settings.stock_velocity_window_days`（**默认 7 天**，按印尼业务日、含今天）。前端健康度 tooltip + 「问 AI」链接均据实显示该窗口天数。改窗口改一处配置全局一致。
- **分档变色**（前端仪表盘）：**≥85% 绿 / 60–85% 黄 / <60% 红**（阈值为默认值、可按客户观感调）。原本恒绿会误导（30% 也显绿）。仪表盘旁 InfoTooltip 用业务白话解释口径；下方图例把百分比拆成「健康 X · 风险 Y（断货/告急/偏低）· 无销量 Z」的绝对数。
- **商品明细**：`get_stock_risk` 的 item 带 `sku_name`（`Inventory.sku_name`，变体名如「红色 / M」，缺失回退 `sku_id`）+ `image_url`（product 级主图，批量查 `Product.main_image_url`、带 scope 过滤防 N+1/跨租户）。看板明细走**展示口径 `include_all=True`**（列全部在库 SKU，配合状态筛选/分页；含 `ok` 充足 / `idle` 无销量两桶，前端有对应 badge），**buckets 计数仍只算风险桶**。前端明细：PC 表格 / 移动端卡片式（响应式，不横滚）、长商品名 2 行截断、**前端分页（每页 5）**。

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

代码：`services/ad_metrics.py`（取数 + 拆分 + 护栏）、`web/routes/board.py:_collect`（overview.ads 注入）、前端 `BoardPage.tsx` 经营概览广告/ROAS 卡。数据源 `fact_ad_spend_daily`（`flows/sync_ad_spend`，结算口径、按 `order_create_time` 归印尼业务日）。

- **三项均来自已结算 statement 的 `fee_tax_breakdown.fee`**：`gmv_max_ad_fee_amount`（GMV Max 投流）/ `tap_shop_ads_commission`（TAP 达人广告）/ `affiliate_ads_commission_amount`（联盟达人 CPS 佣金）。

### 6.1 ⚠️ 两类口径分开：付费投放 vs 达人佣金（2026-06-28 真打修正）

**这是普适口径，不是为某店定制**：GMV Max（付费投放）与达人佣金（TAP+联盟）本质不同——预算撬动 vs 成交分佣——任何店都该分开。**把成交分佣当广告投放算 ROAS 会误导**：佣金成交后按比例分、跟着 GMV 走、无撬动，`GMV ÷ 佣金` = 佣金率倒数，无"广告效率"含义。

真打 prod 店（`7494172960764429390`，授权 OK、60 结算单/3.4 万交易全可拉）恰好 **GMV Max = 0、广告消耗几乎全是达人佣金**，把这点暴露得最清楚（不拆分就会算出"本周 ROAS 暴涨"的假象）。**接入有 GMV Max 投放的店时**，付费投放 > 0、ROAS 才真正有"广告效率"含义，结算护栏（§6.2）也才发挥防近窗虚高的作用——设计已为此就绪，无需按店改代码。

**⚠️ 站内三项的本质（查证 + 客户澄清，2026-06-28）——只有 GMV Max 是付费投放：**

| 字段 | 站内营销 | 本质 | 归类 |
|------|----------|------|------|
| `gmv_max_ad_fee_amount` | **GMV Max**（小店智能广告） | 设预算买曝光、AI 投流，**预算撬动型** | **付费投放** |
| `tap_shop_ads_commission` | **TAP**（TikTok Affiliate Partner，机构代管达人） | 成交才付佣金（CPS），字段名带 "ads" 但本质是佣金 | 达人佣金 |
| `affiliate_ads_commission_amount` | **联盟**（开放达人计划） | 成交才付佣金（CPS） | 达人佣金 |

> 站外投流（TikTok Ads Manager 独立广告账户）走**另一套 Marketing API、不进 Shop 结算单**，本数据源（Finance statement）天然只有站内三项——与"目前只有站内投流"一致，非遗漏。

故拆两类：
- **付费投放** `paid_ad_spend = gmv_max_fee`（**仅 GMV Max**）：**ROAS 只对它算**（`get_roas` 分母）；未投 GMV Max → `paid_ad_spend=0 → roas=None`，前端 ROAS 标「未投 GMV Max（全靠达人带货）」，诚实留空。
- **达人带货佣金** `creator_commission = tap_commission + affiliate_commission`：CPS 分佣，单列展示、不进 ROAS。
- 前端「广告消耗」卡 value 仍显营销总支出、`InfoTooltip` 拆「付费投放（仅 GMV Max）X · 达人带货佣金（TAP+联盟）Y」；广告环比（`change.ad_cost`）按付费投放算（与 ROAS 口径一致）。
- **纠偏 1（口径）**：早前误把 TAP 与 GMV Max 一起算"付费投放"——TAP 是 TikTok Affiliate **Partner**（达人联盟代运营、成交分佣），是佣金不是投放，已挪到达人佣金侧。
- **纠偏 2（接通）**：旧文档"广告消耗=0 是没接通 Finance scope"在此店**不成立**——授权是通的，付费投放真的=0（达人主导店本就不投 GMV Max）。"是否接通"不能只看广告消耗低。

### 6.2 ⚠️ 结算滞后护栏（`complete` / `settled_through`）

广告费仅**已结算**才有，且 `fact_ad_spend_daily` 按 `order_create_time` 归日 → 近几天下单的单多未结算、广告费**持续填充中**（叠加同步 timer 未跑则更滞后）。真打实证：6/24-6/28 广告消耗几乎为 0，本周 ROAS 因分母被掏空而"暴涨"5 倍，是**结算滞后假象**，非广告变高效。

- 护栏**不看"有没有数据"**（近期日有数据但不全会误判完整），看**结算完整线** `settled_through = as_of − ad_settle_lag_days`（`settings.ad_settle_lag_days` 默认 **14**，与 §7.1 扣点 `fee_rate_settle_lag_days` 同源同量级）。窗口结束日晚于该线 → `complete=False`。
- 前端 `complete=False` 时广告/ROAS 卡标注「结算中·截至 MM-DD」，且**不显环比**（避免把结算未回读成涨跌）。这与利润卡 `coverage_complete`、费率告警 `settle_lag_days` 是同一类滞后护栏。
- **效应滞后 ≠ 结算滞后**：广告"本周花、下周起量"是真实业务现象（效应滞后，主要在 GMV Max/TAP 这类预算投放）；但看板上更主导的是**结算滞后**（费用入库晚）。本店付费投放≈0，故效应滞后基本不适用，失真几乎全来自结算滞后 + 把佣金当广告。
- 接通更多投放数据前提：Partner Center 加 Finance 权限 + 店铺重授权（本店已通）；同步靠 `data-sync-ad-spend` timer（过审前停，故 fact 表需手动补跑才完整）。

---

## 7. 告警规则

详见 `docs/proactive-push-ops.md`。同一 scan flow（`flows/scan_fulfillment_alerts.py`）下 5 条确定性规则，每个收件人各自独立判定 / 去重 / 投递：
1. **待发货超时**（按 `tts_sla_time`）。
2. **低库存 / 断货**（按可售天数 = 销速模型）。
3. **扣点率异常（结算口径）**（见 §7.1）。
4. **及时费率异常（预估口径）**（见 §7.1，B1）。
5. **爆单**（某商品当日已付款销量破阈值）。命中商品若为**近 N 天新品**（`get_new_product_ids`，N=`new_product_lookback_days`），文案标注 🌟「新上线爆款」+ 追单/备货提示——同阈同去重，**不重复推送**（新品爆单已被本规则覆盖，仅加醒目标注，见 §4.4）。

走 Data Hub 确定性判定 + `openclaw message send` 直投（0-LLM），与日报（过 LLM）分离。

> 低库存告警用**告警口径**（`get_stock_risk` 默认 `include_all=False`，只推卖得动快断货的）；
> 日报/周报里的断货预警卡用**展示口径**（`include_all=True`，全量按可售天数升序）。详见 §4.4。

### 7.1 费率（扣点率）异常告警

> 痛点：平台**悄悄调佣 / 新增费项**（"突然多收两三个点、月底结算才发现"）。代码：`services/fee_rate_metrics.py`（取数）、`services/fee_rate_alerts.py`（判定 + 文案）、`flows/scan_fulfillment_alerts.py`（巡检接入）。

**费率怎么算**：接口**不返回"费率"字段，只返回金额**；费率是我们自己除出来的，但**分子的扣费金额是 TikTok 官方算好的**（不是自估）：

```
窗口费率 = Σ官方扣费金额 ÷ Σ订单 GMV    （分子分母必须同一批订单）
```
- **分子**＝官方扣费金额，**含税**（字段名即"fee_tax"）、负数=对卖家扣款（落库统一翻正，见 §2 / 财务符号约定）。**不含物流费**（`est_shipping_cost_amount` 是独立字段，不进费率）。
- **分母**＝订单 `total_amount`（distinct，一单多笔交易不重复计 GMV）。
- ⚠️ 分子分母**必须同一批订单**——曾因分子按创建日全部单、分母按付款日已送达单（COD 主导下两批差很大）算出 49% 幽灵佣金。

**维度**＝时间窗口 × 币种 × scope（店/范围）。多订单**合并、GMV 加权**算一个总费率（不是单笔；单笔因类目/有无联盟广告剧烈波动，是噪声）。多币种取**评估窗口 GMV 最大的主币种**，其余忽略（不跨币种混算）。

**两个口径（互补）**：

| 口径 | 评估窗口（eval） | 基准（baseline） | 特点 |
|------|------|------|------|
| **结算（规则 3）** | `[今天−lag−eval_window+1, 今天−lag]` 已结算费率 | 其前 `baseline_days` 天 | 真实最终值，但**滞后**（结算后才有） |
| **及时 / 预估（规则 4，B1）** | 最近 `realtime_eval_days` 天**未结算预估**费率（无滞后） | 已结算历史费率（稳基准） | 平台一调佣预估费率立即变，**结算前**即可报 |

**告警条件**（只报上升，下降是好事不报）：评估费率 > 基准 且 **相对升幅 > `rel_pct`** 且 **绝对升幅 > `abs_pct`（百分点）**，双阈值同时满足。

**护栏（防误报）**：评估 / 基准任一窗口 GMV < `min_gmv`，或基准费率 ≤ 0（冷启动 / 历史不足）→ 跳过并记 `skip_reason`，**不报**。

**去重**：同一评估窗口结束日只报一次；结算口径与及时口径**独立去重状态**（`fee_rate_anomaly` vs `fee_rate_anomaly_realtime`），互不覆盖。及时口径 eval_end=今天 → 每业务日最多一次。

**分项归因（B2，"是哪项费用涨"）**：告警触发后点名升幅最大的费项（如"动态佣金 +2.1pct（8%→11%）"）。
- components 从 `fee_breakdown` JSON 聚合**完整费项**（主佣金 `dynamic_commission` 等只在 JSON、不在提升列）。
- 仅对 eval/baseline **交集费项**归因；**跨口径命名不同**（结算 `platform_commission` vs 未结算 `dynamic_commission`，两套体系）→ 交集空 → **不误判暴涨、降级为"当前构成展示"**。
- ⚠️ 官方"费税合计"明细只覆盖 **~80%**（约 20% 未明细化）→ **总费率准、分项归因只覆盖 80%**。

**配置参数**（`core/config.py`，默认按 IDR）：

| 参数 | 默认 | 含义 |
|------|------|------|
| `fee_rate_settle_lag_days` | 14 | 结算滞后回看天数（结算口径 eval/baseline 都从更早处取） |
| `fee_rate_eval_window_days` | 7 | 结算口径评估窗口天数 |
| `fee_rate_baseline_days` | 28 | 基准窗口天数 |
| `fee_rate_realtime_eval_days` | 3 | 及时口径评估窗口（≤ `unsettled_lookback_days`，全量替换只留近几天） |
| `fee_rate_alert_rel_pct` | 0.15 | 相对升幅阈值（比基准高 15%） |
| `fee_rate_alert_abs_pct` | 0.03 | 绝对升幅阈值（高 3 个百分点） |
| `fee_rate_min_gmv` | 10_000_000 | 窗口 GMV 护栏（低基数不报） |

**触发 vs 诊断粒度分离**：触发判定保持**店×币种×窗口总费率**（GMV 加权、稳、不误报）；细化只用在**诊断展示**（费项轴 B2 已做；类目轴待接 `get_product` 补 category；商品/订单级噪声大不做）。

**类目轴为何暂缓（2026-06-27 决策）**：类目级是真实缺口——总费率 GMV 加权会把"平台只调单个类目佣金"稀释掉（单类目占比小则总费率不过阈值、漏报），而 `dynamic_commission` 本就按类目浮动。但当前**不做**，三条依据：① 数据撑不起——单店、业务 timer 全停、结算历史薄，连总费率告警都还触发不了；类目级要求每类目都有足够单量才算得出稳定费率，更吃数据，薄数据上跑必误报（违背上面的"判定越细越误报"原则）。② 会议主诉求"突然多收两三个点、月底才发现"是**普涨**，B1（无滞后）+B2（点名费项）已覆盖；单类目隐蔽上调是二阶问题。③ 需先接 `get_product` 回填 category（M2/周报品类拆分另有此需），不应为费率告警单独提前做。**重启条件**：timer 已启用且费率历史攒够 + category 已因他用回填 + 实践中确观察到单类目隐蔽调佣。届时做法须为**诊断展示**（总费率告警触发后附"涨幅集中在 X 类目"），**不得让类目级独立触发**（独立触发必误报）。

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
| 2026-06-27 | 费率告警业务规则落档（§7.1）：费率定义=官方扣费额÷订单GMV同批订单/含税不含物流、结算+及时双口径、双阈值+护栏、B2分项归因(交集费项·跨口径降级·80%覆盖)、配置参数表、触发vs诊断粒度分离 | 本文 §7.1、`services/fee_rate_{metrics,alerts}.py`、`flows/scan_fulfillment_alerts.py` |
| 2026-06-27 | §7.1 补「类目轴为何暂缓」决策：真实缺口(单类目调佣被总费率稀释)但当前不做(数据薄/B1B2已覆盖普涨/需先接 get_product)，重启条件 + 届时须诊断展示不得独立触发 | 本文 §7.1 |
| 2026-06-28 | 看板「近 30 天新品」卡：近30天上线在售品的每日销量曲线 + 单日破阈(=50,同爆单告警)界面提醒；飞书爆单告警(规则5)对新品标注 🌟，同阈不重复推送 | 本文 §4.4/§7、`services/order_metrics.py`、`services/hotsell_alerts.py`、`web/routes/board.py` |
| 2026-06-28 | 广告口径拆分 + ROAS 只对付费投放；结算滞后护栏 complete/settled_through(ad_settle_lag_days=14)，近窗标「结算中」不显环比。真打纠偏:授权OK、付费投放真≈0(达人主导)、本周ROAS暴涨=结算滞后假象 | 本文 §6、`services/ad_metrics.py`、`web/routes/board.py`、`core/config.py` |
| 2026-06-28 | §6.1 修正 TAP 归类:站内三项只有 GMV Max 是付费投放,TAP(TikTok Affiliate Partner 机构代管达人)+联盟均为达人 CPS 佣金→`paid_ad_spend=仅gmv_max`、`creator_commission=tap+affiliate`;ROAS 未投 GMV Max 时标「未投 GMV Max」;附站内三项本质表+站外不在结算单说明 | 本文 §6.1、`services/ad_metrics.py`、前端广告卡 |
| 2026-07-01 | 展示类 GMV 统一**下单口径**(`create_time` 归日、排除 CANCELLED、含 COD 在途)对齐 TikTok 后台——COD 主导店(77%)付款口径漏算约 80%(实测日报仅后台 1/5);ROAS 分子仍付款口径(与广告结算对齐);见 §2/§2.2 | `services/order_metrics.py`(`_time_filter`/`_time_col`+各展示函数 `by_create`)、`web/routes/{data,report,board}.py` |
| 2026-07-01 | 与后台残差 ~2.5% 对账钉死(§2.1):直打 `orders/search` vs prod 库**逐单 100% 一致**(total_count=1393、差集 0、金额不一致 0);差异纯来自「排除 CANCELLED」滞后——过去某天下单集合/金额定死不变,但 order_status 持续流转(704/1393 单变状态,UNPAID 陆续被自动取消),晚看的一方取消单更多、去 CANCELLED 后 GMV 更小;退货一单未出现且下单口径本不含退货。属实时后台 vs 定时快照的正常漂移,无需修 | 对账验证(无代码改动) |
| 2026-07-01 | **展示 GMV 改用 `sub_total`(商品小计)+含所有状态(不排除取消)，精确对齐后台**(实测 6/29 195.8M vs 后台 196.2M、6/30 178.2M vs 178.8M，差 <0.5%)。客户「后台不减少」→反推后台口径=sub_total+含取消，我们旧口径 total_amount(偏高~4%)+排除取消(偏低)两错抵消成假 2.5%。新增正交开关 `display`(=create_time+含取消+sub_total)，仅 GMV 总额/趋势/订单数/销量用；爆款榜等口径不变靠 tooltip 标注；ROAS/利润口径不动。加 `orders.sub_total` 列(手写迁移 migrate_gmv_sub_total)+回填。见 §2/§2.1/§2.2 | `models/base_models.py`、`core/domain.py`、`platforms/tiktok_shop/normalize.py`、`services/order_store.py`、`services/order_metrics.py`(`_time_filter`/`_gmv_aggregates`/5 get_gmv_* 加 `display`)、`web/routes/{data,board,report}.py`、`scripts/migrate_gmv_sub_total.py`、`frontend/.../BoardPage.tsx` |
| 2026-07-01 | 看板「库存健康」卡优化(§4.4)：健康度口径显式定义(=不缺货在售商品占比)+仪表盘分档变色(≥85绿/60-85黄/<60红,原恒绿误导)+口径 tooltip/图例；商品明细补 `sku_name`(变体名)+主图小图(批量查 Product 防 N+1/带 scope)、响应式(PC表格/移动卡片不横滚)、长名截断、前端分页(每页20)。无 schema 变更(Inventory.sku_name 早已建表) | `services/stock_metrics.py`、`web/routes/data.py`(`LowStockItem`)、`frontend/src/{api.ts,pages/BoardPage.tsx}`、`tests/test_stock_alerts.py` |
