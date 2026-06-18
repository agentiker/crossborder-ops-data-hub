// 内嵌迷你趋势线（无依赖 SVG）。MetricCard 招牌的一部分。
interface Props {
  data: number[];
  className?: string;
  width?: number;
  height?: number;
}

export function Sparkline({ data, className, width = 96, height = 28 }: Props) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const stepX = width / (data.length - 1);
  const pts = data.map((v, i) => {
    const x = i * stepX;
    // 上下留 2px 余量，避免线贴边被裁
    const y = height - 2 - ((v - min) / span) * (height - 4);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const last = data[data.length - 1];
  const first = data[0];
  // 颜色跟随趋势：升=涨色、降=跌色、平=主色
  const stroke =
    last > first ? "hsl(var(--positive))" : last < first ? "hsl(var(--negative))" : "hsl(var(--primary))";

  return (
    <svg
      className={className}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      fill="none"
      aria-hidden="true"
    >
      <polyline
        points={pts.join(" ")}
        stroke={stroke}
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
