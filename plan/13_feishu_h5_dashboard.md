# Plan 13 — 飞书内嵌 H5 看板 MVP（路线 A 落地）

> 状态：本机实现 + 单测 + curl 验证全部通过（2026-06-13）。隧道端到端（hp 内测）待用户决定时机；暂未 commit/部署。
> 关联记忆：`08b-scope-binding`（飞书长连接 XOR webhook 互斥）、`mvp-data-api`、`product-min-price-semantics`、`proactive-push-daily-report-and-alerts`。
> 后续：plan/09（多租户 tenant_id 硬隔离），接第二个客户时做。

## 背景 / 目标

上一会话做了本机预览看板 demo（`web/routes/dashboard.py`，Chart.js + 服务端复用 /api/data 取数）。用户看完决定**走路线 A**——把它变成飞书里能给客户用的内嵌 H5。

难点不在图表，在「给客户用」三个字：身份、暴露、隔离。三个已确认决策：

1. **身份 = openclaw 签名链接**：复用 openclaw 已有的飞书 `open_id`，bot 发一条带 HMAC 签名 token 的链接（token 含 `open_id`+过期），本项目验签拿 `open_id`。**不碰飞书 OAuth/JSSDK、不开网页应用能力、不改 openclaw 核心、不抢长连接**（飞书长连接 XOR webhook 互斥，openclaw 占着长连接，见 plan/08b）。
2. **托管 = cloudflared 临时隧道内测**：`cloudflared tunnel --url http://localhost:8000` 给 hp 服务开临时 https 公网地址。不动 hp 网络、不申域名、不进生产/systemd。
3. **隔离 = 强制软隔离**：dashboard 只认「服务端按 token 里 open_id 查到的 binding scope」，**忽略客户端 URL 里传的 `shop_id/scope_id`**，防改 URL 越权。全表 `tenant_id` 硬隔离推迟到 plan/09。

预期：老板在飞书里跟 bot 说"看板"，收到链接，点开看到**自己范围内**的 GMV/订单/销量趋势 + 爆款榜 + 断货风险，改 URL 也越不了权。

## 落地清单（已实现）

**新建**
- `web/signed_link.py` — HMAC-SHA256 时效 token（仅标准库）：`make_token(open_id, ttl)` / `verify_token(token)->open_id|None`。格式 `<payload_b64url>.<sig_b64url>`，`payload="<open_id>:<exp_unix>"`，b64url 去 padding，`hmac.compare_digest` 防时序。密钥取 `settings.dashboard.link_secret`，未配置 `make_token` 抛错、`verify_token` 返 None（fail closed）。
- `scripts/dashboard_link.py` — 本机/运维签 token CLI（argparse，仿 `scripts/scope_admin.py`）：`--open-id`（必填）/`--ttl`/`--base`，打印完整 URL。
- `tests/test_signed_link.py` — round-trip / 默认 ttl / 过期 / 篡改签名 / 篡改 payload / 错密钥 / 无密钥 / 空 open_id / 垃圾输入 / 下划线 open_id，16 用例全过。

**修改**
- `core/config.py` — 新增 `DashboardConfig`（`link_secret` / `public_base_url` / `token_ttl_seconds=1800`），挂到 `Settings.dashboard`。
- `web/routes/dashboard.py` — `/dashboard` 入参删 `scope_id`/`open_id`、改 `?t=<token>`（+ 可选 `period`）；验签失败 → 401 自包含错误页 `_render_error()`；成功取 `open_id`，**四个取数调用全部钉死 `scope_id=None, shop_ids=None, shop_id=None`**，只走 `open_id`。页脚改「已签名鉴权 · 范围按账号锁定」。**删了 127.0.0.1 无 token 旁路**（走隧道即公网可达，旁路=裸奔），本机自测靠 CLI 签 token。dashboard_router 仍挂根路径、不加 `require_internal_token`（鉴权由验签承担）。
- `web/routes/data.py` — 新增 `ops_dashboard_link` 端点（`GET /api/data/dashboard/link?open_id=`，挂 `/api/data` 带 `require_internal_token`），返回 `{url, expires_in}`；`public_base_url` 未配 → 503；签发打审计日志。
- `web/app.py` — `include_operations` 白名单加 `"ops_dashboard_link"`。
- `.env`（运维手填）— `DASHBOARD__LINK_SECRET`、`DASHBOARD__PUBLIC_BASE_URL`。

## 越权风险（MVP 接受）

`open_id` 由 agent 调 `ops_dashboard_link` 时传参，弱模型理论上可能传错 → 把 A 的链接签给 B。这是软隔离的根本局限（签发环节信任 agent 传的 open_id）。MVP 缓解：**短时效 token（30min）+ 签发审计日志**。真·隔离（tenant_id 硬隔离 + open_id 与会话强绑不由 agent 自由传）在 plan/09。

## 隧道与配置（hp 内测，临时手起，不进 systemd）

`ssh hp` 后：① 装 cloudflared（hp 未装）；② `cloudflared tunnel --url http://localhost:8000`，抓 `https://<随机>.trycloudflare.com`；③ 填进 `.env` 的 `DASHBOARD__PUBLIC_BASE_URL`（确认 `DASHBOARD__LINK_SECRET` 已设）；④ 重启 data-hub 重读 .env；⑤ 对话让 bot 取链接点开。

**坑**：quick tunnel 域名每次重启都变 → 重抓+重填+重启 data-hub；内测期 cloudflared 用 tmux/nohup 挂住别退（仍属临时，不写 unit）。

## 验证记录（2026-06-13 本机）

- 单测：`uv run pytest tests/test_signed_link.py` 16 passed；全量 `uv run pytest` 124 passed 无回归。
- 本机起服务 curl（临时 env 配 `DASHBOARD__LINK_SECRET`/`DASHBOARD__PUBLIC_BASE_URL`）：
  - 有效 token → 200；无 token / 过期(`--ttl -1`) / 篡改末位 → 401 错误页。
  - **隔离核心**：`?t=<TOKEN>&shop_id=999999&scope_id=evil-scope` → 仍 200 且 `D.scope` 不变（URL 越权被忽略；route 只接受 `t`/`period`，结构上保证）。
  - 签发端点：无 `X-Internal-Token` → 401；带头 → 200 返回 `{url, expires_in:1800}`。
  - 注：本机 DB 无 scope_bindings 表，`D.scope` 落「全部范围」；具体 binding 范围在 hp 端验。
