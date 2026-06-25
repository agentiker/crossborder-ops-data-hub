"""脱敏测试（plan 审计合规：防密钥泄漏进审计表/响应）。"""
from __future__ import annotations

from core.redact import redact_secrets


def test_redact_strips_url_query_with_secrets():
    """HTTPError 串里带 app_secret/sign 的 URL query 被整段抹掉，路径保留。"""
    msg = ("401 Client Error: Unauthorized for url: "
           "https://auth.tiktok-shops.com/api/v2/token/refresh?"
           "app_key=AK&app_secret=SUPERSECRET&refresh_token=RT&sign=SIG")
    out = redact_secrets(msg)
    assert "SUPERSECRET" not in out and "RT" not in out and "SIG" not in out
    assert "https://auth.tiktok-shops.com/api/v2/token/refresh?<redacted>" in out


def test_redact_noop_without_url():
    assert redact_secrets("plain error, no url") == "plain error, no url"
    assert redact_secrets("") == ""
    assert redact_secrets(None) is None
