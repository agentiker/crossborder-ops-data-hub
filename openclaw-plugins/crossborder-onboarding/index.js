// OpenClaw 命令插件：把跨境电商运营 bot 的「指引/onboarding」做成确定性命令。
//
// 为什么存在：ecom / ecom-gtl 这两个客户 bot 跑在弱模型 mimo 上，让它「逐字复制」
// 一段固定 onboarding 文案不可靠（会自由生成、漏字、混入禁用 bullet）。onboarding 是
// 不依赖数据、不需要推理的固定字符串，没理由经过 LLM。这里注册一个 /start 命令，
// handler 直出固定文案——命中后 openclaw 走纯文本下发（dispatchSystemCommand），
// 完全不进 agent/LLM。
//
// 触发：飞书菜单「指引」按钮发送 /start，或用户手打 /start（命令必须 / 前缀且 ASCII，
// 所以无法把裸词「指引」做成命令——裸词仍走 SKILL.md 的 LLM 兜底）。
//
// ⚠️ 文案权威来源就是本文件的 ONBOARDING_ZH。SKILL.md 里 ===ONBOARDING_BEGIN/END===
// 之间那段是「用户手打裸词时的 LLM 兜底」，必须与这里逐字一致——改一处要同步另一处。

// 仅对这两个飞书账号（ecom / ecom-gtl）返回电商文案；其它账号（main-app 等）回落给 agent，
// 不污染。键名以 ~/.openclaw/openclaw.json 的 channels.feishu.accounts 为准。
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

const plugin = {
  id: "crossborder-onboarding",
  name: "Crossborder Onboarding",
  description:
    "把跨境电商运营 bot 的 /start 指引做成确定性命令：handler 直出固定文案，绕开 LLM。",
  configSchema: {
    type: "object",
    additionalProperties: false,
    properties: {},
  },
  register(api) {
    api.registerCommand({
      name: "start",
      description: "显示使用指引",
      channels: ["feishu"],
      acceptsArgs: false,
      // onboarding 无敏感信息，任何能给 bot 发消息的人都该能看到。
      requireAuth: false,
      handler(ctx) {
        if (ECOM_ACCOUNTS.has(ctx && ctx.accountId)) {
          return { text: ONBOARDING_ZH };
        }
        // 非电商账号（main/crayfish 等）：交回 agent 正常处理，不返回电商文案。
        return { continueAgent: true };
      },
    });
  },
};

export default plugin;
