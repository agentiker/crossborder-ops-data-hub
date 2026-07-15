"""默认范围选项组装 + platform:/country: 伪 scope 展开的回归测试。

覆盖：
- expand_scope 新前缀（平台组/区域组展开、空集 fail-closed）；
- list_scope_options：单平台单区域租户(全部店铺+逐店、无分组/后缀/"全量") vs 多平台多区域
  (平台组+区域组+单店带"· 平台/区域"后缀)；真子集命名 scope 追加、=全部的跳过；
- 绑定链路：set_binding 接受分组 scope_key（走同一 expand_scope 校验）。
"""
from __future__ import annotations

import pytest

from models.base_models import BusinessScope, PlatformToken
from services import scope_binding, scope_resolution, shop_directory
from services.scope_resolution import ScopeError, expand_scope, list_scope_options

ACC = "ecom-app"


def _use(session, monkeypatch):
    monkeypatch.setattr(scope_resolution, "SessionLocal", lambda: session)
    monkeypatch.setattr(shop_directory, "SessionLocal", lambda: session)
    monkeypatch.setattr(scope_binding, "SessionLocal", lambda: session)
    shop_directory.clear_shop_name_cache()


def _token(session, shop_id, *, name=None, platform="tiktok_shop", country="ID", account_id=ACC):
    session.add(PlatformToken(
        platform=platform, country=country, shop_id=shop_id, account_id=account_id,
        scope_key=f"platform={platform}|shop={shop_id}",
        token_payload={"seller_name": name} if name else {},
    ))


def _keys(opts):
    return [o["key"] for o in opts]


def _labels(opts):
    return {o["key"]: o["label"] for o in opts}


def test_single_platform_single_region_lists_all_plus_shops(session, monkeypatch):
    """单平台单区域(现 ecom 形态)：全部店铺 + 逐店(店名)，无分组/无后缀/无"全量"。"""
    _use(session, monkeypatch)
    _token(session, "s1", name="店一")
    _token(session, "s2", name="店二")
    session.commit()

    opts = list_scope_options(ACC)
    assert _keys(opts) == ["", "shop:s1", "shop:s2"]
    assert opts[0]["label"] == "全部店铺"
    assert opts[0]["description"] == "TikTok Shop 印尼，2 个店铺"
    assert opts[1]["label"] == "店一"  # 单一平台/区域 → 无后缀
    assert opts[2]["label"] == "店二"
    assert not any(k.startswith(("platform:", "country:")) for k in _keys(opts))
    assert all("全量" not in o["label"] for o in opts)


def test_multi_platform_multi_region_auto_groups(session, monkeypatch):
    """跨多平台+多区域：出平台组 + 区域组，单店带「· 平台/区域」后缀。"""
    _use(session, monkeypatch)
    _token(session, "t-id", name="TT印尼", platform="tiktok_shop", country="ID")
    _token(session, "t-my", name="TT马来", platform="tiktok_shop", country="MY")
    _token(session, "sp-id", name="虾皮印尼", platform="shopee", country="ID")
    session.commit()

    opts = list_scope_options(ACC)
    keys = _keys(opts)
    labels = _labels(opts)
    assert keys[0] == ""  # 全部店铺恒首项
    # 平台组(2平台) + 区域组(2区域)
    assert {"platform:tiktok_shop", "platform:shopee"} <= set(keys)
    assert {"country:ID", "country:MY"} <= set(keys)
    assert labels["platform:shopee"] == "全部 Shopee"
    assert labels["country:MY"] == "全部 马来"
    # 单店带平台/区域后缀
    assert labels["shop:t-id"] == "TT印尼 · TikTok Shop/印尼"
    assert labels["shop:sp-id"] == "虾皮印尼 · Shopee/印尼"


def test_expand_platform_and_country_pseudo_scope(session, monkeypatch):
    _use(session, monkeypatch)
    _token(session, "t-id", platform="tiktok_shop", country="ID")
    _token(session, "t-my", platform="tiktok_shop", country="MY")
    _token(session, "sp-id", platform="shopee", country="ID")
    session.commit()

    assert set(expand_scope("platform:tiktok_shop", account_id=ACC).shop_ids) == {"t-id", "t-my"}
    assert set(expand_scope("country:ID", account_id=ACC).shop_ids) == {"t-id", "sp-id"}
    # 空集 fail-closed
    with pytest.raises(ScopeError):
        expand_scope("platform:lazada", account_id=ACC)
    with pytest.raises(ScopeError):
        expand_scope("country:VN", account_id=ACC)


def test_named_subset_appended_full_set_skipped(session, monkeypatch):
    """真子集命名 scope 追加；恰好=全部的(tts-id-all)跳过，避免与「全部店铺」首项重复。"""
    _use(session, monkeypatch)
    _token(session, "s1", name="店一")
    _token(session, "s2", name="店二")
    session.add(BusinessScope(account_id=ACC, scope_key="tts-id-all", scope_name="全部店铺",
                              scope_type="shop_group", platform="tiktok_shop", country="ID",
                              shop_ids=["s1", "s2"], is_active=True))
    session.add(BusinessScope(account_id=ACC, scope_key="north", scope_name="北区",
                              scope_type="shop_group", platform="tiktok_shop", country="ID",
                              shop_ids=["s1"], is_active=True))
    session.commit()

    keys = _keys(list_scope_options(ACC))
    assert "north" in keys          # 真子集 → 追加
    assert "tts-id-all" not in keys  # =全部 → 跳过


def test_set_binding_accepts_group_scope_key(session, monkeypatch):
    """分组伪 scope 可直接作默认范围绑定（set_binding 走同一 expand_scope 校验）。"""
    _use(session, monkeypatch)
    _token(session, "t-id", platform="tiktok_shop", country="ID")
    _token(session, "t-my", platform="tiktok_shop", country="MY")
    session.commit()

    data = scope_binding.set_binding("ou_x", "platform:tiktok_shop", account_id=ACC)
    assert data["is_set"] and data["scope_key"] == "platform:tiktok_shop"
    got = scope_binding.get_binding("ou_x", account_id=ACC)
    assert got["scope_key"] == "platform:tiktok_shop"
    # 无效分组 → 拒绝落脏 binding
    with pytest.raises(ScopeError):
        scope_binding.set_binding("ou_x", "country:VN", account_id=ACC)
