# 接入 TikTok Marketing API 拉 GMV Max 花费 — 代码侧准备

## 背景与目标
- GMV Max 花费不在 Shop Finance 结算里（prod 真店 24812 笔 `gmv_max_ad_fee_amount` 恒 0），必须接 **独立的 Marketing/Business API**（`business-api.tiktok.com`）。
- 客户已确认 GMV Max 每周有支出，值得接。
- 本轮目标：**只做代码侧准备**，等客户建 App + 授权拿到 `advertiser_id + access_token` 后即可真打。不改动现有利润/日报链路。

## 认证流程依据（官方 Python SDK 逐字确认）
- 换 token：`POST /open_api/v1.3/oauth2/access_token/`，body（application/json）= `app_id + auth_code + secret`，**无 grant_type**。响应含 `access_token`、`advertiser_ids[]`、`scope[]`。token 不过期（除非 revoke）。
- 列广告主：`GET /open_api/v1.3/oauth2/advertiser/get/`，参数 `app_id + secret + access_token`。
- 列店铺：`GET /open_api/v1.3/store/...`（gmv_max_store_list）参数 `advertiser_id + access_token`。
- 拉报表：`GET /open_api/v1.3/gmv_max/report/get/`，必填 `advertiser_id + store_ids[] + dimensions[] + metrics[] + start_date + end_date`，token 走 **`Access-Token` 请求头**。

## 关键架构决策
1. **不继承 `BaseAPIClient`**：它是 Shop 专用（`app_secret+path+params+body` 拼 HMAC sign、token 走 query）。Marketing API 用 `Access-Token` 头、无 sign 拼接，签名模型完全不同。强行继承会套错签名 → 独立写 client。
2. **复用 `token_store`**：`PlatformToken` 表通用，新增 `platform="tiktok_business"` 的 scope 行即可，不建新表。
3. **复用 `FactAdSpendDaily.gmv_max_fee`** 字段与 `services/ad_spend_store.py`、`flows/sync_ad_spend.py` 骨架 —— 现在喂的是 Finance 空数据，接通后改喂真实 `cost`。
4. **多租户**：走现有 `account_id` 体系（ecom-app / ecom-app-gtl），token 按 account 隔离。

## 落地文件清单

### 新增
- `platforms/tiktok_business/__init__.py`
- `platforms/tiktok_business/client.py` — 独立 client：
  - `authenticate(auth_code)` → POST oauth2/access_token，存 token（复用 token_store，platform=tiktok_business）
  - `get_advertisers()` → oauth2/advertiser/get
  - `get_gmv_max_stores(advertiser_id)` → store list
  - `get_gmv_max_report(advertiser_id, store_ids, start_date, end_date, metrics=["cost","gross_revenue","orders","roi"], dimensions=[...])`
  - 请求封装：`Access-Token` 头 + JSON，处理 `code!=0` 业务错误（Marketing API 错误码体系与 Shop 不同）
- `scripts/probe_gmv_max_report.py` — 最小真打脚本：给定 advertiser_id + token，打通 advertiser→store→report 三步，打印 `cost` 原始字段，验证接口 + 口径。

### 修改
- `core/config.py` — 新增 `TikTokBusinessConfig`（`app_id / secret / base_url="https://business-api.tiktok.com" / redirect_uri`），挂到 `Settings`，支持多租户 env（仿现有 apps 结构或单 app 垫片）。
- `web/routes/auth.py` — 新增 `GET /callback/tiktok_business` 站内回调（仿现有 `/callback/tiktok`）：换 token、存库、审计留痕、`ensure_registration` 归属 account。
- `.env`（你侧填）— 新增 business app 凭据 key。

### 暂不做（接通验证后再排）
- 把 `flows/sync_ad_spend.py` 切到真实 `cost` 数据源、加 timer、看板 GMV Max 消耗接线、ROAS 口径统一。这些等真打验证字段后单独一轮。

## 你需要做的（并行）
1. 建 App：勾全 **Reporting + Ads Management + Store/Catalog Management**（scope 建 App 时固化，漏勾要重建，自用 App 勾多无害）。
2. 填 **Advertiser Redirect URL** = `https://<租户域名>/auth/callback/tiktok_business`。
3. 提交审核（2–3 天，可能更久）。过审 + 客户授权后，把 `advertiser_id + access_token`（或授权链接产出的 auth_code）给我。

## 验证路径
过审后 → 我用 `scripts/probe_gmv_max_report.py` 真打 → 确认 `cost` 字段实际键名与量级 → 再开下一轮（灌数据 + 看板 + ROAS）。

## 风险 / 未坐实点（诚实标注）
- Marketing API 的**错误码体系、report 响应里 spend/cost 的确切键名**、dimensions 可选值 —— portal 文档 JS 渲染读不到原文，SDK 只给方法签名。故 client 里这些做成**配置可调 + 真打时坐实**，不硬编码猜测。
- 账户级 GMV Max 授权开关可能在 Business Center 客户授权时另有一步，届时对界面点。

## 落地实现状态（2026-07-09 完成代码侧）
用 fetch-web-doc skill（playwright 拦截 `/gateway/api/doc/client/node/get/`）抓到官方正文，
**metrics 键名已 100% 坐实，不再是未知点**：
- 正确接口文档页 = `/run-a-gmv-max-campaign-report/v1.3`（旧的 gmv-max-ads-reports 两页已标"待废弃"，都指向此新页）。
- 花费字段 = **`cost`**（字符串），响应结构 `data.list[].metrics.{cost,net_cost,gross_revenue,orders,roi,currency}`。
- 硬约束：store_ids 单次≤1；含 stat_time_day 时窗口≤30天；日期基于**广告账户时区**（第 4 个时区，归日小心）。

已落代码（540 测试全绿）：
- `core/config.py` — `TikTokBusinessConfig` + `TikTokBusinessCredential`（多租户 apps + 单 app 垫片）。
- `platforms/tiktok_business/client.py` — 独立 client（不继承 BaseAPIClient），authenticate/get_advertisers/get_gmv_max_stores/get_gmv_max_report，Access-Token 头，code!=0 抛 TikTokBusinessError，复用 token_store（platform=tiktok_business，advertiser_id 落 seller_id 槽）。
- `web/routes/auth.py` — `GET /auth/callback/tiktok_business` 站内回调（state 承载 account_id 多租户归属）。
- `scripts/probe_gmv_max_report.py` — 三步真打脚本（advertiser→store→report），打印 cost 核对。
- `tests/test_tiktok_business_client.py` — 4 条回归锁（Access-Token 头 / 数组 JSON 串 / 无 grant_type / advertiser 走 query token）。

待客户建 App 过审 + 授权后：`python -m scripts.probe_gmv_max_report --auth-code <CODE> --account-id ecom-app` 真打验证，再开下一轮（灌数据+看板+ROAS）。

