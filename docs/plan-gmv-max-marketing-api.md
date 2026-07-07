# GMV Max 广告花费接入看板 — 方案评估

> 2026-07-07 | 状态：待审阅 | 背景见 memory `roi-roas-alert-data-source`

## 1. 为什么要做（已确认的事实）

- 客户店铺开通了 GMV Max 且**确有明显花费**：后台 Seller Center → Marketing → Shop ads → GMV Max，近 7 天 **Cost 9,989.99 USD**、ROI 4.98、SKU orders 6,018。
- 我们看板「广告消耗」里 GMV Max 恒显示 **0** —— prod 真打实测：24812 笔结算交易 `gmv_max_ad_fee_amount` 全为 0，未结算预估也为 0。**不是 bug**，是数据源不对。
- 根因：GMV Max 是**广告侧投放产品**，花费挂在独立广告账户「印尼1店-max户」，走广告账户扣费，**不进 TikTok Shop 结算单**。Shop Finance API 天然看不到。
- 结论：要在看板显示 GMV Max 花费，**必须接 TikTok Marketing API**（与现有 Shop API 是两套独立体系）。

### 1.1 已排查：TikTok Shop 侧无广告花费接口（2026-07-07，查 material/ + 新版 Go SDK）

有人问「TikTok Shop 有没有提供 GMV Max 接口、能否去 Partner Center 申请权限」。**查证结论：Shop 有直播分析接口但无广告花费接口，申请了也拿不到 spend。**

- **新版 Go SDK（go_sdk.zip，2026-07 更新）确实新增了 Live Data scope（`creator.data.live.read.public`）+ 一批接口**：Get GMV Trend / Interactive Trend / Live Core Stats / Traffic Performances / User Portraits / View Trend Performances（见 changelog.txt）。这些**真实存在、可在 Partner Center 申请**。
- **但它们给不了 GMV Max 广告花费**：全是 `LiveRooms/{直播间ID}/...` 维度的**直播间经营分析**——返回 GMV、销售额、观看数、下单数、点击率、粉丝互动、观众画像。搜遍全批接口返回字段，**无任何 spend/cost/ad_fee/campaign/budget/roas 金额字段**；唯一带 "ads" 的 `paid_ads_age_indicators` 是"付费广告来的观众年龄画像占比"（人群分布，非金额）。
- **关键区别**：这批接口回答"直播卖了多少 GMV、观众是谁"，不是"GMV Max 广告花了多少钱"。GMV(成交额)≠ ad spend(广告支出)，接口名叫 GMV Trend 也一样。
- 全新 SDK 搜广告投放接口(func) = 仍 **0**。Shop 的 analytics 只有经营分析，不含广告花费。
- 根因：GMV Max 花费本质是广告账户的钱，归 **TikTok for Business（广告平台）**管，不归 TikTok Shop（电商平台）管。**Marketing API 是唯一数据源。**
- 附带价值：这批 Live Data 接口对**未来做直播分析看板**（直播间 GMV/流量/观众画像）有用，可回头再用。

## 2. 数据源（已抓官方文档确认）

- 接口：`GET https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/`
- 关键参数：`service_type=AUCTION` + **`report_type=TT_SHOP`**（官方定义 "GMV max ads report"）+ `data_level=AUCTION_ADVERTISER`
- 维度：`advertiser_id` / `campaign_id` / `country_code` / `stat_time_day`（按天）
- 指标：`spend`（总花费含广告券）/ `billed_cost`（净花费扣券）/ `campaign_name`
- 授权主体：**advertiser_id + Access-Token header**（不是 shop_cipher）
- 限制：GMV Max 报表**只支持同步模式**、**沙箱不可用** → 只能在 prod 真实广告账户验证（同 Finance 沙箱坑）

## 3. 已定的三个口径决策（用户 2026-07-07 确认）

1. **看板**：汇总成一个「广告消耗」卡片（复用现有）+ 三类分类明细（GMV Max / TAP / 联盟）。ROAS 用 GMV Max 花费算。
2. **币种折算**：USD 花费统一折算并入现有口径（详见 §5，建议 **USD→CNY 直折**，优于 subagent 提的 USD→IDR 叉汇）。
3. **节奏**：先出本方案评估，确认后再开建。

## 4. 工作量：两块相对独立

### 4A. 平台接入（大头，接全新平台）

Marketing API 与 Shop API **只有约 30-40% 可复用**（token 管理框架、retry、审计钩子），签名/授权/scope 全不同。

| 组件 | 现状 | 判断 | 说明 |
|---|---|---|---|
| `core/base_client.py` 签名 | Shop 用 HMAC-SHA256(secret+path+params+body+secret) | **新建** | Marketing 签名规则不同，子类覆盖 `_generate_sign()` |
| `platforms/tiktok_marketing/client.py` | 无 | **新建** | 新 client：签名 + Access-Token header + advertiser_id |
| `PlatformToken` 表 | 有 shop_cipher 列 | **改造** | 加 `advertiser_id` 列；shop_cipher 置空复用本表 |
| `services/scoping.py` | build_scope_key | **改造** | 支持 advertiser_id 维度 |
| OAuth 授权回调 `web/routes/auth.py` | Shop OAuth（auth.tiktok-shops.com） | **改造/新建** | Marketing 是另一套 OAuth 端点，回调框架可复用、换 client |
| `flows/refresh_tokens.py` | 只刷 tiktok_shop | **改造** | 扩展刷 tiktok_marketing token |

**⚠️ 最大不确定性 = 授权链路**：需先跑通「拿到该广告账户 advertiser_id + 可用 Access-Token」。这一步可能要在 TikTok for Business 侧注册 Marketing API App（独立审核）、走独立 OAuth。**建议做正式方案前先做一个「授权 + 打通一次 TT_SHOP 报表接口」的技术验证 spike**，把授权这个最大风险点先证伪/证实。

### 4B. 数据入库 + 看板（小头，高度复用现有链路）

一旦 4A 能拿到数据，这块很轻——现有 ad_spend 链路几乎照搬：

| 组件 | 判断 | 说明 |
|---|---|---|
| `flows/sync_gmv_max_spend.py` | **新建** | 仿 sync_ad_spend：拉 TT_SHOP 报表 → 按 stat_time_day 聚合 → 折算 → upsert |
| `FactGmvMaxMarketingSpend` 表 | **新建** | 独立表存 USD 原值 + 折算值 + 用的汇率日/值（隔离，不污染结算口径的 fact_ad_spend_daily） |
| `services/ad_spend_store.py` upsert | **新增函数** | 复用 scope_key 幂等模式 |
| `services/fx_rate.py` | **改造** | 见 §5 |
| `services/ad_metrics.py::get_ad_spend_summary` | **改造** | 并联查新表，把 Marketing 的 GMV Max 花费加进 gmv_max_fee 返回 |
| `get_roas` / `board.py` / 前端 `BoardPage.tsx` / `api.ts` | **不动** | 取数层合并后对前端透明，ROAS 分母自动含新值，卡片/弹窗自动展示 |

**结算滞后护栏（complete/结算中标注）需要重新审视**：现有 `ad_settle_lag_days=14` 是针对结算口径的。Marketing API 花费是**投放实时口径、先支后结**，不需要 14 天滞后护栏，甚至可能当天就有数据。合并两个不同时效的数据源到同一 gmv_max_fee 时，护栏逻辑要想清楚（否则近窗一直标「结算中」但其实 Marketing 数据已准）。

## 5. 币种折算方案（我定：USD→CNY 直折）

subagent 建议 USD→IDR 叉汇，但**看板利润本就折 CNY**（`fx_rate.py`：GMV/扣点/广告/退货 IDR×idr_to_rmb 折 CNY 展示）。所以：

- **中行牌价表已存全 40 币种含 USD**（`exchange_rate_store._NAME_TO_ISO` 有 "美元": "USD"），USD→CNY 是**中行直接牌价**，无需叉汇，比 USD→IDR（中行不报、要 USD→CNY÷IDR→CNY 叉汇、误差被 IDR 放大）**更准更简单**。
- 改造：`fx_rate._fetch_boc_rate(on_date, currency_code="IDR")` 参数化币种（现硬编码 IDR），新增 `get_usd_to_rmb()` / `convert_usd_to_rmb()`，与现有 `get_idr_to_rmb` 完全对称，零侵入利润链。
- fail-safe：加 `settings.usd_to_rmb` 固定值兜底（同 idr_to_rmb 模式）。
- 看板 GMV 是 IDR、广告卡折算展示——需和现有卡片币种口径对齐（现有 ad_spend 是 IDR）。**这里有个口径细节要确认**：广告消耗卡现在显示 IDR，GMV Max 折成 CNY 还是 IDR？为与卡内 TAP/联盟（IDR）一致，GMV Max 可能要 **USD→IDR** 折算入卡、CNY 只在利润链用。→ 见待确认项。

## 6. 风险 / 待确认

1. **授权是最大风险**（4A）：能否拿到该广告账户的 Marketing API 授权 + token，建议先做 spike 验证。
2. **沙箱验不了**：GMV Max 报表沙箱不可用，只能 prod 真账户端到端测（同 Finance 老坑）。
3. **卡内币种口径**：广告消耗卡 GMV Max 折 IDR（与卡内 TAP/联盟一致）还是折 CNY？倾向折 IDR 入卡、CNY 走利润链。
4. **结算滞后护栏**：Marketing 实时口径 vs 结算 14 天滞后，合并后护栏逻辑要重设计。
5. **多广告账户**：该店目前一个「印尼1店-max户」，未来多户时 scope 怎么切。
6. **出口 IP 白名单**：Marketing API 可能需单独配白名单（现 Shop 已配）。

## 7. 建议里程碑

- **Phase 0（spike，0.5-1 天）**：验证授权 —— 能否拿到 advertiser_id + Access-Token 并成功打通一次 TT_SHOP 报表接口。**这步不通，后面都白搭，优先做。**
- **Phase 1（2-3 天）**：TikTokMarketingClient（签名/授权/token）+ PlatformToken 改造 + 单测。
- **Phase 2（1 天）**：sync flow + 新表 + USD 折算改造。
- **Phase 3（1 天）**：ad_metrics 并联 + 看板集成 + 护栏重设计 + prod 真账户端到端验证。

合计约 **5-7 个工作日**（不含 Marketing API App 注册审核的等待时间，那是外部依赖）。

## 8. 需要你拍板的点

- 是否先做 Phase 0 授权 spike（强烈建议）？
- 卡内 GMV Max 折 IDR 还是 CNY（§6.3）？
- Marketing API App 是否需要重新在 TikTok for Business 侧申请（这决定是否有审核等待）？—— 需查现有 app 是否已含 Marketing 权限。

---

## 9. Marketing API 接入流程 + 注意点（2026-07-07 抓官方文档确认）

### 9.0 先回答：已接入 TTS Partner Center，对接 Marketing API 有帮助吗？

**基本没有帮助，两套体系完全独立。** 官方文档明确：

| | TikTok Shop（你已接入的） | TikTok Marketing API（GMV Max 花费） |
|---|---|---|
| 开发者门户 | Partner Center `partner.tiktokshop.com` | TikTok For Business `business-api.tiktok.com` + `ads.tiktok.com/marketing_api` |
| 账户体系 | TikTok Shop 卖家账户 | **TikTok For Business 账户**（另注册） |
| 开发者 App | Shop App（已有） | **另创建 Marketing 开发者 App，独立审核** |
| 授权主体 | 商家授权 shop（→ shop_cipher） | **广告主(advertiser)授权**（→ advertiser_id） |
| 授权凭证 | Shop OAuth code → access_token | advertiser `auth_code` → access_token |
| 签名 | HMAC-SHA256（自定义签名串） | **无自定义签名**，用 app_id+secret+auth_code 换 token，后续 header 带 access_token |

唯一能复用的是**认知/工程经验**（OAuth 回调怎么落库、token 怎么加密存、直连出口 IP 等），代码和账户层面几乎从零。所以「已接 Partner Center」不会缩短 Marketing API 的接入。

### 9.1 前置条件（一次性，按顺序）

官方 onboarding 三步，全在 TikTok For Business 侧、与 Shop 无关：

1. **创建 TikTok For Business 账户**（`ads.tiktok.com`）。
2. **注册为开发者**（Register as developer）。
3. **创建 Marketing 开发者 App** —— 创建时**勾选所需权限 scope**（要含广告报表 Reporting 权限，覆盖 `report_type=TT_SHOP`）+ 配置 **redirect_uri**（回调地址，可配最多 10 个含 localhost）。**App 需审核**（外部依赖，等待时间不可控，是排期最大变数）。

> ⚠️ 那个「印尼1店-max户」广告账户必须是一个 **TikTok For Business advertiser 账户**、且你能让它完成授权（见 9.2）。需先确认这个户归谁管、能不能配合走授权邮箱验证。

### 9.2 授权流程（每个广告账户一次，advertiser 侧要配合）

1. App 审核通过后，在 **My Apps → 你的 App → Basic Information** 找到 **Advertiser authorization URL**，发给广告主。
2. 广告主浏览器打开该 URL → 看到你申请的权限列表 → 同意平台协议 → 点 Confirm。
3. 广告主点 **Send Code**，广告账户绑定邮箱收到验证码 → 输入验证码 → Confirm。
   - **注意**：同一 App 对同一广告账户，验证有效期 **48 小时**（窗口内重复授权免验证）；换个 App 授权则要重新验证。
4. 验证通过后重定向到你的 `redirect_uri`，URL query 带 `auth_code`（**有效期 1 小时、只能用一次**，过期要重走）。
5. 你的服务端回调端点接住 `auth_code`。

### 9.3 换 access_token（服务端）

```
POST https://business-api.tiktok.com/open_api/v1.3/oauth2/access_token/
Header: Content-Type: application/json
Body: {"app_id": "...", "secret": "...", "auth_code": "..."}
```

- 响应里返回 **access_token**（application/json 方式拿的是长期 token）+ **该 token 可访问的 advertiser_ids 列表** + 权限 scope。
- 拿到 access_token + advertiser_id 后，即可调 §2 的报表接口拉 GMV Max 花费。
- token 后续请求放 header（Access-Token），**无 Shop 那套 HMAC 签名**。

### 9.4 接入注意点（踩坑预判）

- **App 审核是最大不确定性**：排期无法承诺，先提交申请让它并行审。
- **auth_code 1 小时 + 一次性**：回调端点要即时换 token，别缓存 auth_code。
- **advertiser 必须配合**：授权要广告账户邮箱验证码，非纯技术操作，需和客户/运营约时间。
- **redirect_uri 要预先配**：创建 App 时配好回调地址（含生产域名 `gtl.agenticker.cc` 之类），否则回调失败。
- **权限 scope 创建时就要选对**：漏选 Reporting/广告数据权限，拿到 token 也调不了报表，要改 scope 可能得重授权。
- **出口 IP**：Marketing API 是否需 IP 白名单待实测（Shop 那套已配），可能要单独加。
- **token 刷新**：长期 token 的过期/刷新策略需实测（文档提到 token 丢失要 advertiser 取消授权重来）。
- **多广告账户**：一个 access_token 可能覆盖多个 advertiser_id，scope 设计要考虑。

### 9.5 Phase 0 授权 spike 建议（投开发前先验证）

最小验证闭环，把「授权能不能走通」这个最大风险点先证实：

1. 注册 TikTok For Business + 开发者 + 建 App（选 Reporting scope + 配 redirect_uri）→ 提交审核。
2. 审核过后，让「印尼1店-max户」走一次 advertiser 授权，拿到 auth_code。
3. 服务端换到 access_token + advertiser_id。
4. 用它调一次 `report_type=TT_SHOP` 报表，确认能拉到 §1 后台那 9,989 USD 的 spend。

**这 4 步全通，再投 §7 的 5-7 天开发；任何一步卡住（尤其 App 审核、advertiser 授权），先解决再说。**
