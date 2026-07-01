"""中国银行外汇牌价解析 + 幂等入库（www.boc.cn/sourcedb/whpj/index.html）。

抓取层：中行页面是 UTF-8 静态 HTML 表格，8 列（货币名称/现汇买入/现钞买入/现汇卖出/
现钞卖出/中行折算价/发布日期/发布时间）。所有币种统一按 100 外币报价（unit=100）。

parse_boc_html 纯函数（喂 HTML 字符串出 rows，便于单测）；upsert_exchange_rates 仿
services/ad_spend_store.upsert_ad_spend_daily 按自然唯一键 (source,metric_date,currency_code)
幂等 upsert（全局数据无 shop 维度，不用 scope_key）。
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from models.base_models import FactExchangeRate

logger = logging.getLogger(__name__)

SOURCE = "boc"
BOC_UNIT = 100  # 中行所有币种统一按 100 外币报价

# 中行中文名 → ISO 4217 币种码。映射不到的币种回落「中文名」本身作 code（保证不丢行、
# 且业务关心的 IDR/CNY 必中）。仅列全表出现过的 40 个币种。
_NAME_TO_ISO = {
    "阿联酋迪拉姆": "AED", "澳大利亚元": "AUD", "文莱元": "BND", "巴西雷亚尔": "BRL",
    "加拿大元": "CAD", "瑞士法郎": "CHF", "捷克克朗": "CZK", "丹麦克朗": "DKK",
    "欧元": "EUR", "英镑": "GBP", "港币": "HKD", "匈牙利福林": "HUF",
    "印尼卢比": "IDR", "以色列谢克尔": "ILS", "印度卢比": "INR", "日元": "JPY",
    "柬埔寨瑞尔": "KHR", "韩国元": "KRW", "科威特第纳尔": "KWD", "蒙古图格里克": "MNT",
    "澳门元": "MOP", "墨西哥比索": "MXN", "林吉特": "MYR", "挪威克朗": "NOK",
    "尼泊尔卢比": "NPR", "新西兰元": "NZD", "菲律宾比索": "PHP", "巴基斯坦卢比": "PKR",
    "卡塔尔里亚尔": "QAR", "塞尔维亚第纳尔": "RSD", "卢布": "RUB", "沙特里亚尔": "SAR",
    "瑞典克朗": "SEK", "新加坡元": "SGD", "泰国铢": "THB", "土耳其里拉": "TRY",
    "新台币": "TWD", "美元": "USD", "越南盾": "VND", "南非兰特": "ZAR",
}


def _dec(text: str) -> Optional[Decimal]:
    """把单元格文本转 Decimal；空/非数字返回 None（现汇/现钞价可能为空）。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _strip_tags(cell: str) -> str:
    return re.sub(r"<[^>]+>", "", cell).replace("&nbsp;", "").strip()


def parse_boc_html(html: str) -> list[dict]:
    """解析中行牌价 HTML，返回每币种一条 dict。

    每条字段：currency_code / currency_name / unit(=100) / rate_middle(Decimal) /
    spot_buy / cash_buy / spot_sell / cash_sell / metric_date(date) / published_at(datetime)。
    只保留「中行折算价」能转成数字的行（跳表头/提示行/折算价为空的行）。
    """
    # 锁定含表头「货币名称」的数据表，避免误匹配页面上其它布局 table
    m = re.search(r"<table[^>]*>.*?货币名称.*?</table>", html, re.DOTALL)
    if not m:
        return []
    table = m.group(0)

    rows: list[dict] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.DOTALL):
        cells = [_strip_tags(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.DOTALL)]
        # 数据行需 8 列（货币名 + 4价 + 折算价 + 发布日期 + 发布时间）
        if len(cells) < 7:
            continue
        name = cells[0]
        if not name or "点击" in name:  # 跳提示行
            continue
        rate_middle = _dec(cells[5])
        if rate_middle is None:  # 折算价缺失 → 无折算意义，跳过
            continue

        published_at = None
        metric_date: Optional[date] = None
        # cells[6] 形如 "2026/07/02 00:03:29"（发布日期列已含完整时间戳）
        stamp = cells[6].strip() if len(cells) > 6 else ""
        if stamp:
            for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y-%m-%d"):
                try:
                    published_at = datetime.strptime(stamp, fmt)
                    metric_date = published_at.date()
                    break
                except ValueError:
                    continue

        rows.append({
            "currency_code": _NAME_TO_ISO.get(name, name),
            "currency_name": name,
            "unit": BOC_UNIT,
            "rate_middle": rate_middle,
            "spot_buy": _dec(cells[1]),
            "cash_buy": _dec(cells[2]),
            "spot_sell": _dec(cells[3]),
            "cash_sell": _dec(cells[4]),
            "metric_date": metric_date,
            "published_at": published_at,
        })
    return rows


def upsert_exchange_rates(
    session,
    rows: list[dict],
    *,
    source: str = SOURCE,
    raw_response_id: Optional[int] = None,
) -> int:
    """按 (source, currency_code, published_at) 幂等 upsert 牌价行。

    一天多次抓取：同一发布时刻（published_at 相同）重复抓只更新不新增；中行日内真更新了
    （新 published_at）才多存一行 → 存的是「当天实际发生过的不同牌价样本」，供 fx_rate 取
    当天均值。published_at/metric_date 为空的行跳过（无发布时刻无法去重/定位业务日）。
    有则逐字段更新、无则 add；末尾 flush，由调用方 commit。返回落库行数。
    """
    written = 0
    for row in rows:
        metric_date = row.get("metric_date")
        published_at = row.get("published_at")
        if metric_date is None or published_at is None:
            continue
        code = row["currency_code"]
        existing = (
            session.query(FactExchangeRate)
            .filter_by(source=source, currency_code=code, published_at=published_at)
            .first()
        )
        if existing:
            existing.metric_date = metric_date
            existing.currency_name = row.get("currency_name")
            existing.unit = row.get("unit", BOC_UNIT)
            existing.rate_middle = row["rate_middle"]
            existing.spot_buy = row.get("spot_buy")
            existing.cash_buy = row.get("cash_buy")
            existing.spot_sell = row.get("spot_sell")
            existing.cash_sell = row.get("cash_sell")
            existing.raw_response_id = raw_response_id
        else:
            session.add(FactExchangeRate(
                metric_date=metric_date,
                source=source,
                currency_code=code,
                currency_name=row.get("currency_name"),
                unit=row.get("unit", BOC_UNIT),
                rate_middle=row["rate_middle"],
                spot_buy=row.get("spot_buy"),
                cash_buy=row.get("cash_buy"),
                spot_sell=row.get("spot_sell"),
                cash_sell=row.get("cash_sell"),
                published_at=published_at,
                raw_response_id=raw_response_id,
            ))
        written += 1
    session.flush()
    return written
