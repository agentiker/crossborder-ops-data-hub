# 运维手册（Ops Runbook）

生产机日常巡检、常见操作、排坑库。投产步骤见 `production-deployment.md`，openclaw 网关见 `openclaw-setup.md`。

> prod = ssh 别名 `prod`（独立空库，连本机 127.0.0.1 MySQL）。命令默认在部署用户下、`~/code/crossborder-ops-data-hub`。
> 非交互 ssh 的 PATH 没有 nvm/uv，跑 uv 用绝对路径 `~/.local/bin/uv`。

---

## 1. 日常巡检

```bash
uptime                 # 负载（prod 基线 ~0.03）
free -h                # 内存（基线 used ~1.2G / total 3.4G，available ~2.2G；openclaw ~330MB 最大头）
df -h /                # 磁盘（基线 ~24%）
swapon --show          # swap 在不在（2C4G 必须有 4G）

systemctl --user list-timers 'data-*' --all     # 定时器：NEXT/LAST/状态
systemctl --user is-active data-hub.service openclaw-gateway.service cloudflared-board.service
curl -s http://127.0.0.1:8000/health            # web 健康
```

**timer 时间表（OnCalendar 按 OS 本地 CST；业务日 WIB 由 core/timezone 独立写死，不受 OS 时区影响）**：

| 类别 | timer | 触发（CST） |
|---|---|---|
| 业务同步 | sync-orders | 每时 :23 |
| 业务同步 | sync-inventory | 每时 :17 |
| 业务同步 | sync-fulfillments | 每时 :05,20,35,50 |
| 业务同步 | scan-alerts | 每时 :10,40 |
| 业务同步 | sync-sku-variants | 07:05 |
| 业务同步 | push-replenishment | 07:35 |
| 业务同步 | sync-ad-spend | 03:17、15:17 |
| 业务同步 | refresh-tokens | 00,06,12,18 :41 |
| 业务同步 | sync-unsettled-fees | 01:13（收盘后，落 WIB 午夜之后） |
| 业务同步 | aggregate-profit | 01:33（依赖 unsettled） |
| 基建 | anchor-audit | 02:07 |
| 基建 | backup-db | 02:43 |
| 基建 | verify-audit-chain | 03,09,15,21 :17 |

> ⚠️ TikTok 过审/正式运营前，**10 个业务同步 timer 全 disabled**，仅 3 个基建 timer 起。状态核对：
> `systemctl --user list-unit-files 'data-*.timer'`（基建 3 个 enabled、业务 10 个 disabled 为预期）。

**日志位置**（systemd user，统一 journald）：

```bash
journalctl --user -u data-hub.service -n 100 --no-pager          # web
journalctl --user -u openclaw-gateway.service -n 100 --no-pager  # 飞书网关/对话
journalctl --user -u cloudflared-board.service -n 50 --no-pager  # 隧道
journalctl --user -u data-sync-orders.service -n 50 --no-pager   # 某个同步任务
```

---

## 2. 常见操作

```bash
# 重启服务（web 改了代码 / openclaw 改了配置才需要）
systemctl --user restart data-hub.service
systemctl --user restart openclaw-gateway.service     # 改 openclaw.json 后，等 ~2s 再验

# 手动跑一次同步（业务 timer 停着时拉数）
~/.local/bin/uv run python -m flows.sync_inventory
~/.local/bin/uv run python -m flows.sync_orders        # 同理 sync_sku_variants / sync_fulfillments / sync_ad_spend

# 部署 / 更新（含 uv sync + init_db + 装 timer；改了 web 代码加 --restart-web）
# ⚠️ 过审前部署务必加 --no-business-timers：只起基建 timer（anchor/backup/verify），
#    不 enable 业务同步/聚合/告警 timer，避免顺手把 scan-alerts 拉起误发告警。
./deploy/deploy.sh --restart-web --no-business-timers

# 仅前端改动可绕开 deploy.sh（不触发 timer enable）：git pull + 直接构建 frontend/dist（见 §2.5 / deploy-hp skill）

# 若忘了加 --no-business-timers（或老脚本），跑完手动把业务 timer 停回过审前：
for t in orders inventory fulfillments sku-variants ad-spend unsettled-fees; do \
  systemctl --user disable --now data-sync-$t.timer; done
for t in aggregate-profit scan-alerts push-replenishment refresh-tokens; do \
  systemctl --user disable --now data-$t.timer; done

# 启用业务 timer（过审后逐个起）
systemctl --user enable --now data-sync-orders.timer
systemctl --user daemon-reload                          # ⚠️ enable 后必跑，否则 list-timers 的 NEXT 为空

# ── plan/19 交付版「上线」启用顺序（手动验一轮后逐个起；本计划只备脚本、不代执行）──
# 依赖：报告/看板/费率监控都吃同步数据，先起同步、再起聚合、最后起扫告警。
# 1) 同步（无依赖，可全起）
for t in orders inventory fulfillments sku-variants ad-spend unsettled-fees; do \
  systemctl --user enable --now data-sync-$t.timer; done
# 2) 聚合利润（依赖订单+unsettled+ad-spend）
systemctl --user enable --now data-aggregate-profit.timer
# 3) 扫告警（费率/待发货/断货/爆单；依赖上面已落库）
systemctl --user enable --now data-scan-alerts.timer
systemctl --user daemon-reload
# 验证：先 dry-run 看判定不误报
~/.local/bin/uv run python -m flows.scan_fulfillment_alerts --dry-run
# ⚠️ 费率「告警」需 ~2 周已结算历史填满 baseline 才会真正触发（冷启动期 build_decision 走护栏跳过、不误报）。
#    看板「费率监控卡」走 unsettled 实时口径、无此限制——同步一起就有当前费率/趋势可看。

# 预检（改 .env 后、部署前）
~/.local/bin/uv run python -m scripts.preflight
```

**查授权店**（platform_tokens；正确列名）：

```sql
SELECT id, platform, country, shop_id, seller_id, account_id, scope_key,
       token_expire_at, updated_at
FROM platform_tokens;     -- access_token/refresh_token 为 fcr1: 前缀密文（TOKEN_ENCRYPTION_KEY 生效）
```

**查 bootstrap / 操作者权限**（user_roles）：

```sql
SELECT account_id, open_id, role, is_active FROM user_roles;   -- role=boss & is_active=1 即 bootstrap 成功
```

---

## 3. 排坑库（本项目实测，症状 → 根因 → 修法）

- **list-timers 的 NEXT 为空 / timer 不触发** → `enable --now` 后没 reload。修：`systemctl --user daemon-reload`。
- **`/app` 登录报「登录暂不可用（飞书应用未正确配置）」** → 不是浏览器缓存！是 `.env` 的 `FEISHU_OAUTH__APPS` 缺当前 account 凭据（日志 `build_authorize_url 失败 account=<x>` 是铁证）。常因租户名从 ecom-app 改成 ecom-app-gtl 后漏配。修：补 `FEISHU_OAUTH__APPS={"<account>":{...}}` 重启 data-hub。`scripts/preflight.py` 能提前查出。
- **bootstrap 落错租户 / 商家店铺挂错 account** → `TENANCY__DEFAULT_ACCOUNT` 与飞书 agent 的 accountId 名实不符（默认 ecom-app）。修：单客户机 `.env` 设 `TENANCY__DEFAULT_ACCOUNT` + `HOST_TO_ACCOUNT` 三处对齐（preflight 单租户对齐校验）。对齐后授权 token 直落正确 account，无需 `scripts/shop_admin.py assign`。
- **客户授权后浏览器提示「不允许跳转 callback」** → ≠ 授权失败。某些 App 内浏览器（如战斧/Ads Manager）拦第三方重定向，但 TikTok 已把 code 打到我们 callback。查 `platform_tokens` 有该 shop_id 记录即成功，**勿让客户重授**。建议客户用 Chrome 走授权。
- **同步报 TikTok 403 / IP 不在白名单** → 出口 IP 未加白名单，或白名单刚加未生效（几分钟延迟期间 403 正常）。prod 出口 = 本机公网 IP（直连）。判出口直接打 TikTok 接口，别用 ipify 推断（规则代理下它走代理 IP）。见记忆 `tiktok-api-direct-connect`。
- **openclaw 对话报 LLM 500** → `LLM__PROVIDER` 填了 `glm`。中转站 api.agent0101.com 是 Anthropic 兼容，必须填 `anthropic`（代码工厂只认 anthropic/openai_compat）。openclaw 侧同理 provider api=`anthropic-messages`。见 `openclaw-setup.md` §6。
- **ssh 跑 uv/openclaw 报 command not found** → 非交互 ssh 的 PATH 没有 nvm/uv。用绝对路径 `~/.local/bin/uv`、`~/.nvm/.../bin/openclaw`；systemd 单元同理写绝对路径。
- **换机后飞书 ws 频繁掉线** → 同一飞书 app 被两机同时连（双持互踢）。一个 app 只能一个实例。见 `openclaw-setup.md` §6。
- **ssh 连 prod 慢** → 见 `docs/ssh-latency-troubleshooting.md`。

相关记忆：`prod-deployment-status`、`local-lan-hp-egress`、`server-ssh-access`、`tiktok-api-direct-connect`、`multitenant-account-id-architecture`、`audit-compliance-token-encryption`。
