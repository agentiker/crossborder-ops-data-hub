---
name: check-outbound-ip
description: 查本机未经代理的真实出口 IP。当用户说「查出口 IP」「本机公网 IP 是多少」「出口 IP 有没有变」时使用。
---

# 查本机真实出口 IP

本机开了 ShellCrash 透明代理（iptables TPROXY），大部分出站流量会被代理劫持。
直接 `curl ipinfo.io` 或 `curl ddns.oray.com` 查到的是**代理出口 IP**，不是本机真实 NAT IP。

要拿到真实出口 IP，必须用 ShellCrash 配置为 **DIRECT 直连** 的目标。

## 方法

```bash
curl -s --connect-timeout 5 http://www.baidu.com -o /dev/null -w "%{remote_ip}"
```

`www.baidu.com` 在 ShellCrash 中走直连，所以返回的是真实出口 IP。

## 判断依据

- 结果是住宅/运营商 IP（如 `112.94.x.x`、`157.148.x.x`）→ 真实出口 IP
- 结果是 CDN/代理 IP（如 `104.28.x`、`2a09:bac5:...`）→ 被代理兜走了，不可信

## 背景

- 本机和 hp 服务器在同一局域网（192.168.1.0/24），共用同一出口 IP
- 出口 IP 是 PPPoE 动态 IP，路由器 re-dial 后会变
- 该 IP 需登记在 TikTok Shop Partner Center 白名单内，否则 API 返回 403
- 详见 `plan/10_ops_known_issues.md` 第 1 节
