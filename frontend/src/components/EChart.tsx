import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { BarChart, FunnelChart, GaugeChart, LineChart, PieChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

// 按需注册（tree-shake）：看板用到的折线/条形/环图/仪表盘/漏斗 + 网格/提示/图例 + Canvas 渲染。
echarts.use([
  LineChart,
  BarChart,
  PieChart,
  GaugeChart,
  FunnelChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
]);

export interface ChartTokens {
  text: string;
  sub: string;
  grid: string;
  card: string;
  primary: string;
  positive: string;
  negative: string;
  warning: string;
}

function readTokens(): ChartTokens {
  const s = getComputedStyle(document.documentElement);
  // HSL 通道值（shadcn 基础色）需 hsl() 包裹。
  // ⚠️ CSS 变量是 CSS4 空格分隔（如 "158 18% 12%"），但 zrender 的颜色解析只认逗号分隔的
  // hsl()——空格写法它解析失败 → ECharts 在 hover/emphasis 推导高亮色时得到透明，扇区被画没
  // （"鼠标指上去这块就不显示"）。故规整成 "158, 18%, 12%"。canvas 两种写法都吃，无副作用。
  const hsl = (n: string) => {
    const raw = s.getPropertyValue(n).trim();
    if (!raw) return "#888";
    return `hsl(${raw.includes(",") ? raw : raw.replace(/\s+/g, ", ")})`;
  };
  // 半透明真值（StoreClaw 前景/描边）直接取用，不能再包 hsl()
  const raw = (n: string) => s.getPropertyValue(n).trim() || "#888";
  return {
    text: raw("--foreground"),
    sub: hsl("--muted-foreground"),
    grid: raw("--border"),
    card: hsl("--card"),
    primary: hsl("--primary"),
    positive: hsl("--positive"),
    negative: hsl("--negative"),
    warning: hsl("--warning"),
  };
}

// 单浅色主题：token 颜色一次读定（系列/坐标轴/文字用），无主题切换需重算。
export function useChartTokens(): ChartTokens {
  const [tok] = useState<ChartTokens>(() => readTokens());
  return tok;
}

interface Props {
  option: echarts.EChartsCoreOption;
  className?: string;
  height?: number;
}

// 薄封装：init / setOption(notMerge) / ResizeObserver / dispose。颜色由 option 携带，
// 故主题切换时上层重建 option 即可，无需重新 init。
export function EChart({ option, className, height = 280 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    chart.current = echarts.init(ref.current, null, { renderer: "canvas" });
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, true);
  }, [option]);

  return <div ref={ref} className={className} style={{ height, width: "100%" }} />;
}
