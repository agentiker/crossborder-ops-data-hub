// OpenClaw 命令插件：把跨境电商运营 bot 的确定性命令做成 handler 直出，绕开 LLM。
//
// 两个命令：
//  · /start  ——「指引/onboarding」固定文案（见 ONBOARDING_ZH）。
//  · /scope  ——「切换默认查询范围」：拉 data-hub ops_scopes 列出规范分组选项
//               （全部店铺 / 平台组 / 区域组 / 逐店），`/scope <编号>` 直接切换。
//
// 为什么绕 LLM：这两件事都不需要推理——onboarding 是固定字符串；切范围是"列选项+按选择写
// binding"的确定性流程。交给弱模型会时而纯文字、时而漏项、时而不出卡（不稳定）。命令由
// openclaw 在 agent 之前拦截，handler 直接返回，100% 确定。
//
// 触发：飞书菜单按钮 / 用户手打（命令须 / 前缀 ASCII）。裸词自然语言仍走 LLM 兜底。

// 仅对电商租户（ecom / ecom-gtl）返回电商文案；其它账号（main-app 运维等）onboarding 回落
// 给 agent。/scope 则所有账号可用（运维 app 也用来配合测试）。键名以飞书 channels.feishu
// .accounts 为准。
const ECOM_ACCOUNTS = new Set(["ecom-app", "ecom-app-gtl"]);

// 与 SKILL.md 的 ONBOARDING 块逐字一致（含 emoji、**加粗**、中文书名号、空行）。
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
// 复用 openclaw.json env.vars 里的 DATA_HUB_URL / DATA_HUB_TOKEN（与 mcp.servers.data-hub
// 同源凭证）。X-Account-Id 只在电商租户时显式带（main-app 运维 → 省略，data-hub 回落 DEFAULT，
// prod 即 gtl），保证读(ops_scopes)写(set_scope_binding)命中同一租户。

function dataHubBase(ctx) {
  const vars = (ctx && ctx.config && ctx.config.env && ctx.config.env.vars) || {};
  let url = vars.DATA_HUB_URL || "http://127.0.0.1:8000";
  return { base: String(url).replace(/\/mcp\/?$/, "").replace(/\/$/, ""), token: vars.DATA_HUB_TOKEN || "" };
}

function tenantHeaders(ctx) {
  const h = {};
  const acc = ctx && ctx.accountId;
  if (acc && String(acc).startsWith("ecom-app")) h["X-Account-Id"] = acc;
  return h;
}

async function fetchScopes(ctx) {
  const { base, token } = dataHubBase(ctx);
  const resp = await fetch(`${base}/api/data/scopes`, {
    headers: { "X-Internal-Token": token, ...tenantHeaders(ctx) },
  });
  if (!resp.ok) throw new Error(`ops_scopes HTTP ${resp.status}`);
  const data = await resp.json();
  return (data && data.items) || [];
}

async function setBinding(ctx, scopeKey, openId) {
  const { base, token } = dataHubBase(ctx);
  const resp = await fetch(`${base}/api/data/scope/binding`, {
    method: "POST",
    headers: {
      "X-Internal-Token": token,
      "Content-Type": "application/json; charset=utf-8",
      ...tenantHeaders(ctx),
    },
    body: JSON.stringify({ open_id: openId, scope_key: scopeKey || null }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error((data && data.detail) || `set_binding HTTP ${resp.status}`);
  return data; // { scope, scope_key, is_set }
}

// 选项 → 一行展示文案（编号 + 名称 + 描述）
function optLine(i, o) {
  const desc = o.description ? `（${o.description}）` : "";
  return `${i + 1}. ${o.scope_name}${desc}`;
}

function renderList(items) {
  const lines = items.map((o, i) => optLine(i, o));
  return (
    "🎯 **切换默认查询范围**\n\n" +
    lines.join("\n") +
    "\n\n回复 `/scope 编号` 切换（例 `/scope 1`）。切换后，之后不带范围词的查询都按这个范围。"
  );
}

const plugin = {
  id: "crossborder-onboarding",
  name: "Crossborder Onboarding",
  description:
    "跨境电商运营 bot 的确定性命令：/start 指引 + /scope 切换默认范围，handler 直出绕开 LLM。",
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
      description: "切换默认查询范围（列出店铺/平台/区域，回复编号切换）",
      channels: ["feishu"],
      acceptsArgs: true,
      requireAuth: true, // 改 binding 属敏感操作，仅授权发送者
      async handler(ctx) {
        const openId = ctx && ctx.senderId;
        if (!openId) return { text: "无法识别你的身份，暂时无法切换范围。" };
        let items;
        try {
          items = await fetchScopes(ctx);
        } catch (e) {
          return { text: `拉取范围列表失败：${e && e.message ? e.message : e}` };
        }
        if (!items.length) return { text: "当前租户暂无可切换的范围。" };

        const arg = (ctx.args || "").trim();
        if (!arg) return { text: renderList(items) };

        // 解析编号（1-based）或直接 scope_key
        let target = null;
        const n = Number(arg);
        if (Number.isInteger(n) && n >= 1 && n <= items.length) target = items[n - 1];
        else target = items.find((o) => o.scope_key === arg) || null;
        if (!target) {
          return { text: `没找到「${arg}」对应的范围。\n\n${renderList(items)}` };
        }
        try {
          const res = await setBinding(ctx, target.scope_key, openId);
          const shown = (res && res.scope) || target.scope_name;
          return { text: `✅ 已切换默认范围到 **${target.scope_name}**（${shown}）。\n之后不带范围词的查询都按此范围。` };
        } catch (e) {
          return { text: `切换失败：${e && e.message ? e.message : e}` };
        }
      },
    });
  },
};

export default plugin;
