---
name: fetch-web-doc
description: 抓取被 JS/SPA 渲染或需登录态的网页正文（WebSearch/WebFetch/curl 都拿不到内容时）。当要查 TikTok / 各类开发者文档站的接口细节、而内置联网工具报错或只返回空壳时使用。用无头浏览器渲染 + 拦截内容 XHR 拿 JSON。
---

# 抓取 SPA / JS 渲染的网页文档

内置联网工具会在三种**不同**场景失败，先对号入座，别一律归为「被墙」：

| 症状 | 真因 | token/cookie 有用吗 |
|---|---|---|
| `curl` HTTP 200 但正文是空 HTML+一坨 JS | 目标是 **SPA**，正文靠浏览器跑 JS 再发 XHR 拉，curl 不跑 JS | ❌ 没用 |
| `WebFetch` 报 `Unable to verify if domain ... is safe` | Anthropic 服务端**域名安全校验**没过 | ❌ 服务端环节，塞不进凭证 |
| `WebSearch` 报 `empty or malformed response ... gateway` | 中转**网关**异常/抽风 | ❌ 与目标登录态无关 |

**先实测 `curl -o /dev/null -w "%{http_code} %{size_download}"` 确认网络本身通不通**（多半是通的，问题在 SPA），再决定要不要上无头浏览器。

## 核心套路：拦截「内容 XHR」拿 JSON（比抓渲染后 DOM 更可靠）

SPA 文档站的正文通常由一个后台 API 返回 JSON，页面再渲染。**直接截获那个 JSON 请求**，比 `document.body.innerText`（常被 iframe/shadow DOM 挡成几百字）干净得多。

一次性环境（装在 /tmp，不污染项目）：
```bash
cd /tmp && npm init -y >/dev/null 2>&1 && npm install playwright@1.61.1 >/dev/null 2>&1 \
  && npx playwright install chromium >/dev/null 2>&1
```

第一步——**嗅探**：跑一遍，把所有 JSON 响应 URL 打出来，肉眼找哪个是正文内容 API（名字常含 doc/node/content/article/get）：
```bash
cd /tmp && cat > sniff.js <<'EOF'
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ headless: true });
  const p = await b.newPage();
  p.on('response', async r => {
    const ct = r.headers()['content-type'] || '';
    if (ct.includes('json')) console.log(r.status(), r.url().slice(0,160));
  });
  try { await p.goto(process.argv[2], { waitUntil: 'networkidle', timeout: 45000 }); } catch(e){}
  await p.waitForTimeout(2500);
  await b.close();
})();
EOF
node sniff.js "https://目标文档URL"
```

第二步——**截获正文 JSON** 存盘（把 URL 片段填进 includes）：
```bash
cd /tmp && cat > fetch.js <<'EOF'
const { chromium } = require('playwright'); const fs = require('fs');
(async () => {
  const b = await chromium.launch({ headless: true });
  const p = await b.newPage(); let body = null;
  p.on('response', async r => {
    if (r.url().includes('内容API的URL片段')) { try { body = await r.text(); } catch(e){} }
  });
  try { await p.goto(process.argv[2], { waitUntil: 'networkidle', timeout: 45000 }); } catch(e){}
  await p.waitForTimeout(2500);
  fs.writeFileSync('/tmp/doc.json', body || '{}'); console.log(body ? 'OK '+body.length : 'FAIL');
  await b.close();
})();
EOF
node fetch.js "https://目标文档URL"
```

第三步——**解析**（正文常在 `data.content`，是 HTML+markdown 混排，去标签后 grep）：
```bash
python3 -c "
import json,re,html
d=json.load(open('/tmp/doc.json')); c=d['data']['content']
t=re.sub(r'<[^>]+>',' ',c); t=re.sub(r'\s+',' ',html.unescape(t)); print(t[:3000])"
```

## 需要登录态时

`launch()` 换成带持久化 profile：`chromium.launchPersistentContext('/tmp/pw-profile', {headless:false})`，先手动登录一次（headless:false 弹窗），cookie 落在 profile 里，之后 headless 复用。**这种才是 token/cookie 真正有用的场景**——凭证塞在浏览器上下文里，不是塞给内置工具。

## 已验证案例（2026-07-07 TikTok Marketing API 文档）

- 站点 `business-api.tiktok.com/portal/docs?id=<id>` 是 Next.js SPA，curl 只拿到 JS 壳。
- 内容 API = `/gateway/api/doc/client/node/get/`，返回 `{code,msg,data:{title,type,content}}`，`content` 37KB 全正文。
- 靠它拿到 GMV Max 报表接口全部细节（见 [[roi-roas-alert-data-source]]）。

## 收尾

`rm -f /tmp/sniff.js /tmp/fetch.js /tmp/doc.json`。playwright/chromium 装在 `/tmp/node_modules` 可留着复用，要彻底清就 `rm -rf /tmp/node_modules /tmp/package*.json`。
