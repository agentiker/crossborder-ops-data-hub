from __future__ import annotations

from datetime import date

from services.tiktok_policy_evidence import (
    build_search_queries,
    get_policy_references,
    is_allowed_official_url,
    score_candidate,
    search_official_policy_candidates,
    terms_for_fee_keys,
)


def test_allowed_official_url_whitelist():
    assert is_allowed_official_url("https://seller-id.tiktok.com/university/policy")
    assert is_allowed_official_url("https://seller-us.tiktok.com/university/essay?knowledge_id=1")
    assert is_allowed_official_url("https://ads.tiktok.com/help/article/x")
    assert not is_allowed_official_url("https://example.com/tiktok-policy")
    assert not is_allowed_official_url("https://seller-id.tiktok.com.evil.example/policy")


def test_terms_for_fee_keys_dedupes_in_order():
    terms = terms_for_fee_keys(["gmv_max_ad_fee_amount", "affiliate_ads_commission_amount"])
    assert "gmv max" in terms
    assert "shop ads commission" in terms
    assert len(terms) == len(set(t.lower() for t in terms))


def test_build_search_queries_targets_official_sources():
    qs = build_search_queries(
        country="ID",
        fee_keys=["affiliate_ads_commission_amount"],
        alert_date=date(2026, 7, 18),
    )
    joined = "\n".join(qs)
    assert "site:seller-id.tiktok.com" in joined
    assert "site:ads.tiktok.com" in joined
    assert "Indonesia" in joined
    assert "2026" in joined
    assert "shop ads commission" in joined


def test_policy_references_match_fee_terms_and_country():
    refs = get_policy_references(
        country="ID",
        fee_keys=["affiliate_ads_commission_amount"],
        alert_date=date(2026, 7, 18),
        candidates=[{
            "title": "Tentang tarif komisi Shop Ads",
            "url": "https://ads.tiktok.com/help/article/about-setting-different-affiliate-commission-rates-for-tiktok-shop-ads?lang=id",
            "source": "TikTok Business Help",
            "published_at": "2026-03-01",
            "summary": "Seller dapat menetapkan tarif komisi Shop Ads dan komisi afiliasi.",
        }],
    )
    assert len(refs) == 1
    assert refs[0]["confidence"] == "medium"
    matched = [t.lower() for t in refs[0]["matched_terms"]]
    assert "komisi shop ads" in matched or "shop ads commission" in matched


def test_policy_references_searches_when_candidates_not_injected(monkeypatch):
    def fake_search(*, country, fee_keys, alert_date=None, timeout=8):
        assert country == "ID"
        assert fee_keys == ["affiliate_ads_commission_amount"]
        return [{
            "title": "Shop Ads commission update",
            "url": "https://ads.tiktok.com/help/article/shop-ads-commission?lang=id",
            "source": "TikTok Business Help",
            "summary": "shop ads commission for TikTok Shop sellers",
        }]

    monkeypatch.setattr(
        "services.tiktok_policy_evidence.search_official_policy_candidates",
        fake_search,
    )
    refs = get_policy_references(
        country="ID",
        fee_keys=["affiliate_ads_commission_amount"],
        alert_date=date(2026, 7, 18),
    )
    assert len(refs) == 1
    assert refs[0]["url"].startswith("https://ads.tiktok.com")


def test_search_official_policy_candidates_parses_duckduckgo(monkeypatch):
    html = """
    <html><body>
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fads.tiktok.com%2Fhelp%2Farticle%2Fshop-ads%3Flang%3Did">Shop Ads commission</a>
      <a class="result__snippet">TikTok Shop Ads commission for sellers.</a>
      <a class="result__a" href="https://example.com/blog">Not official</a>
    </body></html>
    """

    class Resp:
        text = html
        def raise_for_status(self):
            return None

    monkeypatch.setattr("services.tiktok_policy_evidence.requests.get", lambda *a, **k: Resp())
    out = search_official_policy_candidates(
        country="ID",
        fee_keys=["affiliate_ads_commission_amount"],
        alert_date=date(2026, 7, 18),
    )
    assert len(out) == 1
    assert out[0]["url"] == "https://ads.tiktok.com/help/article/shop-ads?lang=id"
    assert out[0]["source"] == "TikTok Business Help"


def test_policy_references_drop_untrusted_or_weak_candidates():
    refs = get_policy_references(
        country="ID",
        fee_keys=["dynamic_commission_amount"],
        alert_date=date(2026, 7, 18),
        candidates=[
            {
                "title": "Dynamic commission rumor",
                "url": "https://example.com/blog",
                "source": "Blog",
                "summary": "dynamic commission",
            },
            {
                "title": "TikTok unrelated help",
                "url": "https://ads.tiktok.com/help/article/unrelated?lang=id",
                "source": "TikTok Business Help",
                "summary": "shipping templates",
            },
        ],
    )
    assert refs == []


def test_policy_references_drop_generic_gmv_max_intro():
    refs = get_policy_references(
        country="ID",
        fee_keys=["gmv_max_ad_fee_amount"],
        alert_date=date(2026, 7, 18),
        candidates=[{
            "title": "Tentang Product GMV Max",
            "url": "https://ads.tiktok.com/help/article/about-product-gmv-max?lang=id",
            "source": "TikTok Business Help",
            "published_at": "2026-07-01",
            "summary": "Product GMV Max adalah solusi automasi untuk TikTok Shop Ads yang membantu mengoptimalkan total ROI.",
        }],
    )
    assert refs == []


def test_policy_references_keep_commission_specific_gmv_max_doc():
    refs = get_policy_references(
        country="ID",
        fee_keys=["gmv_max_ad_fee_amount"],
        alert_date=date(2026, 7, 18),
        candidates=[{
            "title": "Cara Menyiapkan Penghematan Komisi di TikTok Shop",
            "url": "https://ads.tiktok.com/help/article/how-to-set-up-commission-savings-on-tiktok-shop?lang=id",
            "source": "TikTok Business Help",
            "published_at": "2026-04-01",
            "summary": "Penghematan komisi adalah fitur yang mengurangi komisi platform e-commerce saat menjalankan kampanye GMV Max.",
        }],
    )
    assert len(refs) == 1


def test_score_candidate_prefers_recent_matching_official_reference():
    old = score_candidate(
        {
            "title": "Shop Ads commission",
            "url": "https://ads.tiktok.com/help/article/x?lang=id",
            "published_at": "2024-01-01",
        },
        country="ID",
        fee_keys=["affiliate_ads_commission_amount"],
        alert_date=date(2026, 7, 18),
    )
    recent = score_candidate(
        {
            "title": "Shop Ads commission",
            "url": "https://ads.tiktok.com/help/article/x?lang=id",
            "published_at": "2026-07-01",
        },
        country="ID",
        fee_keys=["affiliate_ads_commission_amount"],
        alert_date=date(2026, 7, 18),
    )
    assert recent > old
