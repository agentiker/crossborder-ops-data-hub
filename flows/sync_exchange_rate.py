"""中国银行外汇牌价同步 flow（www.boc.cn/sourcedb/whpj/index.html）。

全局单次执行（**不**按店循环）：日频抓中行整张牌价表，upsert 全币种进 fact_exchange_rate，
供利润 IDR→CNY 折算取数（services/fx_rate 查表）。中行工作日上午更新牌价，timer 每工作日
一次即可。

中行是公网静态 HTML（UTF-8），不受 TikTok IP 白名单约束，requests 默认走系统代理即可。
解析后硬校验「至少抓到 IDR 且 折算价/unit 在合理区间」——防中行改版静默解析空/错，失败
raise → systemd OnFailure 飞书告警，不静默回落。
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal

import requests

from core.db import SessionLocal, init_db
from core.retry import retry
from flows.network import log_egress_ip
from services.exchange_rate_store import SOURCE, parse_boc_html, upsert_exchange_rates
from services.sync_state import record_raw_response, upsert_cursor

logger = logging.getLogger(__name__)

BOC_URL = "https://www.boc.cn/sourcedb/whpj/index.html"
RESOURCE = "exchange_rate"

# IDR→CNY 合理区间护栏：现值约 0.00038，固定回落值 0.00045，取宽区间兜住单位口径取错
# （若误把 100 外币当 1 外币，值会差 100 倍、落在区间外 → 报错）。
IDR_RATE_MIN = Decimal("0.0002")
IDR_RATE_MAX = Decimal("0.0008")


@retry(retries=3, delay_seconds=60)
def fetch_boc_html() -> str:
    """抓中行牌价页面 HTML（UTF-8）。网络抖动重试 3 次。"""
    log_egress_ip()  # 打印出口 IP 便于审计（中行无白名单需求）
    resp = requests.get(BOC_URL, timeout=20, headers={
        "User-Agent": "Mozilla/5.0 (compatible; ops-data-hub/1.0)"
    })
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def _assert_idr_sane(rows: list[dict]) -> None:
    """解析结果健全性校验：必须含 IDR 行，且 1 IDR→CNY 折算落合理区间。"""
    idr = next((r for r in rows if r.get("currency_code") == "IDR"), None)
    if idr is None:
        raise RuntimeError(f"中行牌价解析未找到 IDR 行（解析 {len(rows)} 行）——疑似页面改版")
    unit = idr.get("unit") or 1
    per_idr = Decimal(str(idr["rate_middle"])) / Decimal(str(unit))
    if not (IDR_RATE_MIN <= per_idr <= IDR_RATE_MAX):
        raise RuntimeError(
            f"IDR→CNY={per_idr} 超出合理区间 [{IDR_RATE_MIN},{IDR_RATE_MAX}]"
            f"（折算价 {idr['rate_middle']} / unit {unit}）——疑似单位口径取错或页面改版"
        )


@retry(retries=2, delay_seconds=30)
def save_to_db(html: str) -> int:
    """单事务：解析 → 校验 → 记 raw 审计摘要 → upsert 全币种 → 更新 cursor。"""
    rows = parse_boc_html(html)
    _assert_idr_sane(rows)  # 校验失败在 commit 前 raise，不落脏数据

    session = SessionLocal()
    try:
        # raw 审计存解析摘要（不存整页 HTML，避免体积膨胀；原始页可随时重抓）
        idr = next((r for r in rows if r.get("currency_code") == "IDR"), None)
        summary = {
            "currency_count": len(rows),
            "idr_rate_middle": float(idr["rate_middle"]) if idr else None,
            "idr_unit": idr.get("unit") if idr else None,
            "metric_date": idr["metric_date"].isoformat() if idr and idr.get("metric_date") else None,
        }
        raw_record = record_raw_response(
            session,
            platform=SOURCE,
            country="GLOBAL",
            shop_id=None,
            seller_id=None,
            account_id=None,
            resource=RESOURCE,
            method="GET",
            path="/sourcedb/whpj/index.html",
            request_params={},
            request_body={},
            response_payload=summary,
            http_status=200,
            business_code="0",
        )

        count = upsert_exchange_rates(session, rows, raw_response_id=raw_record.id)

        upsert_cursor(
            session,
            platform=SOURCE,
            country="GLOBAL",
            shop_id=None,
            seller_id=None,
            account_id=None,
            resource=RESOURCE,
            window_end=datetime.now(timezone.utc),
            extra={"currency_count": count, **summary},
        )
        session.commit()
        return count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def sync_exchange_rate_flow() -> int:
    """中行汇率同步主流程（全局单次，不按店循环）。"""
    init_db()
    html = fetch_boc_html()
    count = save_to_db(html)
    print(f"中行外汇牌价同步完成: {count} 个币种")
    return count


if __name__ == "__main__":
    # 全局流，无 run_for_all_shops（汇率非按店数据）
    sync_exchange_rate_flow()
