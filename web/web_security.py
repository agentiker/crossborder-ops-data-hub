"""看板登录态守卫（plan/14 Phase 3）。

require_web_user 是 /board* 路由的依赖：从签名 cookie 取 open_id → 查 user_roles 拿角色。
- 无 cookie / cookie 失效 → 跳飞书登录（WebAuthRedirect → 302 /board/auth/feishu/login）。
- 有效登录但 open_id 未授角色 → fail closed，403 页（区别于对话侧的灰度开关，看板恒拒）。

OAuth 回调已自助登记（services.user_authz.ensure_registration）：首登者自动成 boss、
其余落"待审批"。故 403 页按登记状态给不同文案——pending 显友好的"申请已提交、等开通"，
不再回显 open_id / 要人跑 CLI（那一步已被回调自动登记取代）。
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.config import settings
from services.user_authz import (
    UserPermission,
    get_registration_status,
    get_user_permission,
)
from web.web_session import verify_session_cookie

LOGIN_PATH = "/board/auth/feishu/login"


class WebAuthRedirect(Exception):
    """未登录：应 302 跳到飞书登录入口。"""

    def __init__(self, location: str = LOGIN_PATH):
        self.location = location


class WebAuthForbidden(Exception):
    """已登录但 open_id 未授权：fail closed 403。status 区分待审批/已停用/无登记，决定文案。"""

    def __init__(self, open_id: str, status: str = "none"):
        self.open_id = open_id
        self.status = status  # pending / deactivated / none


def require_web_user(request: Request) -> UserPermission:
    """看板鉴权依赖：返回登录用户的权限快照，或抛重定向/403。"""
    cfg = settings.feishu_oauth
    raw = request.cookies.get(cfg.cookie_name, "")
    open_id = verify_session_cookie(raw) if raw else None
    if not open_id:
        raise WebAuthRedirect()
    perm = get_user_permission(open_id)
    if perm is None:
        raise WebAuthForbidden(open_id, get_registration_status(open_id))
    return perm


def require_web_user_api(request: Request) -> UserPermission:
    """API 版鉴权依赖（plan/15 Web 对话端）：未登录/未授权返回 JSON 错误而非 302/HTML。

    SPA 用 fetch 调 /api/* 时，302 跳登录会被透明跟随成 HTML 破坏 JSON 解析；故 API
    统一返回 401（未登录，前端据此跳 /board/auth/feishu/login）/ 403（已登录未授角色）。
    鉴权口径与 require_web_user 完全一致（同一 cookie + 同一 user_roles 真相）。
    """
    cfg = settings.feishu_oauth
    raw = request.cookies.get(cfg.cookie_name, "")
    open_id = verify_session_cookie(raw) if raw else None
    if not open_id:
        raise HTTPException(status_code=401, detail="未登录")
    perm = get_user_permission(open_id)
    if perm is None:
        detail = (
            "申请已提交，等待管理员开通"
            if get_registration_status(open_id) == "pending"
            else "账号未获授权，请联系管理员"
        )
        raise HTTPException(status_code=403, detail=detail)
    return perm


def register_web_auth_handlers(app) -> None:
    """把上面两个异常装成响应（在 web/app.py 调用一次）。"""

    @app.exception_handler(WebAuthRedirect)
    async def _redirect(_request: Request, exc: WebAuthRedirect):
        return RedirectResponse(exc.location, status_code=302)

    @app.exception_handler(WebAuthForbidden)
    async def _forbidden(_request: Request, exc: WebAuthForbidden):
        return HTMLResponse(_forbidden_page(exc.status), status_code=403)


def _forbidden_page(status: str = "none") -> str:
    """按登记状态渲染未授权页。pending=申请已提交、等开通；其余=未获授权、联系管理员。"""
    if status == "pending":
        icon, title, desc = (
            "⏳",
            "申请已提交，等待管理员开通",
            "管理员开通你的数据范围后，刷新本页即可访问，无需重新登录。",
        )
    else:  # deactivated / none
        icon, title, desc = (
            "🔒",
            "你已登录，但暂无看板权限",
            "你的账号未获授权（或已被停用），请联系管理员开通后再访问。",
        )
    return (
        _FORBIDDEN_PAGE.replace("__ICON__", icon)
        .replace("__TITLE__", title)
        .replace("__DESC__", desc)
    )


_FORBIDDEN_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>暂无看板权限</title>
<style>
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#0f1117; color:#e6e8ee;
         font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }
  .box { text-align:center; padding:32px 24px; max-width:460px; }
  .icon { font-size:40px; margin-bottom:12px; }
  h1 { font-size:18px; margin:0 0 8px; font-weight:600; }
  p { color:#8a90a2; margin:6px 0; font-size:13px; }
</style>
</head>
<body>
  <div class="box">
    <div class="icon">__ICON__</div>
    <h1>__TITLE__</h1>
    <p>__DESC__</p>
  </div>
</body>
</html>"""
