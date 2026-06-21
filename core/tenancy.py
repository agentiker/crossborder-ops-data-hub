"""多租户（多飞书 app）解析工具——单一事实源。

account_id（= 飞书 app 维度，值如 `ecom-app` / `ecom-app-gtl`）是唯一的租户主键。
数据层（user_roles / conversation_scope_bindings / business_scopes / 告警去重表）都按
account_id 隔离。本模块负责把「一个请求属于哪个租户」从最可信的来源解析出来：

- **board / web 冷登录**：子域名 Host（`gtl.board.agenticker.cc` → `ecom-app-gtl`）。
  子域名在 cloudflared 隧道层就被钉死、用户改不了，比任何 query/cookie 都可信。
- **报告链接 / 对话**：account 编进签名 token / 经 X-Account-Id 头注入（见 web/signed_link、
  web/security），不走本模块的 Host 解析。

未知/裸域一律回落 DEFAULT_ACCOUNT（= 主租户 ecom-app），保证旧链接/旧部署零行为变更。
host→account 映射放 config（env 驱动）——它是部署拓扑（隧道放行了哪些子域名）的一部分，
与 cloudflared config / 飞书 app 凭据同级，理应同处管理、随部署走。
"""
from __future__ import annotations

import contextvars
from typing import Optional

from core.config import settings

# 向后兼容锚点：裸域 board.agenticker.cc、未知 host、未传 X-Account-Id 头一律回落到它。
# 所有存量数据（cookie/token/binding/user_roles）都是 ecom-app，回落语义完全正确。
DEFAULT_ACCOUNT = "ecom-app"


# ── 请求级当前租户（对话/MCP 路径用）─────────────────────────────────────────
# data API/MCP 工具被 openclaw 调用时不走 Host（同进程 ASGI），租户由请求头 X-Account-Id
# 注入。web/security.bind_account_context 依赖在每个 /api/data 请求开头把它写进此 contextvar，
# _resolve_scope / scope_binding / 链接签发等下游读取，免去逐端点透传（漏一个即 fail-open）。
# 默认 DEFAULT_ACCOUNT：未注入头（旧 openclaw、内部调用）= 主租户，零行为变更。
_current_account: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_account", default=DEFAULT_ACCOUNT
)


def set_current_account(account_id: Optional[str]) -> None:
    """写当前请求的租户（须在 async 依赖里调，threadpool 的 set 不回传父 context）。"""
    _current_account.set(account_id or DEFAULT_ACCOUNT)


def current_account() -> str:
    """读当前请求的租户；未设置 → DEFAULT_ACCOUNT。"""
    return _current_account.get()


def account_for_host(host: Optional[str]) -> str:
    """从 Host 头解析 account_id。未知/裸域/空 → DEFAULT_ACCOUNT（兼容旧链接）。"""
    if not host:
        return DEFAULT_ACCOUNT
    host = host.split(":", 1)[0].strip().lower()  # 去端口
    return settings.tenancy.host_to_account.get(host, DEFAULT_ACCOUNT)


def account_from_request(request) -> str:
    """从 FastAPI Request 的 Host 头解析 account_id。"""
    return account_for_host(request.headers.get("host"))


def public_base_url_for(account_id: str) -> str:
    """该租户的公网根地址（报告/看板链接用）。未配则回落 dashboard.public_base_url。"""
    return settings.tenancy.public_base_url.get(
        account_id, settings.dashboard.public_base_url
    )
