"""products/search 默认只拉 ACTIVATE 在售商品的回归锁。

草稿/下架/冻结商品会污染经营分析（动销率分母、断货告警、商品健康度），故同步入库前
就在 API 请求体里用 status 过滤。本测试锁定 iter_products 默认传 {"status":"ACTIVATE"}，
并保留 status=None 拉全量的逃生口。
"""
from __future__ import annotations

import time

from platforms.tiktok_shop.client import TikTokShopClient


def _make_client():
    client = TikTokShopClient(auto_load_token=False)
    client.access_token = "acc"
    client.refresh_token = "ref"
    client.token_expire_at = time.time() + 10_000
    return client


def _capture(monkeypatch, client):
    calls = []

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        calls.append({"path": path, "data": data})
        return {"code": 0, "data": {"products": []}}  # 单页空，立即停

    monkeypatch.setattr(client, "request", fake_request)
    return calls


def test_list_products_defaults_to_activate_only(monkeypatch):
    client = _make_client()
    calls = _capture(monkeypatch, client)

    client.list_products()

    assert calls and calls[0]["data"] == {"status": "ACTIVATE"}


def test_list_products_status_none_pulls_all(monkeypatch):
    client = _make_client()
    calls = _capture(monkeypatch, client)

    client.list_products(status=None)

    assert calls and calls[0]["data"] == {}
