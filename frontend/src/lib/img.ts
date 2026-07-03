// 商品图 URL 高清变换（TikTok CDN tplv 动态处理）。
// 列表用 300 缩略图（快/省流量），点击看大图时就地把 URL 变换成 origin 原图——
// 零后端改动、对所有历史图片即时生效。
//
// TikTok CDN 缩略图 URL 形如：
//   https://...~tplv-aphluv4xwc-resize-jpeg:300:300.jpeg?...
// 把 `resize-{fmt}:WxH` 段替换为 `origin-{fmt}` 即得原图（实测 790×790 / 71KB）。
// 非 TikTok CDN / 不匹配的 URL 原样返回（fail-safe，绝不改坏成 404）。

const TPLV_RESIZE = /~tplv-([a-z0-9]+)-resize-(jpeg|webp|png)(?::\d+:\d+)?/i;

export function hiResUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  // 只处理带 tplv resize 段的 CDN URL；其余原样返回。
  if (!TPLV_RESIZE.test(url)) return url;
  return url.replace(TPLV_RESIZE, "~tplv-$1-origin-$2");
}
