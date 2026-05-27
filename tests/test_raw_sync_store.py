from models.base_models import RawAPIResponse, SyncCursor
from services.raw_sync_store import load_sync_cursor, save_raw_api_response, save_sync_cursor


def test_save_raw_api_response_is_idempotent_by_request_window(session):
    first = save_raw_api_response(
        session,
        platform="tiktok_shop",
        shop_id="shop-1",
        endpoint="/inventory",
        request_key="2026-01-01T00:00:00Z",
        payload={"items": [1]},
    )
    second = save_raw_api_response(
        session,
        platform="tiktok_shop",
        shop_id="shop-1",
        endpoint="/inventory",
        request_key="2026-01-01T00:00:00Z",
        payload={"items": [1, 2]},
        status_code=206,
    )
    session.commit()

    assert first.id == second.id
    assert session.query(RawAPIResponse).count() == 1
    assert session.query(RawAPIResponse).one().response_payload == {"items": [1, 2]}
    assert session.query(RawAPIResponse).one().http_status == 206


def test_save_and_load_sync_cursor_upserts_by_resource_scope(session):
    save_sync_cursor(
        session,
        platform="tiktok_shop",
        shop_id="shop-1",
        resource="inventory",
        cursor_value="cursor-1",
    )
    save_sync_cursor(
        session,
        platform="tiktok_shop",
        shop_id="shop-1",
        resource="inventory",
        cursor_value="cursor-2",
    )
    save_sync_cursor(
        session,
        platform="tiktok_shop",
        shop_id="shop-2",
        resource="inventory",
        cursor_value="other-shop-cursor",
    )
    session.commit()

    assert session.query(SyncCursor).count() == 2
    assert (
        load_sync_cursor(
            session,
            platform="tiktok_shop",
            shop_id="shop-1",
            resource="inventory",
        ).cursor
        == "cursor-2"
    )
