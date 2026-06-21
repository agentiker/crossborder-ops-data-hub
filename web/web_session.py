"""独立运营看板的登录态：无状态 HMAC 签名 cookie（仅标准库，不加依赖）。

定位：飞书 OAuth 走完一次拿到 `open_id` 后即丢弃 token，登录态由本模块签发的
签名 cookie 承载——逐行同构 web/signed_link.py 的对称签名思路，换密钥源
（`settings.feishu_oauth.session_secret`）与 TTL（`session_ttl_seconds`，默认 7 天），
**不建 session 表**（YAGNI）。

token 格式：``<payload_b64url>.<sig_b64url>``
  - ``payload = "<value>:<exp_unix>"``（value 不含 `:`）
  - ``sig = HMAC_SHA256(secret, payload)``
  - b64url 去 padding。

通用 `_make_signed`/`_verify_signed` 同时供登录态 cookie 与 OAuth state（防 CSRF）复用。
密钥未配置时 `_make_signed` 抛错（拒签）、`_verify_signed` 一律返回 None（fail closed）。
验签用 `hmac.compare_digest` 防时序侧信道。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Optional

from core.config import settings
from core.tenancy import DEFAULT_ACCOUNT


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(payload: str, secret: str) -> str:
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64url_encode(sig)


def _make_signed(value: str, ttl: int) -> str:
    """签发一个在 ttl 秒后过期、绑定 value 的 token。value 不能含 ':'、不能为空。"""
    secret = settings.feishu_oauth.session_secret
    if not secret:
        raise RuntimeError("FEISHU_OAUTH__SESSION_SECRET 未配置，无法签发登录态")
    if not value:
        raise ValueError("value 不能为空")
    if ":" in value:
        raise ValueError("value 不能含 ':'（与 exp 分隔符冲突）")
    exp = int(time.time()) + int(ttl)
    payload = f"{value}:{exp}"
    sig = _sign(payload, secret)
    return f"{_b64url_encode(payload.encode('utf-8'))}.{sig}"


def _verify_signed(token: str) -> Optional[str]:
    """验签并返回 value；任何失败（密钥缺失/格式错/签名不符/过期）均返回 None。"""
    secret = settings.feishu_oauth.session_secret
    if not secret or not token:
        return None
    try:
        payload_b64, sig = token.split(".", 1)
        payload = _b64url_decode(payload_b64).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None

    expected_sig = _sign(payload, secret)
    if not hmac.compare_digest(sig, expected_sig):
        return None

    try:
        value, exp_str = payload.rsplit(":", 1)
        exp = int(exp_str)
    except ValueError:
        return None
    if not value or exp < int(time.time()):
        return None
    return value


def make_session_cookie(
    open_id: str, account_id: str = DEFAULT_ACCOUNT, ttl: Optional[int] = None
) -> str:
    """签发登录态 cookie 值（绑定 open_id + 租户 account_id）。ttl 缺省取 session_ttl_seconds。

    多租户：value 内用 `|` 拼 ``open_id|account_id``（外层 payload 仍 ``value:exp``，
    `_make_signed` 的禁 `:` 约束不破——open_id/account_id 都不含 `:`/`|`）。
    """
    if not open_id:
        raise ValueError("open_id 不能为空")
    if ttl is None:
        ttl = settings.feishu_oauth.session_ttl_seconds
    return _make_signed(f"{open_id}|{account_id}", ttl)


def verify_session_cookie(raw: str) -> Optional[tuple[str, str]]:
    """验签登录态 cookie，返回 ``(open_id, account_id)``；失败返回 None（fail closed）。

    向后兼容：旧格式 cookie（value 只含 open_id、无 `|`）回落 account_id=DEFAULT_ACCOUNT
    （存量 cookie 都是 ecom-app，回落语义正确）→ 已签发的 7 天老 session 零失效。
    """
    value = _verify_signed(raw)
    if value is None:
        return None
    if "|" in value:
        open_id, account_id = value.split("|", 1)
        return open_id, (account_id or DEFAULT_ACCOUNT)
    return value, DEFAULT_ACCOUNT
