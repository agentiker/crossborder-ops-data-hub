"""GMV Max 报表接口真打验证脚本（TikTok Marketing API）。

用途：客户建 App + 授权后，打通 advertiser → store → report 三步，打印 GMV Max 花费
（cost）等字段的原始值，确认接口通、口径对，再决定灌数据入库。

依赖：core/config 的 tiktok_business.app_id / app_secret（或 --app-id/--app-secret 覆盖）。

用法（三选一，按你手头有什么）：

  # A. 只有 auth_code（刚授权完，还没换 token）：
  python -m scripts.probe_gmv_max_report --auth-code <CODE> --account-id ecom-app

  # B. 已有 access_token（+ 可选 advertiser_id）：
  python -m scripts.probe_gmv_max_report --access-token <TOKEN>

  # C. token 已入库（跑过 A 或回调授权过）：
  python -m scripts.probe_gmv_max_report --account-id ecom-app --advertiser-id <ADV_ID>

  # 指定报表窗口（默认最近 7 天，注意含 stat_time_day 时窗口 ≤30 天）：
  python -m scripts.probe_gmv_max_report --access-token <T> --start 2025-07-01 --end 2025-07-07
"""
import argparse
import json
from datetime import date, timedelta

from platforms.tiktok_business.client import TikTokBusinessClient, TikTokBusinessError


def _print_header(title):
    print(f"\n{'=' * 8} {title} {'=' * 8}")


def main():
    p = argparse.ArgumentParser(description="GMV Max 报表真打验证")
    p.add_argument("--account-id", default=None, help="租户 account_id（决定用哪套凭据/token）")
    p.add_argument("--app-id", default=None, help="覆盖 Marketing API App ID")
    p.add_argument("--app-secret", default=None, help="覆盖 App secret")
    p.add_argument("--auth-code", default=None, help="OAuth 授权码（换 token，路径 A）")
    p.add_argument("--access-token", default=None, help="已有 access_token（路径 B）")
    p.add_argument("--advertiser-id", default=None, help="广告主 ID（不填则自动列举）")
    p.add_argument("--store-id", default=None, help="店铺 ID（不填则自动列举可用店）")
    p.add_argument("--start", default=None, help="报表起始日 YYYY-MM-DD（默认 7 天前）")
    p.add_argument("--end", default=None, help="报表结束日 YYYY-MM-DD（默认今天）")
    p.add_argument("--no-persist", action="store_true", help="换 token 时不落库")
    args = p.parse_args()

    client = TikTokBusinessClient(
        account_id=args.account_id,
        app_id=args.app_id,
        app_secret=args.app_secret,
        access_token=args.access_token,
        advertiser_id=args.advertiser_id,
    )
    print(f"base_url={client.base_url}  account_id={client.account_id}  app_id={client.app_id or '(未配)'}")

    try:
        # ── 拿到 access_token ──────────────────────────────────────────
        if args.auth_code:
            _print_header("1. 换 token (oauth2/access_token)")
            data = client.authenticate(args.auth_code, persist=not args.no_persist)
            print("scope:", data.get("scope"))
            print("advertiser_ids:", data.get("advertiser_ids"))
        elif not client.access_token:
            _print_header("1. 从库加载 token")
            if not client.load_token(advertiser_id=args.advertiser_id):
                print("✗ 库里没有 token，请用 --auth-code 或 --access-token")
                return
            print("✓ 已加载 access_token")

        # ── 列广告主 ──────────────────────────────────────────────────
        advertiser_id = args.advertiser_id
        if not advertiser_id:
            _print_header("2. 列广告主 (oauth2/advertiser/get)")
            advertisers = client.get_advertisers()
            for a in advertisers:
                print("  advertiser:", a)
            if not advertisers:
                print("✗ 无授权广告主"); return
            advertiser_id = str(advertisers[0].get("advertiser_id"))
            print(f"→ 用第一个 advertiser_id={advertiser_id}")

        # ── 列 GMV Max 店铺 ───────────────────────────────────────────
        store_id = args.store_id
        if not store_id:
            _print_header("3. 列 GMV Max 店铺 (gmv_max/store/list)")
            stores = client.get_gmv_max_stores(advertiser_id)
            for s in stores:
                print("  store:", s)
            usable = [s for s in stores if s.get("is_gmv_max_available") in (True, "true")]
            pick = (usable or stores)
            if not pick:
                print("✗ 无可用店铺"); return
            store_id = str(pick[0].get("store_id") or pick[0].get("id"))
            print(f"→ 用 store_id={store_id}（is_gmv_max_available 优先）")

        # ── 拉报表 ────────────────────────────────────────────────────
        end = args.end or date.today().isoformat()
        start = args.start or (date.today() - timedelta(days=7)).isoformat()
        _print_header(f"4. GMV Max 报表 (gmv_max/report/get)  {start} ~ {end}")
        report = client.get_gmv_max_report(advertiser_id, store_id, start, end)
        print("page_info:", report.get("page_info"))
        rows = report.get("list") or []
        print(f"共 {len(rows)} 行：")
        for row in rows:
            print(json.dumps(row, ensure_ascii=False, indent=2))
        # 重点核对 cost 字段是否真有值
        _print_header("核对：花费字段")
        total_cost = 0.0
        for row in rows:
            m = row.get("metrics", {})
            print(f"  dims={row.get('dimensions')}  cost={m.get('cost')}  "
                  f"gross_revenue={m.get('gross_revenue')}  currency={m.get('currency')}")
            try:
                total_cost += float(m.get("cost") or 0)
            except (TypeError, ValueError):
                pass
        print(f"→ cost 合计 = {total_cost}")

    except TikTokBusinessError as e:
        print(f"\n✗ 业务错误 code={e.code} message={e.message} request_id={e.request_id}")
    except Exception as e:
        print(f"\n✗ 请求失败: {e}")


if __name__ == "__main__":
    main()
