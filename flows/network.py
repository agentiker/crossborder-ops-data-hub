"""Network diagnostics shared by sync flows."""

import re

# 用国内 echo 服务查出口 IP。
#
# 为什么不用 ifconfig.co/ipify 这类海外服务：服务器上跑着 ShellCrash 透明代理
# （iptables TPROXY，网络层劫持），海外域名会被兜到代理节点，查出来是代理 IP
# （如 Cloudflare WARP 104.28.x / IPv6 2a09:bac5），而 TikTok 流量是按域名直连的
# （只有 *.tiktok-shops.com / *.tiktokglobalshop.com 在直连白名单）。两者出口不同，
# 海外 echo 会误报，让人以为"没走对出口"。
#
# 改用 oray 的国内 DDNS 检测：ShellCrash 对国内 IP 直连，所以查到的就是真实 NAT
# 出口（即 TikTok 看到的、需要在白名单里的那个 IPv4）。
_EGRESS_PRIMARY = "https://ddns.oray.com/checkip"  # 返回 "Current IP Address: x.x.x.x"
_EGRESS_FALLBACK = "https://ifconfig.co/ip"        # 海外兜底（仅本机直连环境准确）


def log_egress_ip() -> None:
    """Print current egress IP for TikTok allow-list troubleshooting."""
    import requests

    no_proxy = {"http": None, "https": None}
    try:
        text = requests.get(_EGRESS_PRIMARY, timeout=10, proxies=no_proxy).text
        m = re.search(r"\d{1,3}(?:\.\d{1,3}){3}", text)
        ip = m.group(0) if m else text.strip()
        print(f"出口 IP（需在 TikTok IP 白名单中）: {ip}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"出口 IP 主查询失败，尝试兜底: {e}")

    try:
        ip = requests.get(_EGRESS_FALLBACK, timeout=10, proxies=no_proxy).text.strip()
        print(f"出口 IP（兜底源，服务器上可能是代理 IP，仅供参考）: {ip}")
    except Exception as e:  # noqa: BLE001
        print(f"出口 IP 查询失败（不影响同步）: {e}")
