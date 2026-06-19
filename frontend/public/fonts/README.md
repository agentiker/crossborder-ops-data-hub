# 自托管字体

StoreClaw 全程使用 **GoogleSansFlex**。要像素级对齐，把字体文件放在此目录：

```
public/fonts/GoogleSansFlex.woff2
```

构建后由 Vite 原样拷到 `dist/fonts/`，线上经 `/app/` 前缀服务于 `/app/fonts/GoogleSansFlex.woff2`。
`src/index.css` 的 `@font-face` 已指向该路径，`tailwind.config.js` 的 `fontFamily.sans` 已把
`GoogleSansFlex` 置首位。**放入文件即生效，无需改代码。**

## 缺文件时的行为

文件不存在 → 运行时 `/app/fonts/GoogleSansFlex.woff2` 404 → 浏览器静默回退到系统无衬线栈
（`ui-sans-serif / system-ui / PingFang SC / …`）。`font-display: swap` 保证无 FOIT。布局/间距/
配色不受影响，仅字形与 StoreClaw 真身有细微差异。构建**不会**因此失败（用的是 `/app/` 绝对路径，
Vite 不做构建期解析）。

## ⚠️ 许可提示

GoogleSansFlex 是 Google 的字体，自托管/再分发前请自行确认授权边界。本仓库**不**附带该字体文件
（`.gitignore` 已排除 `public/fonts/*.woff2`），由使用方按合规渠道获取后放入。
