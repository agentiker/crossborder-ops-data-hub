"""飞书 OAuth v2 网页免登客户端（看板登录用，见 plan/14）。

三步（已查最新文档）：
  1. build_authorize_url(state)：拼授权页 URL，浏览器跳转过去（飞书客户端内自动免登）。
  2. exchange_code_for_token(code)：回调拿到的 code 换 user_access_token。
  3. fetch_open_id(token)：用 token 取用户 open_id。

登录后只用一次 token 取 open_id 即丢弃（不持有/不刷新，省 offline_access）；登录态由
web/web_session.py 的签名 cookie 承载。HTTP 用已有 requests，零新增依赖。

open_id 是 per-app 的：app_id/app_secret 必须与运营对话用的是同一飞书 app（建议 ecom-app），
user_roles 存的 open_id 才能三处串起来。
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode

import requests

from core.config import settings

# 飞书登录端点。授权入口刻意用 authen/v1/index 而非 accounts 域的 authen/v1/authorize：
#   - index：网页应用登录入口，复用浏览器/飞书客户端已有的登录态自动发码，已授权用户
#     不再每次弹"权限同意卡片"（竹云/Logto 等外部浏览器免密方案均用此端点）。入参用 app_id。
#   - authorize（accounts 域）：OAuth 同意页流程，外部浏览器下每次都展示同意卡片——即本次
#     "反复要授权"的根因。index 发的 code 同样被下面的 v2 token 端点接受（Logto 实证）。
AUTHORIZE_URL = "https://open.feishu.cn/open-apis/authen/v1/index"
TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"

# 拿 open_id 的最小权限；已在飞书后台为该 app 开通。默认**不**塞进 authorize（见下），
# 避免每次登录弹同意页；保留常量供需要显式增量授权时引用。
DEFAULT_SCOPE = "contact:user.id:readonly"

_TIMEOUT = 10


class FeishuOAuthError(RuntimeError):
    """飞书 OAuth 交互失败（配置缺失 / 网络错 / 飞书返回非 0 code）。"""


def build_authorize_url(state: str, *, scope: Optional[str] = None) -> str:
    """拼飞书授权页 URL。app_id/redirect_uri 未配置则拒绝生成（抛错）。

    走 authen/v1/index 登录入口：复用已有飞书登录态，已授权用户静默发码、不再弹同意页。
    scope 缺省取 settings.feishu_oauth.oauth_scope（默认空=不传）。
    """
    cfg = settings.feishu_oauth
    if not cfg.app_id or not cfg.redirect_uri:
        raise FeishuOAuthError("FEISHU_OAUTH__APP_ID / REDIRECT_URI 未配置，无法发起登录")
    if not state:
        raise FeishuOAuthError("state 不能为空（防 CSRF）")
    if scope is None:
        scope = cfg.oauth_scope
    # authen/v1/index 入参约定：app_id（非 client_id）+ redirect_uri + state；回调照旧带 code。
    params = {
        "app_id": cfg.app_id,
        "redirect_uri": cfg.redirect_uri,
        "state": state,
    }
    if scope:
        params["scope"] = scope
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str) -> str:
    """用回调 code 换 user_access_token。code 5 分钟单次有效。"""
    cfg = settings.feishu_oauth
    if not cfg.app_id or not cfg.app_secret:
        raise FeishuOAuthError("FEISHU_OAUTH__APP_ID / APP_SECRET 未配置")
    if not code:
        raise FeishuOAuthError("code 不能为空")
    try:
        resp = requests.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": cfg.app_id,
                "client_secret": cfg.app_secret,
                "code": code,
                "redirect_uri": cfg.redirect_uri,
            },
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise FeishuOAuthError(f"换 token 请求失败：{exc}") from exc

    data = _parse_json(resp)
    # v2 端点：成功时顶层带 access_token；失败时 code != 0。
    if data.get("code") not in (0, None):
        raise FeishuOAuthError(f"飞书换 token 返回错误：code={data.get('code')} {data.get('error_description') or data.get('msg')}")
    token = data.get("access_token")
    if not token:
        raise FeishuOAuthError("飞书换 token 响应缺 access_token")
    return token


def fetch_user_identity(user_access_token: str) -> tuple[str, Optional[str]]:
    """用 user_access_token 取 (open_id, name)。name 是飞书昵称，缺失则 None（不强求）。

    自助申请登记用：name 写进 user_roles.note，老板审批时认得出是谁。open_id 仍强校验，缺则抛错。
    """
    if not user_access_token:
        raise FeishuOAuthError("user_access_token 不能为空")
    try:
        resp = requests.get(
            USER_INFO_URL,
            headers={"Authorization": f"Bearer {user_access_token}"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise FeishuOAuthError(f"取 user_info 请求失败：{exc}") from exc

    data = _parse_json(resp)
    if data.get("code") not in (0, None):
        raise FeishuOAuthError(f"飞书取 user_info 返回错误：code={data.get('code')} {data.get('msg')}")
    payload = data.get("data") or {}
    open_id = payload.get("open_id")
    if not open_id:
        raise FeishuOAuthError("飞书 user_info 响应缺 open_id")
    name = payload.get("name") or payload.get("en_name") or None
    return open_id, name


def fetch_open_id(user_access_token: str) -> str:
    """用 user_access_token 取 open_id（fetch_user_identity 的薄封装，保留旧调用兼容）。"""
    open_id, _ = fetch_user_identity(user_access_token)
    return open_id


def _parse_json(resp) -> dict:
    try:
        return resp.json()
    except ValueError as exc:
        raise FeishuOAuthError(
            f"飞书响应非 JSON（HTTP {getattr(resp, 'status_code', '?')}）"
        ) from exc
