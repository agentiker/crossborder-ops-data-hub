"""Pending-fulfillment snapshot persistence (idempotent upsert + stale delete).

待发货是"当前状态集合"，不是事件流：每次同步用本批快照覆盖本 scope —— 在快照里的单
upsert，不在快照里的旧行删除（发货后即从待发货态离开）。故只认 core.domain 的 DomainOrder，
不 import platforms。删除严格限定本次同步的 scope，多店互不误删；空快照=清空本店。
"""
from __future__ import annotations

from typing import Optional

from core.domain import DomainOrder
from models.base_models import PendingFulfillment
from services.scoping import build_pending_fulfillment_key


def replace_pending_fulfillments(
    session,
    orders: list[DomainOrder],
    *,
    platform: str = "tiktok_shop",
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    raw_response_id: Optional[int] = None,
) -> tuple[int, int]:
    """Upsert the snapshot and delete this scope's rows that are no longer pending.

    Returns (upserted_count, removed_count).
    """
    seen_keys: set[str] = set()
    for order in orders:
        key = build_pending_fulfillment_key(
            platform=platform,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            order_id=order.order_id,
        )
        seen_keys.add(key)

        # 列表展示用的派生字段（每条 line_item = 一件）
        item_count = len(order.line_items)
        first_product_name = order.line_items[0].product_name if order.line_items else None

        existing = (
            session.query(PendingFulfillment)
            .filter_by(idempotency_key=key)
            .first()
        )
        if existing:
            existing.order_status = order.order_status
            existing.tts_sla_time = order.tts_sla_time
            existing.rts_sla_time = order.rts_sla_time
            existing.shipping_due_time = order.shipping_due_time
            existing.collection_due_time = order.collection_due_time
            existing.delivery_option_name = order.delivery_option_name
            existing.is_cod = order.is_cod
            existing.total_amount = order.total_amount
            existing.currency = order.currency
            existing.item_count = item_count
            existing.first_product_name = first_product_name
            existing.warehouse_id = order.warehouse_id
            existing.create_time = order.create_time
            existing.paid_time = order.paid_time
            existing.update_time = order.update_time
            existing.raw_response_id = raw_response_id
        else:
            session.add(
                PendingFulfillment(
                    platform=platform,
                    country=country,
                    shop_id=shop_id,
                    seller_id=seller_id,
                    account_id=account_id,
                    idempotency_key=key,
                    order_id=order.order_id,
                    order_status=order.order_status,
                    tts_sla_time=order.tts_sla_time,
                    rts_sla_time=order.rts_sla_time,
                    shipping_due_time=order.shipping_due_time,
                    collection_due_time=order.collection_due_time,
                    delivery_option_name=order.delivery_option_name,
                    is_cod=order.is_cod,
                    total_amount=order.total_amount,
                    currency=order.currency,
                    item_count=item_count,
                    first_product_name=first_product_name,
                    warehouse_id=order.warehouse_id,
                    create_time=order.create_time,
                    paid_time=order.paid_time,
                    update_time=order.update_time,
                    raw_response_id=raw_response_id,
                )
            )

    # 删本 scope 内不在本批快照中的行（== None 自动转 IS NULL，匹配可空 scope 列）。
    # 空快照（seen_keys 为空）= 本店当前 0 单待发货，应清空本 scope 全部旧行。
    stale_q = session.query(PendingFulfillment).filter(
        PendingFulfillment.platform == platform,
        PendingFulfillment.country == country,
        PendingFulfillment.shop_id == shop_id,
        PendingFulfillment.seller_id == seller_id,
        PendingFulfillment.account_id == account_id,
    )
    if seen_keys:
        stale_q = stale_q.filter(PendingFulfillment.idempotency_key.notin_(seen_keys))
    removed = stale_q.delete(synchronize_session=False)

    session.flush()
    return len(orders), removed
