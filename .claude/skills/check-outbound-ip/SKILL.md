---
name: check-outbound-ip
description: 查本机未经代理的真实出口 IP。当用户说「查出口 IP」「本机公网 IP 是多少」「出口 IP 有没有变」时使用。
---

# 查本机真实出口 IP

本机/hp 开了 ShellCrash 透明代理（iptables TPROXY），大部分**海外**出站会被代理劫持，
`curl ipinfo.io`/`ifconfig.co` 查到的是代理节点 IP，不是本机真实 NAT 出口。

要拿到真实出口 IP，必须查一个 **ShellCrash 直连（国内）的 echo 服务**——它返回的是
**你自己的公网出口 IP**（即 TikTok 看到、需要在白名单里的那个 IPv4）。

## 方法（与 `flows/network.py:log_egress_ip` 同源，单一真相）

```bash
# 在 hp 上跑（项目已封装，最稳）
cd ~/code/crossborder-ops-data-hub && ~/.local/bin/uv run python -c "from flows.network import log_egress_ip; log_egress_ip()"
```

裸 curl 兜底（必须 `--noproxy` 强制不走代理；oray 是国内域名走直连，返回你的公网 IP）：

```bash
curl -s --noproxy '*' https://ddns.oray.com/checkip   # 返回 "Current IP Address: x.x.x.x"
```

## ⚠️ 不要用 `curl www.baidu.com -w "%{remote_ip}"`

`%{remote_ip}` 是**你连接的对端服务器的 IP**（即百度 CDN 节点，如 `157.148.69.x` 联通广东），
**不是你的出口 IP**；`%{local_ip}` 又只是内网网卡 IP（`192.168.1.x`）。两者都查不到出口。
（2026-06-24 踩过坑：据此把百度 IP 当出口加白名单，TikTok 必然 403。）

## 判断依据 / 验证锚点

- 结果是住宅/运营商公网 IP（如 `112.94.x.x`）→ 真实出口 IP
- 结果是 CDN/代理 IP（如 `104.28.x`、`2a09:bac5:...`）→ 被代理兜走了，不可信
- **最终判据 = TikTok 本身**：直连 `GET /authorization/202309/shops`，返 `36009033` 即 IP 不在白名单；
  能通即当前出口已在白名单。oray 查出的 IP 应与 TikTok 接受的一致（实测一致）。

## 背景

- 本机和 hp 服务器在同一局域网（192.168.1.0/24），共用同一出口 IP
- 出口 IP 是 PPPoE 动态 IP，路由器 re-dial 后会变
- 该 IP 需登记在 TikTok Shop Partner Center 白名单内，否则 API 返回 403
- 详见 `plan/10_ops_known_issues.md` 第 1 节
