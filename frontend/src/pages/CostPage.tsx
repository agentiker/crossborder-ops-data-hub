import { useEffect, useMemo, useState } from "react";
import { ImageOff, PackageSearch, Search } from "lucide-react";
import { api, type ProductCostRow } from "@/api";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

// 马帮成本页（/costs）：product_costs 当前快照，成本 = RMB 含国内头程运费（马帮统一成本价）。
// 与汇率同属「基础数据」——利润折算的底层参数，低频参考性质。商品图从平台商品主数据关联
// （马帮 ERP 无图），未匹配到则显占位。数据量小（数百 SKU），筛选/排序全在前端做。

type SortKey = "updated" | "cost_desc" | "cost_asc";

const SORTS: { key: SortKey; label: string }[] = [
  { key: "updated", label: "最近更新" },
  { key: "cost_desc", label: "成本高→低" },
  { key: "cost_asc", label: "成本低→高" },
];

// 成本 RMB 展示：保留 2 位小数、去尾 0（15.80 → 15.8，15.00 → 15）。
function fmtCost(v: number): string {
  return v.toFixed(2).replace(/\.?0+$/, "");
}

// updated_at（ISO）→ YYYY-MM-DD；无则「—」。
function fmtDate(iso: string | null): string {
  return iso ? iso.slice(0, 10) : "—";
}

export function CostPage() {
  const [rows, setRows] = useState<ProductCostRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortKey>("updated");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .costList()
      .then((r) => {
        if (alive) setRows(r.items);
      })
      .catch((e) => {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  // 关键词筛选（seller_sku / 款号，大小写不敏感）+ 排序。后端已按更新时间倒序返回，
  // 「最近更新」直接用原序；成本排序在此重排。
  const view = useMemo(() => {
    if (!rows) return [];
    const kw = q.trim().toLowerCase();
    const filtered = kw
      ? rows.filter(
          (r) =>
            r.seller_sku.toLowerCase().includes(kw) ||
            (r.product_title ?? "").toLowerCase().includes(kw),
        )
      : rows;
    if (sort === "updated") return filtered;
    const dir = sort === "cost_desc" ? -1 : 1;
    return [...filtered].sort((a, b) => (a.unit_cost_rmb - b.unit_cost_rmb) * dir);
  }, [rows, q, sort]);

  const total = rows?.length ?? 0;

  return (
    <div className="flex-1">
      <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6">
        <PageHeader
          title="马帮成本"
          scope="产品单位成本 · RMB 含国内头程运费"
          period={total ? `共 ${total} 个 SKU` : undefined}
        />

        {/* 控件行：关键词搜索 + 排序分段。移动端换行、触控目标足够大。 */}
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <label className="relative min-w-0 flex-1 sm:max-w-xs">
            <span className="sr-only">搜索 SKU 或款号</span>
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-foreground-tertiary" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索 SKU 或款号"
              className="pl-8"
            />
          </label>

          <div className="inline-flex rounded-md bg-fill-default p-0.5">
            {SORTS.map((s) => (
              <button
                key={s.key}
                type="button"
                onClick={() => setSort(s.key)}
                aria-pressed={sort === s.key}
                className={cn(
                  "h-8 rounded-[7px] px-3 text-sm transition-colors",
                  sort === s.key
                    ? "bg-white font-medium text-foreground shadow-sm"
                    : "text-foreground-secondary hover:text-foreground",
                )}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        {/* 列表卡：加载 / 错误 / 空 / 数据 四态。行式布局（缩略图 + SKU + 成本），移动端天然适配。 */}
        <Card className="mt-4">
          <CardContent className="p-0">
            {loading ? (
              <div className="space-y-2 p-4">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-14 w-full" />
                ))}
              </div>
            ) : error ? (
              <div className="flex h-[320px] flex-col items-center justify-center gap-1 text-center">
                <p className="text-sm text-destructive">加载失败</p>
                <p className="text-xs text-foreground-tertiary">{error}</p>
              </div>
            ) : view.length === 0 ? (
              <div className="flex h-[320px] flex-col items-center justify-center gap-1 text-center">
                <PackageSearch className="size-6 text-foreground-tertiary" />
                <p className="text-sm text-foreground-secondary">
                  {total === 0 ? "暂无成本数据" : "没有匹配的 SKU"}
                </p>
                <p className="text-xs text-foreground-tertiary">
                  {total === 0
                    ? "马帮成本同步后在此展示（按 seller_sku 关联）"
                    : "换个关键词试试"}
                </p>
              </div>
            ) : (
              <ul className="divide-y divide-border-shallow">
                {view.map((r) => (
                  <CostRow key={r.seller_sku} row={r} />
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function CostRow({ row }: { row: ProductCostRow }) {
  const [imgError, setImgError] = useState(false);
  const showImg = row.image_url && !imgError;
  return (
    <li className="flex items-center gap-3 px-4 py-2.5">
      {/* 缩略图：无图 / 加载失败 → 占位框。固定 44px，圆角，object-cover 防拉伸。 */}
      <div className="flex size-11 shrink-0 items-center justify-center overflow-hidden rounded-md bg-fill-default">
        {showImg ? (
          <img
            src={row.image_url!}
            alt={row.seller_sku}
            loading="lazy"
            onError={() => setImgError(true)}
            className="size-full object-cover"
          />
        ) : (
          <ImageOff className="size-4 text-foreground-tertiary" />
        )}
      </div>

      {/* 主体：SKU（等宽）+ 款号 + 来源 note。min-w-0 让 truncate 生效。 */}
      <div className="min-w-0 flex-1">
        <div className="truncate font-mono text-sm font-medium text-foreground">
          {row.seller_sku}
        </div>
        {row.product_title && (
          <div className="truncate text-xs text-foreground-secondary">{row.product_title}</div>
        )}
        {row.note && (
          <div className="truncate text-[11px] text-foreground-tertiary">{row.note}</div>
        )}
      </div>

      {/* 右侧：成本 RMB + 更新日期。tabnum 对齐数字。 */}
      <div className="shrink-0 text-right">
        <div className="tabnum text-sm font-semibold text-foreground">
          ¥{fmtCost(row.unit_cost_rmb)}
        </div>
        <div className="text-[11px] text-foreground-tertiary">{fmtDate(row.updated_at)}</div>
      </div>
    </li>
  );
}
