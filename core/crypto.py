"""Token 字段透明加解密（plan 审计合规第 5 节）。

`platform_tokens.access_token/refresh_token` 此前明文存 DB，不满足 TikTok 审核的
数据安全要求。本模块用 SQLAlchemy `TypeDecorator` 在 ORM 边界统一拦截：写时 Fernet
加密、读时解密——业务代码（save_token/load_token 的 base+tiktok 两份共 4 处分支）零改动，
只需把列类型从 `Text` 换成 `EncryptedText`。

密钥来自 `settings.token_encryption_key`（.env 的 TOKEN_ENCRYPTION_KEY，Fernet key）：
- 未配置时**写非空值直接 raise**（fail closed，防止裸明文静默落库）；
- 读到密文（带版本前缀）→ 解密，密文但无 key → raise；
- 读到无前缀的存量明文 → 原样返回（迁移兼容，scripts/migrate_encrypt_tokens.py 转存量）。

密文带版本前缀 `fcr1:`，为将来多 key 轮换留口。空值（None/""）不加密透传——
save_token 的「空 refresh_token / 空 cipher 不抹库」逻辑在 Python 层判断，不受影响。
"""
from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from core.config import settings

# 密文版本前缀：标识本列值已用 v1（Fernet）加密。无此前缀 = 存量明文。
_PREFIX = "fcr1:"

# Fernet 实例进程级缓存（key 来自 settings，运行期不变）。
_fernet = None


def _get_fernet():
    """懒加载 Fernet；未配置 key 返回 None（由调用方决定 fail closed）。"""
    global _fernet
    if _fernet is not None:
        return _fernet
    key = (settings.token_encryption_key or "").strip()
    if not key:
        return None
    from cryptography.fernet import Fernet

    _fernet = Fernet(key.encode())
    return _fernet


def encrypt_token(plaintext: str) -> str:
    """加密非空明文 → `fcr1:<token>`。未配 key → raise（fail closed）。"""
    f = _get_fernet()
    if f is None:
        raise RuntimeError(
            "TOKEN_ENCRYPTION_KEY 未配置，拒绝以明文写入 token（fail closed）。"
            "请在 .env 设置 Fernet 密钥后重试。"
        )
    return _PREFIX + f.encrypt(plaintext.encode()).decode()


def decrypt_token(value: str) -> str:
    """解密 `fcr1:<token>`；无前缀的存量明文原样返回。"""
    if not value.startswith(_PREFIX):
        return value  # 存量明文，迁移兼容
    f = _get_fernet()
    if f is None:
        raise RuntimeError(
            "读到密文但 TOKEN_ENCRYPTION_KEY 未配置，无法解密。请配置正确的 Fernet 密钥。"
        )
    return f.decrypt(value[len(_PREFIX):].encode()).decode()


def is_encrypted(value) -> bool:
    """判断 DB 中的值是否已是本模块密文（迁移脚本用，区分存量明文）。"""
    return isinstance(value, str) and value.startswith(_PREFIX)


class EncryptedText(TypeDecorator):
    """透明加密的 Text 列：写时 Fernet 加密、读时解密，空值（None/""）透传。"""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return value
        return encrypt_token(value)

    def process_result_value(self, value, dialect):
        if value is None or value == "":
            return value
        return decrypt_token(value)
