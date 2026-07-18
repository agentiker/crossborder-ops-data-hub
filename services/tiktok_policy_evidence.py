"""TikTok 官方公开资料搜索与候选匹配。

本模块只做告警后的参考资料增强，不参与费率异常判定。默认真实搜索官方公开网页；
搜索失败返回空，不阻塞告警。测试可传入候选资料或 monkeypatch 搜索函数保持离线。
"""
from __future__ import annotations

from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests


ALLOWED_EXACT_DOMAINS = {
    "ads.tiktok.com",
    "newsroom.tiktok.com",
    "seller-id.tiktok.com",
}
ALLOWED_SUFFIXES = (".tiktok.com",)
SELLER_DOMAIN_PREFIX = "seller-"


FEE_KEY_TERMS = {
    "dynamic_commission_amount": ["dynamic commission", "komisi dinamis", "commission", "komisi"],
    "platform_commission_amount": ["platform commission", "commission", "komisi platform", "komisi"],
    "referral_fee_amount": ["referral fee", "biaya referral", "commission"],
    "transaction_fee_amount": ["transaction fee", "biaya transaksi"],
    "affiliate_ads_commission_amount": [
        "shop ads commission", "ads commission", "affiliate commission",
        "komisi shop ads", "komisi afiliasi",
    ],
    "affiliate_commission_amount": ["affiliate commission", "creator commission", "komisi afiliasi"],
    "gmv_max_ad_fee_amount": ["gmv max", "commission savings", "penghematan komisi"],
    "tap_shop_ads_commission": ["shop ads commission", "tiktok shop affiliate partner", "tap"],
    "seller_growth_fee_amount": ["seller growth fee", "bonus cashback", "biaya pertumbuhan"],
    "bonus_cashback_service_fee_amount": ["bonus cashback", "cashback service fee"],
    "mall_service_fee_amount": ["mall service fee", "tiktok shop mall"],
    "sfp_service_fee_amount": ["seller free shipping", "free shipping programme", "sfp"],
}

STRICT_FEE_TERMS = [
    "commission",
    "komisi",
    "fee",
    "biaya",
    "tarif",
    "rate",
    "penghematan komisi",
    "commission savings",
]

BLOCKLIST_TITLE_TERMS = [
    "about product gmv max",
    "tentang product gmv max",
    "gmv max guidelines",
    "pedoman gmv max",
    "how to create",
    "cara membuat",
]


SEARCH_TIMEOUT_SECONDS = 8
SEARCH_MAX_RESULTS_PER_QUERY = 6


def is_allowed_official_url(url: str) -> bool:
    """仅允许 TikTok 官方域名，避免把非官方 SEO 文章发给老板。"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if host in ALLOWED_EXACT_DOMAINS:
        return True
    # TikTok Shop University 常见国家域名形如 seller-us.tiktok.com / seller-jp.tiktok.com。
    return host.startswith(SELLER_DOMAIN_PREFIX) and host.endswith(ALLOWED_SUFFIXES)


def terms_for_fee_keys(fee_keys: list[str]) -> list[str]:
    """费项 key → 中英/印尼关键词。"""
    terms = []
    for key in fee_keys:
        terms.extend(FEE_KEY_TERMS.get(key, []))
    # 保序去重。
    seen = set()
    out = []
    for term in terms:
        t = term.lower()
        if t not in seen:
            seen.add(t)
            out.append(term)
    return out


def _source_label(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host == "ads.tiktok.com":
        return "TikTok Business Help"
    if host == "newsroom.tiktok.com":
        return "TikTok Newsroom"
    if host.startswith("seller-"):
        return "TikTok Shop Academy"
    return "TikTok"


def _normalize_result_url(url: str) -> str:
    """DuckDuckGo HTML 结果常包一层 /l/?uddg=，这里还原目标 URL。"""
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        qs = parse_qs(parsed.query)
        if qs.get("uddg"):
            return unquote(qs["uddg"][0])
    return url


class _DuckDuckGoParser(HTMLParser):
    """提取 DuckDuckGo HTML 搜索结果标题、URL 和摘要。"""

    def __init__(self):
        super().__init__()
        self.results: list[dict] = []
        self._current: dict | None = None
        self._capture_title = False
        self._capture_snippet = False
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls = attrs.get("class") or ""
        if tag == "a" and "result__a" in cls:
            self._current = {"url": _normalize_result_url(attrs.get("href") or ""), "title": "", "summary": ""}
            self._capture_title = True
            self._text = []
        elif tag in {"a", "div"} and "result__snippet" in cls and self.results:
            self._capture_snippet = True
            self._text = []

    def handle_data(self, data):
        if self._capture_title or self._capture_snippet:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._capture_title and self._current is not None:
            self._current["title"] = unescape(" ".join("".join(self._text).split()))
            if self._current["url"]:
                self.results.append(self._current)
            self._current = None
            self._capture_title = False
            self._text = []
        elif tag in {"a", "div"} and self._capture_snippet:
            if self.results:
                self.results[-1]["summary"] = unescape(" ".join("".join(self._text).split()))
            self._capture_snippet = False
            self._text = []


def build_search_queries(*, country: str | None, fee_keys: list[str], alert_date: date | None) -> list[str]:
    """生成面向官方网页的搜索 query。"""
    terms = terms_for_fee_keys(fee_keys)[:4] or ["TikTok Shop commission fee"]
    market_terms = ["Indonesia", "ID", "lang:id"] if (country or "").upper() == "ID" else []
    year = str(alert_date.year) if alert_date else ""
    domain_part = "(site:seller-id.tiktok.com OR site:ads.tiktok.com OR site:newsroom.tiktok.com)"
    base = " ".join([domain_part, "TikTok Shop", *market_terms, year]).strip()
    queries = [f"{base} {term}" for term in terms[:3]]
    # 补一个宽泛 query，防止具体费项英文/印尼文命名不同导致召回太窄。
    queries.append(f"{base} commission fee policy announcement")
    seen = set()
    out = []
    for q in queries:
        key = " ".join(q.split()).lower()
        if key not in seen:
            seen.add(key)
            out.append(q)
    return out


def search_official_policy_candidates(
    *,
    country: str | None,
    fee_keys: list[str],
    alert_date: date | None = None,
    timeout: int = SEARCH_TIMEOUT_SECONDS,
) -> list[dict]:
    """真实搜索官方公开资料；失败返回空。"""
    candidates: list[dict] = []
    seen_urls = set()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
        )
    }
    for query in build_search_queries(country=country, fee_keys=fee_keys, alert_date=alert_date):
        try:
            resp = requests.get(
                f"https://duckduckgo.com/html/?q={quote_plus(query)}",
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
        except requests.RequestException:
            continue
        parser = _DuckDuckGoParser()
        parser.feed(resp.text)
        for result in parser.results[:SEARCH_MAX_RESULTS_PER_QUERY]:
            url = result.get("url") or ""
            if not is_allowed_official_url(url) or url in seen_urls:
                continue
            seen_urls.add(url)
            candidates.append({
                "title": result.get("title") or url,
                "url": url,
                "source": _source_label(url),
                "summary": result.get("summary") or "",
                "terms": terms_for_fee_keys(fee_keys),
            })
    return candidates


def _parse_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def score_candidate(candidate: dict, *, country: str | None, fee_keys: list[str], alert_date: date | None) -> int:
    """对一条候选官方资料打分；分数仅用于排序/过滤，不宣称因果确认。"""
    url = str(candidate.get("url") or "")
    if not is_allowed_official_url(url):
        return 0

    haystack = " ".join(
        str(candidate.get(k) or "") for k in ("title", "summary", "content", "source", "url")
    ).lower()
    title = str(candidate.get("title") or "").lower()
    # 概念介绍/创建教程类 GMV Max 文档不解释费率变化，不能作为费率告警依据。
    if any(term in title for term in BLOCKLIST_TITLE_TERMS):
        return 0
    if not any(term in haystack for term in STRICT_FEE_TERMS):
        return 0

    candidate_terms = [str(t).lower() for t in candidate.get("terms") or []]
    query_terms = [t.lower() for t in terms_for_fee_keys(fee_keys)]

    score = 1
    for term in query_terms:
        if term in haystack or term in candidate_terms:
            score += 3

    c = (country or "").upper()
    if c == "ID" and ("indonesia" in haystack or "lang=id" in url.lower() or "seller-id." in url.lower()):
        score += 2

    published = _parse_date(candidate.get("published_at"))
    if alert_date and published:
        days = abs((alert_date - published).days)
        if days <= 90:
            score += 2
        elif days <= 365:
            score += 1

    return score


def get_policy_references(
    *,
    country: str | None,
    fee_keys: list[str],
    alert_date: date | None = None,
    candidates: list[dict] | None = None,
    limit: int = 2,
    min_score: int = 4,
) -> list[dict]:
    """返回最多 limit 条官方参考资料；无强匹配则返回空。"""
    pool = list(
        candidates
        if candidates is not None
        else search_official_policy_candidates(
            country=country, fee_keys=fee_keys, alert_date=alert_date
        )
    )
    ranked = []
    for item in pool:
        score = score_candidate(item, country=country, fee_keys=fee_keys, alert_date=alert_date)
        if score < min_score:
            continue
        matched_terms = [
            t for t in terms_for_fee_keys(fee_keys)
            if t.lower() in " ".join(str(item.get(k) or "") for k in ("title", "summary", "content", "url")).lower()
            or t.lower() in [str(x).lower() for x in item.get("terms") or []]
        ]
        ranked.append((score, {
            "title": item.get("title") or item.get("url"),
            "url": item.get("url"),
            "source": item.get("source") or "TikTok",
            "published_at": item.get("published_at"),
            "matched_terms": matched_terms[:4],
            "confidence": "medium" if score >= 6 else "low",
            "summary": item.get("summary") or "",
        }))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in ranked[:limit]]
