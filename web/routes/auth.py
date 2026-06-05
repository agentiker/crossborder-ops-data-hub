"""TikTok Shop OAuth authentication routes."""

import logging

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import HTMLResponse

from core.db import init_db
from platforms.tiktok_shop.client import TikTokShopClient

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/callback/tiktok")
async def tiktok_callback(
    code: str = Query(..., description="TikTok OAuth 授权码"),
    state: str = Query(default="", description="状态参数"),
):
    """TikTok Shop OAuth 回调端点

    商家授权后，TikTok会重定向到此端点，携带授权码。
    系统使用授权码换取access_token和refresh_token。
    """
    if not code:
        raise HTTPException(status_code=400, detail="缺少授权码 (code)")

    # 初始化数据库
    init_db()

    try:
        client = TikTokShopClient(auto_load_token=False)

        result = client.authenticate(code)
        data = result.get("data", {})

        return {
            "success": True,
            "message": "授权成功",
            "data": {
                "scope_key": client.scope_key,
                "shop_cipher": client.shop_cipher,
                "access_token_expire_in": data.get("access_token_expire_in"),
                "refresh_token_expire_in": data.get("refresh_token_expire_in"),
                "state": state,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"授权失败: {str(e)}")


@router.get("/callback/tiktok/html")
async def tiktok_callback_html(
    code: str = Query(..., description="TikTok OAuth 授权码"),
    state: str = Query(default="", description="状态参数"),
):
    """TikTok Shop OAuth 回调端点 (HTML 版本)

    返回HTML页面，适合浏览器直接访问。
    """
    logger.info(f"[OAuth回调] 收到授权码: code={code[:20]}..., state={state}")

    if not code:
        logger.warning("[OAuth回调] 缺少授权码")
        return HTMLResponse(
            content="<html><body><h1>错误</h1><p>缺少授权码</p></body></html>",
            status_code=400,
        )

    # 初始化数据库
    init_db()

    try:
        client = TikTokShopClient(auto_load_token=False)

        logger.info("[OAuth回调] 开始用授权码换取Token...")
        result = client.authenticate(code)
        data = result.get("data", {})
        logger.info(f"[OAuth回调] Token换取成功! shop_cipher={client.shop_cipher}, scope_key={client.scope_key}")

        html_content = f"""
        <html>
        <head><title>TikTok Shop 授权成功</title></head>
        <body>
            <h1>授权成功!</h1>
            <ul>
                <li><strong>Scope Key:</strong> {client.scope_key}</li>
                <li><strong>Shop Cipher:</strong> {client.shop_cipher or 'N/A'}</li>
                <li><strong>Access Token 有效期:</strong> {data.get('access_token_expire_in', 'N/A')} 秒</li>
                <li><strong>Refresh Token 有效期:</strong> {data.get('refresh_token_expire_in', 'N/A')} 秒</li>
            </ul>
            <p>Token 已保存到数据库，可以关闭此页面。</p>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)
    except Exception as e:
        logger.error(f"[OAuth回调] 授权失败: {e}", exc_info=True)
        html_content = f"""
        <html>
        <head><title>TikTok Shop 授权失败</title></head>
        <body>
            <h1>授权失败</h1>
            <p>错误信息: {str(e)}</p>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content, status_code=500)
