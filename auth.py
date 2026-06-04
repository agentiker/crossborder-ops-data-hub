"""
TikTok Shop OAuth 授权脚本
首次使用时运行，用授权码换取 Token 并持久化到数据库

用法:
    python auth.py <auth_code>
"""
import argparse

from core.db import init_db
from platforms.tiktok_shop.client import TikTokShopClient


def main():
    parser = argparse.ArgumentParser(description="TikTok Shop OAuth 授权")
    parser.add_argument("auth_code", help="TikTok Shop OAuth 一次性授权码")
    parser.add_argument("--country", default="GLOBAL", help="国家或站点代码")
    parser.add_argument("--shop-id", default=None, help="TikTok Shop 店铺 ID")
    parser.add_argument("--seller-id", default=None, help="卖家 ID")
    parser.add_argument("--account-id", default=None, help="补充账号 ID")
    args = parser.parse_args()

    # 初始化数据库表（如已存在则跳过）
    init_db()

    client = TikTokShopClient(
        country=args.country,
        shop_id=args.shop_id,
        seller_id=args.seller_id,
        account_id=args.account_id,
        auto_load_token=False,
    )

    result = client.authenticate(args.auth_code)
    print("授权成功!")
    data = result.get("data", {})
    expires_in = data.get("expires_in") or data.get("access_token_expire_in")
    print(f"  scope_key: {client.scope_key}")
    print(f"  access_token 有效期: {expires_in} 秒")
    print("  Token 已持久化到 MySQL platform_tokens 表")


if __name__ == "__main__":
    main()
