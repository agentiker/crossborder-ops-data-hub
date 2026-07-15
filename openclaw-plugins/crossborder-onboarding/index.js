// OpenClaw 命令插件：把跨境电商运营 bot 的确定性命令做成 handler 直出，绕开 LLM。
//
// 命令：
//  · /start  ——「指引/onboarding」固定文案（见 ONBOARDING_ZH）。
//  · /scope  ——「切换默认查询范围」：拉 data-hub ops_scopes，渲染成飞书交互卡（一范围一个
//               按钮），点按钮 → 卡片回调路由回本插件 → 写 binding。也支持 `/scope <编号>` 手打。
//
// 为什么绕 LLM：都不需要推理，交给弱模型会不稳定（时而纯文字、漏项、不出卡）。命令由 openclaw
// 在 agent 之前拦截，handler 直接返回，100% 确定。
//
// 交互卡机制（反查 openclaw / openclaw-lark 源码确认）：
//  · handler 返回 { text: JSON.stringify(飞书v2卡) }，openclaw-lark deliver.detectCardJson 识别
//    出整串是卡 JSON → 发 msg_type=interactive。
//  · 按钮带 value.action = "scope:<scope_key>"；点按 → card.action.trigger →
//    openclaw-lark dispatchFeishuPluginInteractiveHandler 按首个 ":" 前的 namespace("scope")
//    路由到 api.registerInteractiveHandler 注册的 handler，payload = ":" 之后全部（即 scope_key，
//    含 "shop:xxx" 的冒号也归 payload）。handler 返回 { toast:{...} } 反馈。

const ECOM_ACCOUNTS = new Set(["ecom-app", "ecom-app-gtl"]);

const ONBOARDING_ZH = `👋 你好，我是 **Adis**，跨境电商数据化运营顾问。

我支持多平台跨境电商数据，目前已接入 **印尼 TikTok Shop**（更多平台陆续开放），能帮你查：
- 📦 库存 / 低库存
- 🛍️ 商品目录
- 💰 GMV / 订单 / 销量
- 📈 销售趋势
- 🔥 爆款单品榜

📬 我还会**每天早上自动推送经营日报**，**待发货超时、库存快断货时主动提醒**——不用你盯着。

💡 可用底部菜单先选 **平台 / 区域**（如 TikTok → 印尼）再提问；也可直接发「印尼」「全部」切换。
直接问就行，例 "印尼库存"、"本周 GMV"、"近 7 天爆款"。
想重新开始发 /new 清空会话（不会清空聊天记录），再看一次指引发「/start」。`;

// ── data-hub HTTP 小客户端 ─────────────────────────────────────────────────
// 凭证来自 openclaw.json env.vars（命令 ctx.config 可读）。交互回调 ctx 没有 config，故把凭证
// 从 /scope 命令运行时缓存到模块级，供按钮回调复用（同一 gateway 进程内）。

let _hubCache = null; // { base, token }

function hubFromCtx(ctx) {
  const vars = (ctx && ctx.config && ctx.config.env && ctx.config.env.vars) || {};
  const base = String(vars.DATA_HUB_URL || "http://127.0.0.1:8000")
    .replace(/\/mcp\/?$/, "")
    .replace(/\/$/, "");
  const hub = { base, token: vars.DATA_HUB_TOKEN || "" };
  _hubCache = hub; // 缓存供交互回调用
  return hub;
}

function tenantHeader(accountId) {
  return accountId && String(accountId).startsWith("ecom-app") ? { "X-Account-Id": accountId } : {};
}

async function apiGetScopes(hub, accountId) {
  const resp = await fetch(`${hub.base}/api/data/scopes`, {
    headers: { "X-Internal-Token": hub.token, ...tenantHeader(accountId) },
  });
  if (!resp.ok) throw new Error(`ops_scopes HTTP ${resp.status}`);
  const data = await resp.json();
  return (data && data.items) || [];
}

async function apiSetBinding(hub, scopeKey, openId, accountId) {
  const resp = await fetch(`${hub.base}/api/data/scope/binding`, {
    method: "POST",
    headers: {
      "X-Internal-Token": hub.token,
      "Content-Type": "application/json; charset=utf-8",
      ...tenantHeader(accountId),
    },
    body: JSON.stringify({ open_id: openId, scope_key: scopeKey || null }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error((data && data.detail) || `set_binding HTTP ${resp.status}`);
  return data; // { scope, scope_key, is_set }
}

function optLine(i, o) {
  const desc = o.description ? `（${o.description}）` : "";
  return `${i + 1}. ${o.scope_name}${desc}`;
}

// 纯文字兜底（不支持交互卡的渠道 / 卡构建失败）——仍可 /scope 编号 切换。
function listBody(items) {
  return (
    items.map((o, i) => optLine(i, o)).join("\n") +
    "\n\n点下方按钮，或回复 `/scope 编号`（例 `/scope 1`）切换。切换后不带范围词的查询都按此范围。"
  );
}

// 飞书 v2 CardKit 卡：一范围一个按钮，value.action="scope:<key>" 供回调路由。
function scopeCardJson(items) {
  const buttons = items.map((o, i) => {
    const desc = o.description ? `（${o.description}）` : "";
    const val = { action: `scope:${o.scope_key || ""}` };
    return {
      tag: "button",
      text: { tag: "plain_text", content: `${o.scope_name}${desc}` },
      type: i === 0 ? "primary" : "default",
      width: "fill",
      value: val, // 飞书要求交互组件带 value，否则 200340
      behaviors: [{ type: "callback", value: val }],
    };
  });
  return {
    schema: "2.0",
    config: { wide_screen_mode: true },
    header: { template: "blue", title: { tag: "plain_text", content: "🎯 切换默认查询范围" } },
    body: {
      elements: [
        { tag: "markdown", content: "点选要切换到的默认范围（切换后不带范围词的查询都按此范围）：" },
        ...buttons,
      ],
    },
  };
}

const plugin = {
  id: "crossborder-onboarding",
  name: "Crossborder Onboarding",
  description:
    "跨境电商运营 bot 的确定性命令：/start 指引 + /scope 切换默认范围（交互卡），handler 直出绕开 LLM。",
  configSchema: { type: "object", additionalProperties: false, properties: {} },
  register(api) {
    api.registerCommand({
      name: "start",
      description: "显示使用指引",
      channels: ["feishu"],
      acceptsArgs: false,
      requireAuth: false,
      handler(ctx) {
        if (ECOM_ACCOUNTS.has(ctx && ctx.accountId)) return { text: ONBOARDING_ZH };
        return { continueAgent: true };
      },
    });

    api.registerCommand({
      name: "scope",
      description: "切换默认查询范围（列出店铺/平台/区域，点按或回复编号切换）",
      channels: ["feishu"],
      acceptsArgs: true,
      requireAuth: true, // 改 binding 属敏感操作，仅授权发送者
      async handler(ctx) {
        const openId = ctx && ctx.senderId;
        if (!openId) return { text: "无法识别你的身份，暂时无法切换范围。" };
        const hub = hubFromCtx(ctx);
        let items;
        try {
          items = await apiGetScopes(hub, ctx.accountId);
        } catch (e) {
          return { text: `拉取范围列表失败：${e && e.message ? e.message : e}` };
        }
        if (!items.length) return { text: "当前租户暂无可切换的范围。" };

        const arg = (ctx.args || "").trim();
        if (!arg) {
          // 无参 → 出交互卡（text 即卡 JSON，openclaw-lark 自动发 interactive）
          try {
            return { text: JSON.stringify(scopeCardJson(items)) };
          } catch (_e) {
            return { text: "🎯 **切换默认查询范围**\n\n" + listBody(items) };
          }
        }

        // 有参：编号（1-based）或直接 scope_key
        let target = null;
        const n = Number(arg);
        if (Number.isInteger(n) && n >= 1 && n <= items.length) target = items[n - 1];
        else target = items.find((o) => o.scope_key === arg) || null;
        if (!target) return { text: `没找到「${arg}」对应的范围。\n\n${listBody(items)}` };
        try {
          const res = await apiSetBinding(hub, target.scope_key, openId, ctx.accountId);
          const shown = (res && res.scope) || target.scope_name;
          return { text: `✅ 已切换默认范围到 **${target.scope_name}**（${shown}）。\n之后不带范围词的查询都按此范围。` };
        } catch (e) {
          return { text: `切换失败：${e && e.message ? e.message : e}` };
        }
      },
    });

    // 卡片按钮回调：namespace "scope"，payload = scope_key（"" = 全部店铺）
    api.registerInteractiveHandler({
      channel: "feishu",
      namespace: "scope",
      async handler(ctx) {
        const openId = ctx && ctx.senderId;
        const scopeKey = (ctx && ctx.payload) || ""; // "" = 全部店铺
        if (!openId) return { toast: { type: "error", content: "无法识别身份" } };
        if (!_hubCache) return { toast: { type: "warning", content: "会话已过期，请重新发送 /scope" } };
        try {
          const res = await apiSetBinding(_hubCache, scopeKey, openId, ctx.accountId);
          const shown = (res && res.scope) || "所选范围";
          return { toast: { type: "success", content: `✅ 已切换到 ${shown}` } };
        } catch (e) {
          return { toast: { type: "error", content: `切换失败：${e && e.message ? e.message : e}` } };
        }
      },
    });
  },
};

export default plugin;
