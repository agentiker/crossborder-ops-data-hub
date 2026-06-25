"""飞书 OAuth 登录路由（plan/14 Phase 3）。挂在 /board/auth/feishu。

- GET /login：签一个短时效 state（防 CSRF，可携带回跳 next）→ 302 跳飞书授权页。
- GET /callback：校 state → 用 code 换 token → 取 open_id → 签登录 cookie → 302 回 next 或 /board。
- GET /logout：清 cookie → 停在「已退出」确认页（不自动跳 /app，否则会被立刻拉回授权页）。

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
from core.tenancy import account_from_request
from services.user_authz import ensure_registration
from web.feishu_oauth import (
    FeishuOAuthError,
    build_authorize_url,
    exchange_code_for_token,
    fetch_user_identity,
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


def _redirect_uri(request: Request) -> str:
    """按当前子域名 Host 重建回调地址（须与 token 交换时逐字一致、且在该 app 白名单中）。

    cloudflared 终止 TLS 后转发 http 给本服务，request.url.scheme 会是 http；公网回调一律 https。
    """
    host = (request.headers.get("host") or "").split(",")[0].strip()
    return f"https://{host}{settings.feishu_oauth.redirect_path}"


@router.get("/login", include_in_schema=False)
async def login(request: Request, next_: str = Query("", alias="next")):
    """发起登录：从子域名定 account → 生成签名 state（含 account + 白名单 next）→ 跳对应 app 授权页。"""
    account = account_from_request(request)
    # 诊断日志（PC/移动端 webview 定位）：是哪种客户端把请求带到了 OAuth 发起这一步。
    logger.info("feishu login 发起：account=%s next=%s ua=%r",
                account, next_, request.headers.get("user-agent", ""))
    nonce = secrets.token_urlsafe(16)
    safe = _safe_next(next_)
    # state value = account|nonce[|b64url(next)]（均不含 ':'/'|'，与底层 payload 分隔不冲突）。
    # account 编进 state：回调以 state 里的 account 为准选凭据（防 Host 头被篡改）。
    parts = [account, nonce]
    if safe:
        parts.append(_b64url(safe.encode("utf-8")))
    state = _make_signed("|".join(parts), _STATE_TTL)
    try:
        url = build_authorize_url(
            state, account_id=account, redirect_uri=_redirect_uri(request)
        )
    except FeishuOAuthError as exc:
        logger.error("build_authorize_url 失败（account=%s）：%s", account, exc)
        return HTMLResponse(_auth_error("登录暂不可用（飞书应用未正确配置）"), status_code=500)
    return RedirectResponse(url, status_code=302)


@router.get("/callback", include_in_schema=False)
async def callback(
    request: Request,
    code: str = Query("", description="飞书回调授权码"),
    state: str = Query("", description="登录发起时签发的 state"),
):
    """飞书回调：校 state → 换 token → 取 open_id → 签 cookie → 回 next 或看板。"""
    logger.info("feishu callback 到达：has_code=%s ua=%r",
                bool(code), request.headers.get("user-agent", ""))
    state_value = _verify_signed(state) if state else None
    if state_value is None:
        # state 缺失/伪造/过期 → 防 CSRF，拒绝。
        return HTMLResponse(_auth_error("登录校验失败，请重新发起登录"), status_code=400)
    if not code:
        return HTMLResponse(_auth_error("未收到授权码，请重新登录"), status_code=400)

    # state value = account|nonce[|b64url(next)]。解出 account（选凭据用）与回跳目标。
    # 缺失/非白名单 next 一律回 _HOME（开放重定向防护）。
    parts = state_value.split("|")
    account = parts[0]
    dest = _HOME
    if len(parts) >= 3:
        try:
            nxt = _b64url_decode(parts[2]).decode("utf-8")
            dest = _safe_next(nxt) or _HOME
        except (ValueError, UnicodeDecodeError):
            dest = _HOME

    try:
        token = exchange_code_for_token(
            code, account_id=account, redirect_uri=_redirect_uri(request)
        )
        open_id, name = fetch_user_identity(token)
    except FeishuOAuthError as exc:
        logger.warning("飞书 OAuth 回调失败（account=%s）：%s", account, exc)
        return HTMLResponse(_auth_error("飞书登录失败，请重试"), status_code=502)

    # 自助申请登记：首登者（该 account 下 user_roles 表空）bootstrap 为本租户 boss、其余落
    # "待审批"自动进该租户老板审批列表。范围由老板在网页开通前不可用（fail-closed 不变）。
    try:
        reg = ensure_registration(open_id, name=name, account_id=account)
    except Exception:  # 登记失败不阻断登录（设了 cookie 仍能走到 403 页），仅记日志
        logger.exception("自助登记失败：open_id=%s account=%s", open_id, account)
        reg = "existing"
    logger.info("看板登录成功：open_id=%s account=%s name=%s reg=%s", open_id, account, name, reg)
    if reg in ("boss", "pending"):  # 授权记录：仅新登记，existing 每次登录不刷屏
        from services.audit import log_audit_event_safe

        log_audit_event_safe(
            event_type="authorization", event_action="feishu.register",
            actor_open_id=open_id, actor_source="oauth", account_id=account,
            target=open_id, summary=f"飞书登录自助登记：{reg}",
            after={"name": name, "result": reg},
        )
    cfg = settings.feishu_oauth
    resp = RedirectResponse(dest, status_code=302)
    resp.set_cookie(
        key=cfg.cookie_name,
        value=make_session_cookie(open_id, account_id=account),
        max_age=cfg.session_ttl_seconds,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="lax",
        path="/",  # 全站共享登录态：/board 看板与 /app 对话、/api/* 同源 cookie（plan/15）
    )
    return resp


@router.get("/logout", include_in_schema=False)
async def logout():
    """登出：清登录 cookie，停在「已退出」确认页（不自动跳 /app）。

    若像旧逻辑那样登出后 302 回 /app，/app 一加载发现无登录态又自动拉去飞书授权 →
    用户永远退不出去。所以这里返回一个静态确认页：默认就停住，由用户自己点「重新登录」
    才再走授权，真正实现"退出登录"。
    """
    cfg = settings.feishu_oauth
    resp = HTMLResponse(_LOGGED_OUT_PAGE)
    resp.delete_cookie(key=cfg.cookie_name, path="/")
    return resp


def _auth_error(msg: str) -> str:
    return _AUTH_ERROR_PAGE.replace("__MSG__", msg)


_LOGGED_OUT_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>已退出登录</title>
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
    <div class="icon">👋</div>
    <h1>已退出登录</h1>
    <p>你已安全退出。需要时可重新登录。</p>
    <a href="/board/auth/feishu/login?next=/app">重新登录</a>
  </div>
</body>
</html>"""


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
