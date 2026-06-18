import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useTheme } from "@/theme";

// 按需注册（tree-shake）：只引看板用到的折线/条形 + 网格/提示/图例 + Canvas 渲染。
echarts.use([
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
]);

export interface ChartTokens {
  text: string;
  sub: string;
  grid: string;
  primary: string;
  positive: string;
  negative: string;
  warning: string;
}

function readTokens(): ChartTokens {
  const s = getComputedStyle(document.documentElement);
  const v = (n: string) => {
    const raw = s.getPropertyValue(n).trim();
    return raw ? `hsl(${raw})` : "#888";
  };
  return {
    text: v("--foreground"),
    sub: v("--muted-foreground"),
    grid: v("--border"),
    primary: v("--primary"),
    positive: v("--positive"),
    negative: v("--negative"),
    warning: v("--warning"),
  };
}

// 随主题变化重算 token 颜色（系列/坐标轴/文字用）；切主题后 CSS 变量已更新再读取。
export function useChartTokens(): ChartTokens {
  const { theme } = useTheme();
  const [tok, setTok] = useState<ChartTokens>(() => readTokens());
  useEffect(() => {
    setTok(readTokens());
  }, [theme]);
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
