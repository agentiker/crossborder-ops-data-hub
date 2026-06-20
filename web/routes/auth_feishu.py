"""飞书 OAuth 登录路由（plan/14 Phase 3）。挂在 /board/auth/feishu。

- GET /login：签一个短时效 state（防 CSRF，可携带回跳 next）→ 302 跳飞书授权页。
- GET /callback：校 state → 用 code 换 token → 取 open_id → 签登录 cookie → 302 回 next 或 /board。
- GET /logout：清 cookie → 回 /board（随即触发重新登录）。

登录成功后默认回 /board；若发起登录时带了 next（仅 /report、/board、/app 白名单内的站内
路径），登录后回跳到该 URL。next 编进签名 state 一起防篡改，回跳前再做一次白名单校验，
避免开放重定向。token 只用一次取 open_id 即丢弃。
"""

from __future__ import annotations

import base64
import logging
import re
import secrets

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.config import settings
from web.feishu_oauth import (
    FeishuOAuthError,
    build_authorize_url,
    exchange_code_for_token,
    fetch_open_id,
)
from web.web_session import _make_signed, _verify_signed, make_session_cookie

logger = logging.getLogger(__name__)
router = APIRouter()

_STATE_TTL = 600  # state 有效期 10 分钟，够走完授权
_HOME = "/board"

# 登录后回跳白名单：仅允许站内 /report、/board、/app 路径，防开放重定向。
_SAFE_NEXT_RE = re.compile(r"^/(report|board|app)(/|$|\?)")


def _safe_next(path: str) -> str | None:
    """校验 next 是站内安全路径；不合规返回 None。"""
    if not path or not path.startswith("/"):
        return None
    if path.startswith("//") or path.startswith("/\\"):  # 协议相对 URL → 开放重定向
        return None
    return path if _SAFE_NEXT_RE.match(path) else None


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@router.get("/login", include_in_schema=False)
async def login(next_: str = Query("", alias="next")):
    """发起登录：生成签名 state（可携带白名单 next）→ 跳飞书授权页。"""
    nonce = secrets.token_urlsafe(16)
    safe = _safe_next(next_)
    # next 编进 state 的 value（b64url，避免 ':'/特殊字符破坏底层 payload 分隔），与 nonce 同签。
    value = f"{nonce}|{_b64url(safe.encode('utf-8'))}" if safe else nonce
    state = _make_signed(value, _STATE_TTL)
    try:
        url = build_authorize_url(state)
    except FeishuOAuthError as exc:
        logger.error("build_authorize_url 失败：%s", exc)
        return HTMLResponse(_auth_error("登录暂不可用（飞书应用未正确配置）"), status_code=500)
    return RedirectResponse(url, status_code=302)


@router.get("/callback", include_in_schema=False)
async def callback(
    code: str = Query("", description="飞书回调授权码"),
    state: str = Query("", description="登录发起时签发的 state"),
):
    """飞书回调：校 state → 换 token → 取 open_id → 签 cookie → 回 next 或看板。"""
    state_value = _verify_signed(state) if state else None
    if state_value is None:
        # state 缺失/伪造/过期 → 防 CSRF，拒绝。
        return HTMLResponse(_auth_error("登录校验失败，请重新发起登录"), status_code=400)
    if not code:
        return HTMLResponse(_auth_error("未收到授权码，请重新登录"), status_code=400)

    # 从签名 state 解出回跳目标；缺失/非白名单一律回 _HOME（开放重定向防护）。
    dest = _HOME
    if "|" in state_value:
        try:
            nxt = _b64url_decode(state_value.split("|", 1)[1]).decode("utf-8")
            dest = _safe_next(nxt) or _HOME
        except (ValueError, UnicodeDecodeError):
            dest = _HOME

    try:
        token = exchange_code_for_token(code)
        open_id = fetch_open_id(token)
    except FeishuOAuthError as exc:
        logger.warning("飞书 OAuth 回调失败：%s", exc)
        return HTMLResponse(_auth_error("飞书登录失败，请重试"), status_code=502)

    logger.info("看板登录成功：open_id=%s", open_id)  # 运维可据此用 user_admin 登记角色
    cfg = settings.feishu_oauth
    resp = RedirectResponse(dest, status_code=302)
    resp.set_cookie(
        key=cfg.cookie_name,
        value=make_session_cookie(open_id),
        max_age=cfg.session_ttl_seconds,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="lax",
        path="/",  # 全站共享登录态：/board 看板与 /app 对话、/api/* 同源 cookie（plan/15）
    )
    return resp


@router.get("/logout", include_in_schema=False)
async def logout():
    """登出：清登录 cookie，回 /board（随即触发重新登录）。"""
    cfg = settings.feishu_oauth
    resp = RedirectResponse(_HOME, status_code=302)
    resp.delete_cookie(key=cfg.cookie_name, path="/")
    return resp


def _auth_error(msg: str) -> str:
    return _AUTH_ERROR_PAGE.replace("__MSG__", msg)


_AUTH_ERROR_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>登录失败</title>
<style>
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#0f1117; color:#e6e8ee;
         font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }
  .box { text-align:center; padding:32px 24px; max-width:420px; }
  .icon { font-size:40px; margin-bottom:12px; }
  h1 { font-size:18px; margin:0 0 8px; font-weight:600; }
  p { color:#8a90a2; margin:0 0 16px; font-size:13px; }
  a { color:#5b8cff; text-decoration:none; border:1px solid #272b38; border-radius:16px;
      padding:6px 16px; font-size:13px; }
</style>
</head>
<body>
  <div class="box">
    <div class="icon">⚠️</div>
    <h1>登录失败</h1>
    <p>__MSG__</p>
    <a href="/board/auth/feishu/login">重新登录</a>
  </div>
</body>
</html>"""
