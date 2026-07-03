import { useEffect, useMemo, useState } from "react";
import { ArrowRightLeft } from "lucide-react";
import { api, type FxCurrency, type FxSeries } from "@/api";
import { EChart, useChartTokens } from "@/components/EChart";
import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

// 汇率走势页（/fx）：中行牌价日序列，按币种 + 时间段筛选。汇率非隔离数据（全局牌价），
// 与主看板独立成页——它是利润折算的底层参数，低频参考性质，不挤占主看板的经营决策密度。

const RANGES = [
  { days: 30, label: "30 天" },
  { days: 90, label: "90 天" },
  { days: 365, label: "1 年" },
] as const;

// 小额币种（如 IDR，1 外币≈0.000379 CNY）纵轴按「100 外币→CNY」缩放，读数落在 3.79 量级更直觉；
// 大额币种（USD 等 rate≥1）直显原值。判据：最新值 < 0.1 视为小额。返回 {scale, unitLabel}。
function displayScale(latest: number | null): { scale: number; unit: number } {
  if (latest != null && latest > 0 && latest < 0.1) return { scale: 100, unit: 100 };
  return { scale: 1, unit: 1 };
}

// 精确值格式：小额币种保留到 6 位有效小数（0.000379），大额 4 位（680.6700 → 680.67）。
function fmtRate(v: number): string {
  if (v === 0) return "0";
  return v < 0.1 ? v.toPrecision(3) : v.toFixed(4).replace(/\.?0+$/, "");
}

export function FxPage() {
  const t = useChartTokens();
  const [currencies, setCurrencies] = useState<FxCurrency[] | null>(null);
  const [currency, setCurrency] = useState("IDR");
  const [days, setDays] = useState<number>(90);
  const [series, setSeries] = useState<FxSeries | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 币种下拉：仅首载一次。
  useEffect(() => {
    api
      .fxCurrencies()
      .then((r) => setCurrencies(r.items))
      .catch(() => setCurrencies([{ code: "IDR", name: "印尼卢比" }]));
  }, []);

  // 序列：币种/时间段变即重取。
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .fxSeries(currency, days)
      .then((s) => {
        if (alive) setSeries(s);
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
  }, [currency, days]);

  const { scale, unit } = displayScale(series?.latest ?? null);
  const hasData = !!series && series.points.length > 0;
  const up = (series?.change_pct ?? 0) >= 0;

  const option = useMemo(() => {
    if (!series) return {};
    const pts = series.points;
    const lineColor = t.primary;
    return {
      grid: { left: 8, right: 14, top: 16, bottom: 4, containLabel: true },
      tooltip: {
        trigger: "axis" as const,
        // 精确值展示 1 外币→CNY，不受纵轴缩放影响（缩放只为坐标可读，真值在 tooltip）。
        formatter: (ps: { axisValue: string; dataIndex: number }[]) => {
          const i = ps[0]?.dataIndex ?? 0;
          const raw = pts[i]?.rate ?? 0;
          return `${ps[0]?.axisValue}<br/>1 ${series.currency} = ${fmtRate(raw)} CNY`;
        },
      },
      xAxis: {
        type: "category" as const,
        data: pts.map((p) => p.date.slice(5)), // MM-DD
        axisLine: { lineStyle: { color: t.grid } },
        axisLabel: { color: t.sub, fontSize: 10 },
        // 点多时稀疏显标签，避免拥挤（1 年 ≈ 250 个交易日）。
        boundaryGap: false,
      },
      yAxis: {
        type: "value" as const,
        scale: true, // 汇率波动小，从 0 起会压平曲线 → 自适应区间凸显趋势。
        axisLabel: { color: t.sub, fontSize: 10 },
        splitLine: { lineStyle: { color: t.grid } },
      },
      series: [
        {
          type: "line" as const,
          smooth: true,
          symbol: pts.length > 60 ? "none" : "circle",
          symbolSize: 5,
          data: pts.map((p) => Number((p.rate * scale).toPrecision(6))),
          lineStyle: { color: lineColor, width: 2 },
          itemStyle: { color: lineColor },
          areaStyle: { color: lineColor, opacity: 0.08 },
          emphasis: { disabled: true },
        },
      ],
    };
  }, [series, scale, t]);

  return (
    <div className="flex-1">
      <div className="mx-auto max-w-4xl px-4 py-6 sm:px-6">
        <PageHeader
          title="汇率走势"
          scope="中国银行外汇牌价 · 折算价日均值"
          period={currency ? `1 ${currency} → CNY` : undefined}
        />

        {/* 控件行：币种下拉 + 时间段分段。移动端换行、触控目标足够大。 */}
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <label className="relative">
            <span className="sr-only">选择币种</span>
            <select
              value={currency}
              onChange={(e) => setCurrency(e.target.value)}
              disabled={!currencies}
              className={cn(
                "h-9 appearance-none rounded-md border border-input bg-transparent pl-3 pr-8 text-sm font-medium text-foreground",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
              )}
            >
              {(currencies ?? [{ code: "IDR", name: "印尼卢比" }]).map((c) => (
                <option key={c.code} value={c.code}>
                  {c.name} {c.code}
                </option>
              ))}
            </select>
            <ArrowRightLeft className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 text-foreground-tertiary" />
          </label>

          <div className="inline-flex rounded-md bg-fill-default p-0.5">
            {RANGES.map((r) => (
              <button
                key={r.days}
                type="button"
                onClick={() => setDays(r.days)}
                aria-pressed={days === r.days}
                className={cn(
                  "h-8 rounded-[7px] px-3 text-sm transition-colors",
                  days === r.days
                    ? "bg-white font-medium text-foreground shadow-sm"
                    : "text-foreground-secondary hover:text-foreground",
                )}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>

        {/* 摘要行：克制的 label 行，非英雄大数字（避开 hero-metric 模板）。 */}
        {hasData && (
          <div className="mt-5 flex flex-wrap items-baseline gap-x-6 gap-y-2">
            <div>
              <div className="text-xs text-foreground-tertiary">最新</div>
              <div className="tabnum text-lg font-semibold text-foreground">
                1 {series!.currency} = {fmtRate(series!.latest!)}
                <span className="ml-1 text-sm font-normal text-foreground-secondary">CNY</span>
              </div>
            </div>
            <div>
              <div className="text-xs text-foreground-tertiary">区间涨跌</div>
              <div
                className={cn(
                  "tabnum text-lg font-semibold",
                  series!.change_pct == null
                    ? "text-foreground-secondary"
                    : up
                      ? "text-positive"
                      : "text-negative",
                )}
              >
                {series!.change_pct == null
                  ? "—"
                  : `${up ? "+" : ""}${series!.change_pct.toFixed(2)}%`}
              </div>
            </div>
            {unit !== 1 && (
              <div className="text-xs text-foreground-tertiary">
                纵轴按 100 {series!.currency} 缩放显示，鼠标悬停看单位精确值
              </div>
            )}
          </div>
        )}

        {/* 图表卡：加载 / 空 / 错误 / 数据 四态。 */}
        <Card className="mt-4">
          <CardContent className="p-4 sm:p-5">
            {loading ? (
              <Skeleton className="h-[320px] w-full" />
            ) : error ? (
              <div className="flex h-[320px] flex-col items-center justify-center gap-1 text-center">
                <p className="text-sm text-destructive">加载失败</p>
                <p className="text-xs text-foreground-tertiary">{error}</p>
              </div>
            ) : !hasData ? (
              <div className="flex h-[320px] flex-col items-center justify-center gap-1 text-center">
                <p className="text-sm text-foreground-secondary">该时段暂无牌价数据</p>
                <p className="text-xs text-foreground-tertiary">
                  中行牌价自 2026-07-02 起入库，更早历史不回填；换个币种或时段试试
                </p>
              </div>
            ) : (
              <EChart option={option} height={340} />
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
