"""本地运维 CLI：为某个飞书 open_id 签一条看板链接（HMAC 签名 token）。

**仅本地使用**，不起 HTTP。用于本机自测（看板已删 127.0.0.1 无 token 旁路，自测靠它签 token）
或运维临时给某账号补发链接。密钥取 .env 的 DASHBOARD__LINK_SECRET。

用法：
  uv run python -m scripts.dashboard_link --open-id ou_xxx
  uv run python -m scripts.dashboard_link --open-id ou_xxx --ttl 3600
  uv run python -m scripts.dashboard_link --open-id ou_xxx --base http://127.0.0.1:8000
  uv run python -m scripts.dashboard_link --open-id ou_xxx --ttl -1   # 签一个已过期 token（测 401）
"""
from __future__ import annotations

import argparse
import sys

from core.config import settings
from web.signed_link import make_token


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="为飞书 open_id 签发看板链接")
    parser.add_argument("--open-id", dest="open_id", required=True, help="飞书 open_id（ou_xxx）")
    parser.add_argument(
        "--ttl",
        type=int,
        default=settings.dashboard.token_ttl_seconds,
        help="有效期（秒），默认取配置 token_ttl_seconds；传负值可签出已过期 token 测 401",
    )
    parser.add_argument(
        "--base",
        default=None,
        help="链接根地址，默认取 DASHBOARD__PUBLIC_BASE_URL，未配则 http://127.0.0.1:8000",
    )
    args = parser.parse_args(argv)

    try:
        token = make_token(args.open_id, ttl=args.ttl)
    except (RuntimeError, ValueError) as exc:
        print(f"签发失败：{exc}", file=sys.stderr)
        return 2

    base = args.base or settings.dashboard.public_base_url or "http://127.0.0.1:8000"
    print(base.rstrip("/") + "/dashboard?t=" + token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
