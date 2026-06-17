"""看板登录态守卫（plan/14 Phase 3）。

require_web_user 是 /board* 路由的依赖：从签名 cookie 取 open_id → 查 user_roles 拿角色。
- 无 cookie / cookie 失效 → 跳飞书登录（WebAuthRedirect → 302 /board/auth/feishu/login）。
- 有效登录但 open_id 未登记角色 → fail closed，403 页（区别于对话侧的灰度开关，看板恒拒）。

未登记时 403 页**回显当前 open_id**：首次登录的老板可据此用 scripts/user_admin 把自己
登记成 boss（解开"要先登录才知道 open_id、却要先登记才能进"的鸡蛋问题），再刷新即可。
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.config import settings
from services.user_authz import UserPermission, get_user_permission
from web.web_session import verify_session_cookie

LOGIN_PATH = "/board/auth/feishu/login"


class WebAuthRedirect(Exception):
    """未登录：应 302 跳到飞书登录入口。"""

    def __init__(self, location: str = LOGIN_PATH):
        self.location = location


class WebAuthForbidden(Exception):
    """已登录但 open_id 未登记角色：fail closed 403。"""

    def __init__(self, open_id: str):
        self.open_id = open_id


def require_web_user(request: Request) -> UserPermission:
    """看板鉴权依赖：返回登录用户的权限快照，或抛重定向/403。"""
    cfg = settings.feishu_oauth
    raw = request.cookies.get(cfg.cookie_name, "")
    open_id = verify_session_cookie(raw) if raw else None
    if not open_id:
        raise WebAuthRedirect()
    perm = get_user_permission(open_id)
    if perm is None:
        raise WebAuthForbidden(open_id)
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
        raise HTTPException(status_code=403, detail=f"open_id={open_id} 未获授权")
    return perm


def register_web_auth_handlers(app) -> None:
    """把上面两个异常装成响应（在 web/app.py 调用一次）。"""

    @app.exception_handler(WebAuthRedirect)
    async def _redirect(_request: Request, exc: WebAuthRedirect):
        return RedirectResponse(exc.location, status_code=302)

    @app.exception_handler(WebAuthForbidden)
    async def _forbidden(_request: Request, exc: WebAuthForbidden):
        return HTMLResponse(_forbidden_page(exc.open_id), status_code=403)


def _forbidden_page(open_id: str) -> str:
    # open_id 不来自用户输入（取自验签通过的 cookie），但仍做最小转义防意外。
    safe = (open_id or "").replace("<", "&lt;").replace(">", "&gt;")
    return _FORBIDDEN_PAGE.replace("__OPEN_ID__", safe)


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
  code { background:#1a1d27; border:1px solid #272b38; border-radius:6px;
         padding:3px 8px; color:#5b8cff; font-size:12px; word-break:break-all; }
</style>
</head>
<body>
  <div class="box">
    <div class="icon">🔒</div>
    <h1>你已登录，但还未获授看板权限</h1>
    <p>请把下面的 open_id 发给管理员登记后再访问：</p>
    <p><code>__OPEN_ID__</code></p>
    <p style="margin-top:16px;font-size:12px;">登记命令：user_admin set --open-id 上面的值 --role boss</p>
  </div>
</body>
</html>"""
