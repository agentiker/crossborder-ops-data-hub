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
# hp 存量数据（cookie/token/binding/user_roles）都是 ecom-app，默认回落语义完全正确；
# prod 等单客户独立部署用 .env 的 TENANCY__DEFAULT_ACCOUNT 覆盖成本机租户（如 ecom-app-gtl），
# 让 board 冷登录 / token 授权 / MCP 查询回落都对齐到飞书 agent 的同一租户。
DEFAULT_ACCOUNT = settings.tenancy.default_account

# 跨租户操作哨兵：set_current_account(TENANT_BYPASS) 让 ORM 自动过滤跳过，
# 用于 refresh_tokens（扫描全租户）、迁移脚本（截断全量）等场景。
TENANT_BYPASS = "__bypass__"


# ── 请求级当前租户（对话/MCP 路径用）─────────────────────────────────────────
# data API/MCP 工具被 openclaw 调用时不走 Host（同进程 ASGI）。租户来源优先级：
#   ① X-Account-Id 头（bind_account_context 注入，openclaw 若支持就走这条）；
#   ② 渲染/WebUI 路径显式 set_current_account（按 token/登录身份的 account）；
#   ③ 都没有 → 由 services.user_authz.resolve_dialog_account 按 open_id 反查 user_roles。
# contextvar 默认 None = **未显式设定**；current_account() 读时回落 DEFAULT_ACCOUNT。
# account_is_set() 让对话路径区分"已显式设(①②，信任)"与"未设(③，按 open_id 反查)"。
_current_account: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_account", default=None
)


def set_current_account(account_id: Optional[str]) -> None:
    """写当前请求的租户（须在 async 依赖里调，threadpool 的 set 不回传父 context）。"""
    _current_account.set(account_id or DEFAULT_ACCOUNT)


def current_account() -> str:
    """读当前请求的租户；未显式设定 → DEFAULT_ACCOUNT。"""
    return _current_account.get() or DEFAULT_ACCOUNT


def account_is_set() -> bool:
    """当前请求是否已显式设定租户（头注入或渲染/WebUI 显式设）。"""
    return _current_account.get() is not None


def current_account_or_none() -> Optional[str]:
    """读当前请求租户；未显式设定或 TENANT_BYPASS → None。

    供 ORM do_orm_execute 事件判断是否启用自动租户过滤：
    None = 不过滤（向后兼容 / bypass），str = 注入 WHERE account_id = ?。
    """
    v = _current_account.get()
    if v is None or v == TENANT_BYPASS:
        return None
    return v


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


def is_valid_account(account_id: str) -> bool:
    """校验 account_id 是否为系统已知合法租户（防店铺归属/路由落入未知租户）。

    合法集 = host_to_account 映射出现过的租户（= 部署拓扑放行的子域名，部署级事实源）
    + DEFAULT_ACCOUNT（主租户兜底）。
    """
    known = set(settings.tenancy.host_to_account.values())
    known.add(DEFAULT_ACCOUNT)
    return account_id in known
