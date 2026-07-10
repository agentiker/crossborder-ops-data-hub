import animate from "tailwindcss-animate";

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // StoreClaw 描边三级：半透明叠加真值（var() 直引，非 hsl 包裹）
        border: {
          DEFAULT: "var(--border)",
          shallow: "var(--border-shallow)",
          deep: "var(--border-deep)",
        },
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        // background-solid 别名供 fork 的 bg-background-solid / from-background-solid 解析
        background: {
          DEFAULT: "hsl(var(--background))",
          solid: "hsl(var(--background))",
        },
        // StoreClaw 前景三级：半透明叠加真值（var() 直引）
        foreground: {
          DEFAULT: "var(--foreground)",
          secondary: "var(--foreground-secondary)",
          tertiary: "var(--foreground-tertiary)",
        },
        // StoreClaw 填充层（bg-fill / bg-fill-deep…）：半透明叠加真值（var() 直引）
        // default 别名供 fork 的 bg-fill-default / hover:bg-fill-default 原样解析
        fill: {
          DEFAULT: "var(--fill)",
          default: "var(--fill)",
          shallow: "var(--fill-shallow)",
          deep: "var(--fill-deep)",
        },
        primary: {
          DEFAULT: "hsl(var(--primary) / <alpha-value>)",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive) / <alpha-value>)",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        // 业务语义色：涨/跌 + 告警分级（看板与告警卡复用）。alpha slot 支持 /15 浅底叠加徽章。
        positive: "hsl(var(--positive) / <alpha-value>)",
        negative: "hsl(var(--negative) / <alpha-value>)",
        success: "hsl(var(--success) / <alpha-value>)",
        warning: "hsl(var(--warning) / <alpha-value>)",
        caution: "hsl(var(--caution) / <alpha-value>)", // 黄：偏低/提示
        info: "hsl(var(--info) / <alpha-value>)",       // 蓝：监控中/信息
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        // StoreClaw 全程 GoogleSansFlex（自托管），缺文件时回退系统无衬线栈
        sans: [
          "GoogleSansFlex", "ui-sans-serif", "system-ui", "-apple-system",
          "BlinkMacSystemFont", "Segoe UI", "PingFang SC", "Hiragino Sans GB",
          "Microsoft YaHei", "sans-serif",
        ],
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        // Dialog 专用入场：keyframe 的 transform 会整体覆盖元素 class 里的 transform，
        // 所以必须把居中位移 translate(-50%,-50%) 写进每一帧，否则动画期间弹窗掉到下方、
        // 结束才跳回中间（fade-in 的 translateY 就是这个坑）。
        "dialog-in": {
          from: { opacity: "0", transform: "translate(-50%, -50%) scale(0.96)" },
          to: { opacity: "1", transform: "translate(-50%, -50%) scale(1)" },
        },
        // 时段徽标在线绿点呼吸
        "pulse-dot": {
          "0%, 100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.45", transform: "scale(0.85)" },
        },
        // forkStoreClaw：欢迎页绿点呼吸（animate-pulse-slow，照搬 fork 关键帧值）
        "pulse-slow": {
          "0%, 100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.45", transform: "scale(0.8)" },
        },
        // forkStoreClaw：入场上浮 + 问候 emoji 摆动
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        wiggle: {
          "0%, 100%": { transform: "rotate(0deg)" },
          "15%": { transform: "rotate(-8deg)" },
          "30%": { transform: "rotate(8deg)" },
          "45%": { transform: "rotate(-3deg)" },
          "60%": { transform: "rotate(0deg)" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        "fade-in": "fade-in 0.18s ease-out",
        "dialog-in": "dialog-in 0.18s ease-out",
        "pulse-dot": "pulse-dot 2s ease-in-out infinite",
        "pulse-slow": "pulse-slow 2s ease-in-out infinite",
        "fade-up": "fade-up 0.5s ease both",
        wiggle: "wiggle 3.5s ease-in-out infinite",
      },
    },
  },
  plugins: [animate],
};
