"""马帮 ERP 无头浏览器客户端：登录 + 抓「统一成本价」与「组合成分」。

为什么要浏览器（见 memory mabang-cost-scrape-feasibility）：登录本身是纯表单 POST、无验证码，
但登录后 app 在 `aamz.mabangerp.com` 子域、靠 cMKey 跨域握手建会话，www 的 cookie 不认 aamz；
且组合列表接口 `combosku.getCombosSkuList` 手动翻页/搜索只回工具栏、成分的 data-* 属性由 JS 注入
raw HTML 里没有。故必须 Playwright 登录后在 aamz iframe 上下文里 fetch / 读渲染后 DOM。

用法：
    with MabangClient(user, password) as mb:
        base = mb.fetch_base_costs()      # {库存SKU: Decimal(统一成本价)}
        combos = mb.fetch_combos(base)    # {组合SKU: [(成分库存SKU, 件数)]}
凭证只用于登录、不落库、不进日志。
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

from playwright.sync_api import Frame, Page, sync_playwright

logger = logging.getLogger(__name__)

HOME_URL = "https://www.mabangerp.com/index.htm"
STOCK_LIST_URL = "https://www.mabangerp.com/index.php?mod=stock.list&searchStatus=1"
COMBO_LIST_URL = "https://www.mabangerp.com/index.php?mod=combosku.list"

# —— 登录按钮点击（弹窗内「登录」，同名元素多个取最后一个可见的）——
_CLICK_LOGIN_JS = """() => {
  const m = document.querySelector('#account')
      ? document.querySelector('#account').closest('div[class*=login],div[class*=modal],form') : null;
  const scope = m || document;
  const bs = [...scope.querySelectorAll('button,a,div,span')]
      .filter(e => /^登 ?录$/.test((e.innerText||'').trim()));
  if (bs.length) bs[0].click();
}"""

_OPEN_MODAL_JS = """() => {
  const t = [...document.querySelectorAll('a,button,span,div')]
      .find(e => /^登录$|^登 录$/.test((e.innerText||'').trim()));
  if (t) t.click();
}"""

# —— getStockList 翻页拉全量基础SKU 统一成本价（在 aamz frame 内 fetch，同源带 cookie）——
_FETCH_BASE_JS = """async (origin) => {
  const all = []; let page = 1; const per = 500;
  while (page <= 40) {
    const body = new URLSearchParams({searchKey:'Stock_stockSku', operate:'likeStart',
      'search-content':'库存SKU', status:'', showstart:'1',
      page:String(page), rowsPerPage:String(per), stockOrderby:''});
    const r = await fetch(origin + '/index.php?mod=stock.getStockList',
      {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8',
       'X-Requested-With':'XMLHttpRequest'}, body});
    const j = await r.json();
    if (!j.success) return {error: j.message || 'getStockList failed', page};
    const rows = j.stockData || [];
    rows.forEach(d => all.push([d.stockSku, d.defaultCost]));
    if (rows.length < per) break;
    page++;
  }
  return {rows: all};
}"""

# —— 组合列表：填搜索框 + 点搜索，让页面自己重渲染 iframe ——
_SEARCH_COMBO_JS = """(kw) => {
  const inp = document.querySelector('input[name="searchKeywords"]')
      || document.querySelector('#search-content-text1');
  if (inp) inp.value = kw;
  const bt = [...document.querySelectorAll('button,a,input[type=button],input[type=submit]')]
      .find(e => /搜索|查询/.test((e.textContent||e.value||'').trim()));
  if (bt) bt.click();
}"""

# —— 从渲染后 DOM 抽组合名 + 成分（data-* 由 JS 注入，故读 DOM 不读 raw HTML）——
_READ_COMBO_JS = """() => {
  const map = {};
  document.querySelectorAll('a.comboskuCopy').forEach(a => {
    const cid = (a.id || '').replace('stock_', '');
    const name = (a.textContent || '').trim();
    if (cid && name) map[cid] = {sku: name, comps: []};
  });
  document.querySelectorAll('[data-cid][data-sku][data-quantity]').forEach(e => {
    const cid = e.getAttribute('data-cid');
    const sku = e.getAttribute('data-sku');
    const qty = parseInt(e.getAttribute('data-quantity') || '1');
    if (map[cid]) map[cid].comps.push([sku, qty]);
  });
  return Object.values(map);
}"""


def _to_decimal(raw) -> Decimal:
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, AttributeError, TypeError):
        return Decimal("0")


def _style_keywords(base_skus: list[str]) -> list[str]:
    """从基础SKU 名提取 3-4 位数字款号作为组合搜索关键词（去重）。

    组合短码（809-KH-L）以款号打头，与基础SKU（XGN809-002Black-L）共享该数字。收集所有
    3-4 位数字组、去重逐个搜索并累积去重组合——过匹配无害（按组合名去重），保证完整覆盖。
    """
    kws: set[str] = set()
    for sku in base_skus:
        for m in re.findall(r"\d{3,4}", sku):
            kws.add(m)
    return sorted(kws)


class MabangClient:
    def __init__(self, user: str, password: str, *, headless: bool = True, slow_ms: int = 0):
        if not user or not password:
            raise ValueError("马帮凭证缺失（配置 MABANG__USER / MABANG__PASSWORD）")
        self._user = user
        self._password = password
        self._headless = headless
        self._slow_ms = slow_ms
        self._pw = None
        self._browser = None
        self._page: Page | None = None

    # —— 生命周期 ——
    def __enter__(self) -> "MabangClient":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless, slow_mo=self._slow_ms)
        ctx = self._browser.new_context(viewport={"width": 1600, "height": 950}, locale="zh-CN")
        self._page = ctx.new_page()
        self._login()
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    # —— 内部 ——
    def _login(self) -> None:
        p = self._page
        p.goto(HOME_URL, wait_until="networkidle", timeout=45000)
        p.wait_for_timeout(1500)
        acc = p.locator("#account")
        if not acc.is_visible():
            p.evaluate(_OPEN_MODAL_JS)
            p.wait_for_timeout(1200)
        acc.wait_for(state="visible", timeout=8000)
        acc.fill(self._user)
        p.locator("#password").fill(self._password)
        p.evaluate(_CLICK_LOGIN_JS)
        p.wait_for_timeout(5000)
        # 进库存页建立 aamz 会话；找不到 aamz frame 即登录失败（凭证错/被锁/改版）
        p.goto(STOCK_LIST_URL, wait_until="networkidle", timeout=45000)
        p.wait_for_timeout(4000)
        if self._aamz_frame() is None:
            raise RuntimeError("马帮登录失败：未进入 aamz 应用会话（检查凭证/是否被锁/页面改版）")
        logger.info("马帮登录成功")

    def _aamz_frame(self) -> Frame | None:
        for f in self._page.frames:
            if "aamz" in f.url:
                return f
        return None

    def _require_aamz(self) -> Frame:
        fr = self._aamz_frame()
        if fr is None:
            raise RuntimeError("马帮 aamz 应用 frame 丢失（会话失效？）")
        return fr

    # —— 抓数 ——
    def fetch_base_costs(self) -> dict[str, Decimal]:
        """{库存SKU: 统一成本价 Decimal}（全量，含 0；调用方按 >0 过滤）。"""
        fr = self._require_aamz()
        origin = fr.evaluate("() => location.origin")
        res = fr.evaluate(_FETCH_BASE_JS, origin)
        if isinstance(res, dict) and res.get("error"):
            raise RuntimeError(f"马帮 getStockList 失败: {res['error']}")
        out: dict[str, Decimal] = {}
        for sku, cost in res["rows"]:
            if sku:
                out[sku] = _to_decimal(cost)
        logger.info("抓到基础SKU %d 个（统一成本价非零 %d）",
                    len(out), sum(1 for v in out.values() if v > 0))
        return out

    def fetch_combos(self, base_costs: dict[str, Decimal]) -> dict[str, list[tuple[str, int]]]:
        """{组合SKU: [(成分库存SKU, 件数)]}，按款号搜索式累积去重。"""
        p = self._page
        p.goto(COMBO_LIST_URL, wait_until="networkidle", timeout=45000)
        p.wait_for_timeout(4500)
        keywords = _style_keywords(list(base_costs.keys()))
        combos: dict[str, list[tuple[str, int]]] = {}
        for kw in keywords:
            fr = self._require_aamz()
            fr.evaluate(_SEARCH_COMBO_JS, kw)
            p.wait_for_timeout(2600)
            fr = self._require_aamz()
            rows = fr.evaluate(_READ_COMBO_JS)
            for r in rows:
                sku = r["sku"]
                comps = [(c[0], int(c[1])) for c in r["comps"]]
                if sku and sku not in combos:
                    combos[sku] = comps
        logger.info("抓到组合SKU %d 个（搜索 %d 个款号）", len(combos), len(keywords))
        return combos
