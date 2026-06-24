---
status: active
owner: codex
depends_on: [01_multi_shop_token_and_auth, 07_scope_foundation]
---

# 10 运维已知问题 / 待办

记录 2026-06-08 把定时数据拉取部署到测试服务器（yamk）后，**当前能跑但留有隐患**的两点。
部署细节（systemd user timer、ShellCrash 直连）见 README「服务器定时任务运维」。

## 1. 出口 IP 是住宅 IP，会漂移

TikTok Shop API 走 IP 白名单。本机和服务器在同一局域网（192.168.1.0/24 → 网关 192.168.1.1），
IPv4 出站被 SNAT 成同一公网 IP（广州联通住宅宽带），这个 IP 已登记在
TikTok 后台白名单内。

**隐患**：出口 IP 是**住宅 PPPoE 动态 IP**，不是固定 IP。一旦路由器 re-dial
（断电、重启、运营商强制下线），公网 IP 可能变成别的值——此时**本机和服务器会同时失效**
（两边共用同一出口），所有 TikTok sync / token 刷新会 403。

**怎么发现**：用 `check-outbound-ip` skill 查出口 IP（`curl -s http://www.baidu.com -o /dev/null -w "%{remote_ip}"`），
与 TikTok 后台白名单比对。变了就是这里出问题。或 `journalctl --user -u data-sync-orders` 看到 403。

**怎么处理（临时）**：到 TikTok Shop Partner Center 把新出口 IP 加进白名单。

**根治方向（待定）**：
- 上固定出口（VPS 固定 IP / 专线静态 IP / 花生壳等带固定出口的服务），让出口 IP 稳定；
- 或做出口 IP 监控 + 变更告警，IP 一变就提醒去后台更新白名单。
- 优先级看 sync 中断的实际频率，暂不动。

## 2. flow 入口写死"单店自动发现"，接第二个店会报错

当前只有 **1 个授权店铺**：印尼 TikTok Shop，`shop_id=7494691994496238970`（country=ID）。

`flows/sync_inventory.py` / `sync_orders.py` / `refresh_tokens.py` 的 `__main__` 通过
`flows/_shop_discovery.py:discover_single_shop()` 从 `platform_tokens` 读**唯一**授权店铺。
该函数在 **0 店或 >1 店时直接 `raise`**（设计如此，不猜）。

**隐患**：以后接入第二个店（不论同国多店还是新国家），三个 flow 的 `__main__`（以及
对应的 systemd timer）会因为 `discover_single_shop()` 抛"Multiple authorized shops"而**全部失败**。

**到时怎么改**：把 flow 入口从"发现单店"改成"遍历所有授权店铺，按店循环跑"。注意：
- idempotency_key 用真实 `country/shop_id` 拼，别退回 GLOBAL/`_`（否则同 SKU 双行，见
  08a-deployment 记忆的"flow 入口默认 scope"坑）；
- 这和 01（多店 token/auth）、07（scope 集合查询）同源，真要做多店时一起规划，别只补 flow 入口。

**现在不做**：单店场景能跑，提前做多店循环是过度设计。这条只是"接第二个店之前必须先改"的提醒。
