"""TikTok Shop OAuth authentication routes."""

import logging

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import HTMLResponse

from core.audit_context import set_audit_actor
from core.db import init_db
from core.tenancy import DEFAULT_ACCOUNT
from platforms.tiktok_shop.client import TikTokShopClient
from services.audit import log_audit_event_safe

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
    # 审计身份（plan 审计合规第 3 节）：authenticate 的 _auth_get 在 api_call_logs 标 oauth。
    set_audit_actor(source="oauth")

    try:
        # account_id=DEFAULT_ACCOUNT 决定 save_token 写进 platform_tokens.account_id
        # 列（隔离命脉，discover_single_shop 读它重建 scope）。Option C 后该值不再进
        # scope_key 串，但仍必须用它把这家店的租户归属落成 ecom-app。
        client = TikTokShopClient(auto_load_token=False, account_id=DEFAULT_ACCOUNT)

        result = client.authenticate(code)
        data = result.get("data", {})

        log_audit_event_safe(
            event_type="authorization", event_action="oauth.callback", actor_source="oauth",
            account_id=DEFAULT_ACCOUNT, target=client.shop_id,
            summary="TikTok OAuth 授权成功",
            after={
                "scope_key": client.scope_key,
                "has_shop_cipher": bool(client.shop_cipher),
                "granted_scopes": data.get("granted_scopes"),
                "access_token_expire_in": data.get("access_token_expire_in"),
            },
        )
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
        log_audit_event_safe(
            event_type="authorization", event_action="oauth.callback", actor_source="oauth",
            account_id=DEFAULT_ACCOUNT, summary=f"TikTok OAuth 授权失败: {str(e)[:200]}",
        )
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
    # 审计身份（plan 审计合规第 3 节）：authenticate 的 _auth_get 在 api_call_logs 标 oauth。
    set_audit_actor(source="oauth")

    try:
        # account_id=DEFAULT_ACCOUNT 决定 save_token 写进 platform_tokens.account_id
        # 列（隔离命脉，discover_single_shop 读它重建 scope）。Option C 后该值不再进
        # scope_key 串，但仍必须用它把这家店的租户归属落成 ecom-app。
        client = TikTokShopClient(auto_load_token=False, account_id=DEFAULT_ACCOUNT)

        logger.info("[OAuth回调] 开始用授权码换取Token...")
        result = client.authenticate(code)
        data = result.get("data", {})
        logger.info(f"[OAuth回调] Token换取成功! shop_cipher={client.shop_cipher}, scope_key={client.scope_key}")
        log_audit_event_safe(
            event_type="authorization", event_action="oauth.callback", actor_source="oauth",
            account_id=DEFAULT_ACCOUNT, target=client.shop_id,
            summary="TikTok OAuth 授权成功（HTML 回调）",
            after={
                "scope_key": client.scope_key,
                "has_shop_cipher": bool(client.shop_cipher),
                "granted_scopes": data.get("granted_scopes"),
            },
        )

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
        log_audit_event_safe(
            event_type="authorization", event_action="oauth.callback", actor_source="oauth",
            account_id=DEFAULT_ACCOUNT, summary=f"TikTok OAuth 授权失败（HTML 回调）: {str(e)[:200]}",
        )
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
