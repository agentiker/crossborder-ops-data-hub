"""部署前环境预检（preflight）。

在 `deploy.sh` 之前跑一遍，把「能在启动前查出来的配置错」一次性暴露，避免到
bootstrap / 授权 / 同步那几步才发现漏配（本项目投产时最贵的坑都属此类：
FEISHU_OAUTH__APPS 漏配导致登录 500、租户名实不符导致 bootstrap 落错 account）。

只读 `core.config.settings`（已加载 .env）+ 连一次本地 DB，不打任何外部接口、不改任何状态。
聚焦三类、不重复 pydantic 的类型校验：
  1. 必填密钥非空（DB / TikTok / 内部 token / 合规三件套 / 看板 / 飞书 OAuth）；
  2. 单租户三处对齐（default_account 同时在 FEISHU_OAUTH__APPS 有凭据、在 HOST_TO_ACCOUNT 有子域名）；
  3. 连通性（本地 DB 可连、openclaw_bin 路径存在）。

退出码：0 = 无 ERROR（可部署）；1 = 有 ERROR；2 = 运行异常。
`--strict` 时 WARN 也计入失败（CI / 部署门禁用）。

用法：
  uv run python -m scripts.preflight
  uv run python -m scripts.preflight --strict
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

OK, WARN, ERR = "ok", "warn", "err"
_MARK = {OK: "✓", WARN: "⚠", ERR: "✗"}


class Checks:
    """收集分组检查结果，按 group 顺序汇总打印。"""

    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    def ok(self, group: str, msg: str) -> None:
        self.items.append((OK, group, msg))

    def warn(self, group: str, msg: str) -> None:
        self.items.append((WARN, group, msg))

    def err(self, group: str, msg: str) -> None:
        self.items.append((ERR, group, msg))

    def need(self, value, group: str, name: str, hint: str = "") -> bool:
        """必填非空：非空 → ok 返回 True；空 → err 返回 False。"""
        if str(value or "").strip():
            self.ok(group, name)
            return True
        self.err(group, f"{name} 未配置" + (f"（{hint}）" if hint else ""))
        return False


def _check_fernet_key(checks: Checks, group: str, key: str) -> None:
    if not str(key or "").strip():
        checks.err(group, "TOKEN_ENCRYPTION_KEY 未配置（token 写库 fail-closed，授权前必配）")
        return
    try:
        from cryptography.fernet import Fernet

        Fernet(key.encode())
        checks.ok(group, "TOKEN_ENCRYPTION_KEY（Fernet 合法）")
    except Exception:  # noqa: BLE001
        checks.err(group, "TOKEN_ENCRYPTION_KEY 非合法 Fernet key（须 urlsafe base64 32B / 44 字符）")


def _check_tenant_alignment(checks: Checks, settings) -> None:
    """单租户名实对齐：default_account 须三处一致，否则 bootstrap/授权落错租户。"""
    g = "单租户对齐"
    default = settings.tenancy.default_account
    checks.ok(g, f"DEFAULT_ACCOUNT = {default}")

    # ① 飞书 OAuth 凭据（漏配 → /app 登录报「飞书应用未正确配置」500）
    cred = settings.feishu_oauth.credential(default)
    if str(cred.app_id or "").strip() and str(cred.app_secret or "").strip():
        checks.ok(g, f"FEISHU_OAUTH__APPS 有 {default} 的 app_id/app_secret")
    else:
        checks.err(
            g,
            f"FEISHU_OAUTH__APPS 缺 {default} 的凭据（/app 登录会 500，bootstrap 卡死）",
        )

    # ② 子域名映射（缺失仍可裸域回落，但单客户独立部署应显式登记 → WARN）
    mapped = set(settings.tenancy.host_to_account.values())
    if default in mapped:
        host = next(h for h, a in settings.tenancy.host_to_account.items() if a == default)
        checks.ok(g, f"HOST_TO_ACCOUNT 有 {host} → {default}")
    else:
        checks.warn(
            g,
            f"HOST_TO_ACCOUNT 无映射到 {default}（裸域回落可用，但建议显式登记子域名）",
        )


def _check_db(checks: Checks, settings) -> None:
    g = "连通性"
    try:
        from sqlalchemy import text

        from core.db import SessionLocal

        s = SessionLocal()
        try:
            s.execute(text("SELECT 1"))
        finally:
            s.close()
        checks.ok(g, f"DB 可连（{settings.db.host}:{settings.db.port}/{settings.db.database}）")
    except Exception as exc:  # noqa: BLE001
        checks.err(g, f"DB 连不上（{settings.db.host}:{settings.db.port}）：{exc}")


def _check_openclaw_bin(checks: Checks, settings) -> None:
    g = "连通性"
    b = str(settings.openclaw_bin or "").strip()
    if not b:
        checks.warn(g, "OPENCLAW_BIN 未配置（监控告警直投飞书会找不到 openclaw）")
        return
    found = (os.path.isabs(b) and os.path.exists(b)) or shutil.which(b)
    if found:
        checks.ok(g, f"openclaw_bin 可用（{b}）")
    else:
        checks.warn(g, f"openclaw_bin 不存在/不在 PATH（{b}）；systemd 下须用绝对路径")


def run(strict: bool = False) -> int:
    from core.config import settings

    c = Checks()

    # ── 必填密钥 ──
    g = "数据库 / API"
    c.need(settings.db.password, g, "DB__PASSWORD")
    c.need(settings.db.database, g, "DB__DATABASE")
    c.need(settings.api.internal_token, g, "API__INTERNAL_TOKEN", "openclaw DATA_HUB_TOKEN 须与之一致")

    g = "TikTok"
    c.need(settings.tiktok.app_key, g, "TIKTOK__APP_KEY")
    c.need(settings.tiktok.app_secret, g, "TIKTOK__APP_SECRET")

    g = "看板 / 飞书 OAuth"
    c.need(settings.dashboard.link_secret, g, "DASHBOARD__LINK_SECRET")
    c.need(settings.dashboard.public_base_url, g, "DASHBOARD__PUBLIC_BASE_URL")
    c.need(settings.feishu_oauth.session_secret, g, "FEISHU_OAUTH__SESSION_SECRET")
    if not settings.feishu_oauth.cookie_secure:
        c.warn(g, "FEISHU_OAUTH__COOKIE_SECURE=false（生产 HTTPS 应为 true）")

    g = "上线合规"
    _check_fernet_key(c, g, settings.token_encryption_key)
    c.need(settings.backup_gpg_passphrase, g, "BACKUP_GPG_PASSPHRASE", "每日加密备份口令")
    if settings.audit_anchor_enabled:
        c.need(settings.audit_anchor_account, g, "AUDIT_ANCHOR_ACCOUNT", "锚定开启但收件人空")
        c.need(settings.audit_anchor_open_id, g, "AUDIT_ANCHOR_OPEN_ID", "锚定开启但收件人空")
    else:
        c.warn(g, "AUDIT_ANCHOR_ENABLED=false（空库阶段可接受，正式运营前置 true）")

    g = "Web 对话 LLM"
    if str(settings.llm.api_key or "").strip() and str(settings.llm.model or "").strip():
        c.ok(g, f"provider={settings.llm.provider} model={settings.llm.model}")
    else:
        c.warn(g, "LLM api_key/model 未配（仅影响 Web 对话，同步/告警不受影响）")

    # ── 单租户对齐 ──
    _check_tenant_alignment(c, settings)

    # ── 连通性 ──
    _check_db(c, settings)
    _check_openclaw_bin(c, settings)

    # ── 汇总打印 ──
    print("══ 部署前预检 (preflight) ══")
    last_group = None
    for level, group, msg in c.items:
        if group != last_group:
            print(f"\n[{group}]")
            last_group = group
        print(f"  {_MARK[level]} {msg}")

    n_err = sum(1 for lv, _, _ in c.items if lv == ERR)
    n_warn = sum(1 for lv, _, _ in c.items if lv == WARN)
    failed = n_err > 0 or (strict and n_warn > 0)
    verdict = "失败" if failed else "通过"
    print(f"\n结果：{n_err} ERROR / {n_warn} WARN → {verdict}"
          + ("（--strict：WARN 计入失败）" if strict and n_warn else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="部署前环境预检（有 ERROR 非零退出）")
    p.add_argument("--strict", action="store_true", help="WARN 也计入失败（CI/部署门禁用）")
    try:
        raise SystemExit(run(strict=p.parse_args().strict))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 预检运行异常: {exc}", file=sys.stderr)
        raise SystemExit(2)
