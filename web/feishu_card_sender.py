"""飞书 IM 卡片发送层（后端直投 interactive 卡片）。

日报/周报改造：不再让 openclaw agent 输出 markdown 文字投递，而是后端用
summary 真实数字拼 v2 CardKit 卡片，直接调飞书 IM API 发 `msg_type=interactive`。

凭证复用 data-hub 自己的飞书 app（`settings.feishu_oauth.credential(account_id)`，
与看板 OAuth 同一个 app：ecom-app / ecom-app-gtl），不依赖 openclaw 的凭证。

tenant_access_token 有效期约 2h，进程内按 account_id 缓存 + 提前 5 分钟过期重取，
避免每次发消息都换 token（触发飞书频控）。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import requests

from core.config import settings

logger = logging.getLogger("web.feishu_card_sender")

_TENANT_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_SEND_MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"

# account_id -> {"token": str, "exp": epoch_seconds}
_token_cache: dict[str, dict] = {}
# 提前过期余量：token 名义 ~7200s，留 300s 缓冲重取。
_TOKEN_SKEW = 300


class FeishuSendError(RuntimeError):
    """飞书 API 返回非 0 code 或网络异常。"""


def get_tenant_access_token(account_id: str) -> str:
    """取（并缓存）指定租户飞书 app 的 tenant_access_token。"""
    now = time.time()
    cached = _token_cache.get(account_id)
    if cached and cached["exp"] - _TOKEN_SKEW > now:
        return cached["token"]

    cred = settings.feishu_oauth.credential(account_id)
    if not cred.app_id or not cred.app_secret:
        raise FeishuSendError(
            f"飞书 app 凭证未配置（account_id={account_id}）：检查 FEISHU_OAUTH__APP_ID/SECRET 或 __APPS"
        )
    try:
        resp = requests.post(
            _TENANT_TOKEN_URL,
            json={"app_id": cred.app_id, "app_secret": cred.app_secret},
            timeout=15,
        )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise FeishuSendError(f"取 tenant_access_token 网络异常：{exc}") from exc
    if data.get("code") != 0:
        raise FeishuSendError(
            f"取 tenant_access_token 失败 code={data.get('code')} msg={data.get('msg')}"
        )
    token = data["tenant_access_token"]
    expire = int(data.get("expire", 7200))
    _token_cache[account_id] = {"token": token, "exp": now + expire}
    return token


def send_interactive_card(account_id: str, open_id: str, card: dict) -> str:
    """向 open_id 发一张 interactive 卡片，返回 message_id；失败抛 FeishuSendError。"""
    token = get_tenant_access_token(account_id)
    payload = {
        "receive_id": open_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    try:
        resp = requests.post(
            _SEND_MESSAGE_URL,
            params={"receive_id_type": "open_id"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=20,
        )
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise FeishuSendError(f"发送卡片网络异常：{exc}") from exc
    if data.get("code") != 0:
        # 记录飞书返回码，便于排查卡片 JSON 字段错（如 CardKit 200570）
        raise FeishuSendError(
            f"发送卡片失败 code={data.get('code')} msg={data.get('msg')} account={account_id}"
        )
    msg_id: Optional[str] = (data.get("data") or {}).get("message_id")
    logger.info("interactive card sent: account=%s open_id=%s message_id=%s",
                account_id, open_id, msg_id)
    return msg_id or ""
