"""飞书内嵌 H5 看板的 HMAC 签名时效 token（仅标准库，不加依赖）。

定位：路线 A 的「身份」环节——bot 发一条带签名 token 的链接，token 里编进飞书 `open_id`
和过期时间，本服务验签拿回 `open_id`，据此按 binding scope 强制软隔离（见 plan/13）。
**不碰飞书 OAuth/JSSDK**，纯对称签名。

token 格式：``<payload_b64url>.<sig_b64url>``
  - ``payload = "<open_id>:<exp_unix>"``（open_id 形如 ou_xxx，不含 `:`）
  - ``sig = HMAC_SHA256(secret, payload)``
  - b64url 均去掉 padding（`=`）。

密钥取 `settings.dashboard.link_secret`：未配置时 `make_token` 抛错（拒绝签出无效 token），
`verify_token` 一律返回 None（fail closed）。验签用 `hmac.compare_digest` 防时序侧信道。
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
    """b64url 编码并去掉 padding。"""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """b64url 解码，补回 padding；非法输入抛 binascii.Error / ValueError。"""
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(payload: str, secret: str) -> str:
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64url_encode(sig)


def make_token(
    open_id: str, account_id: str = DEFAULT_ACCOUNT, ttl: Optional[int] = None
) -> str:
    """签发一个在 ttl 秒后过期、绑定 open_id + 租户 account_id 的 token。

    ttl 缺省取 settings.dashboard.token_ttl_seconds。密钥未配置时抛 RuntimeError。
    多租户：payload value 内用 `|` 拼 ``open_id|account_id``（report 路由据此做跨租户校验）。
    """
    secret = settings.dashboard.link_secret
    if not secret:
        raise RuntimeError("DASHBOARD__LINK_SECRET 未配置，无法签发看板链接")
    if not open_id:
        raise ValueError("open_id 不能为空")
    if ttl is None:
        ttl = settings.dashboard.token_ttl_seconds
    exp = int(time.time()) + int(ttl)
    payload = f"{open_id}|{account_id}:{exp}"
    sig = _sign(payload, secret)
    return f"{_b64url_encode(payload.encode('utf-8'))}.{sig}"


def verify_token(token: str) -> Optional[tuple[str, str]]:
    """验签并返回 ``(open_id, account_id)``；任何失败均返回 None。

    向后兼容：旧格式 token（value 只含 open_id、无 `|`）回落 account_id=DEFAULT_ACCOUNT。
    """
    secret = settings.dashboard.link_secret
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

    # value 不含 ':'，从右切出 exp，兼顾稳健
    try:
        value, exp_str = payload.rsplit(":", 1)
        exp = int(exp_str)
    except ValueError:
        return None
    if not value or exp < int(time.time()):
        return None
    if "|" in value:
        open_id, account_id = value.split("|", 1)
        return open_id, (account_id or DEFAULT_ACCOUNT)
    return value, DEFAULT_ACCOUNT
