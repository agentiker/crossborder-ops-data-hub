"""敏感信息脱敏（plan 审计合规：防密钥泄漏进审计表/响应）。

TikTok 接口异常（如 requests.HTTPError）的字符串带**完整请求 URL**，而 auth 端点把
app_secret / sign / refresh_token / 授权 code 都放在 query —— 直接 str(e) 落进
api_call_logs.error / audit_log.summary 或回显给调用方，就把 app 级长期密钥写进了长存表/外泄。

`redact_secrets` 把消息里任何 URL 的 query 串整段抹成 `?<redacted>`（保留路径供排障）。
对「密钥只在 URL query」的已知泄漏向量足够；非 URL 内联密钥需调用方另行处理。
"""
from __future__ import annotations

import re

# 匹配 http(s) URL 直到 '?'，吃掉其后的整段 query（到空白为止）。
_URL_QUERY = re.compile(r"(https?://[^\s?]+)\?[^\s]*")


def redact_secrets(text: str | None) -> str | None:
    """抹掉消息中所有 URL 的 query 串（app_secret/sign/token/code 都在 query）。"""
    if not text:
        return text
    return _URL_QUERY.sub(r"\1?<redacted>", text)
