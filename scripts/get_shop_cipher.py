"""Get shop_cipher from TikTok Shop API."""
import hmac
import hashlib
import json
import time
from platforms.tiktok_shop.client import TikTokShopClient

client = TikTokShopClient()

path = "/authorization/202309/shops"
params = {"app_key": client.app_key, "timestamp": str(int(time.time()))}

# 手动算签名 - 路径去掉前导斜杠
sign_params = {k: v for k, v in params.items() if k != "sign" and v is not None}
sorted_params = "".join(f"{k}{v}" for k, v in sorted(sign_params.items()))
sign_path = path.lstrip("/")
sign_str = f"{client.app_secret}{sign_path}{sorted_params}{client.app_secret}"
sign = hmac.new(client.app_secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest().upper()

print(f"sign_path: {sign_path}")
print(f"sign_str: {sign_str}")

params["sign"] = sign
host = "open-api-sandbox.tiktokglobalshop.com"
headers = {
    "x-tts-access-token": client.access_token or "",
    "Content-Type": "application/json",
    "Host": host,
}

# 用真实IP直连，绕过DNS污染
real_ip = "18.167.100.12"
url = f"https://{real_ip}{path}"
resp = client.session.get(url, params=params, headers=headers, timeout=30, verify=False, proxies={"http": None, "https": None})
print(f"Status: {resp.status_code}")
print(f"Response: {resp.text}")
