"""Network diagnostics shared by Prefect flows."""


def log_egress_ip() -> None:
    """Print current egress IP for TikTok allow-list troubleshooting."""
    import requests

    try:
        ip = requests.get(
            "https://ifconfig.co/ip",
            timeout=10,
            proxies={"http": None, "https": None},
        ).text.strip()
        print(f"出口 IP（需在 TikTok IP 白名单中）: {ip}")
    except Exception as e:  # noqa: BLE001
        print(f"出口 IP 查询失败（不影响同步）: {e}")
