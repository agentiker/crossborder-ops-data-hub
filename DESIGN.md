---
name: 跨境运营数据中枢
description: 橄榄书房 — 墨绿近黑与橄榄暖白构成的可信经营账册
colors:
  pine-ink: "#192420"
  pine-ink-88: "#192420e0"
  pine-ink-70: "#192420b3"
  pine-ink-50: "#19242080"
  olive-paper: "#f7f7f3"
  card-white: "#fdfdfb"
  muted-surface: "#f0eedd"
  muted-ink: "#5e6e67"
  hairline: "#0000001a"
  hairline-shallow: "#0000000d"
  field-stroke: "#dbdac7"
  positive-pine: "#2c8c5f"
  negative-red: "#db2424"
  destructive-red: "#d32222"
  warning-amber: "#ce7612"
  caution-gold: "#af8509"
  info-blue: "#1c6dca"
typography:
  display:
    fontFamily: "GoogleSansFlex, ui-sans-serif, system-ui, -apple-system, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "GoogleSansFlex, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.15rem"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "normal"
  title:
    fontFamily: "GoogleSansFlex, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "-0.01em"
  body:
    fontFamily: "GoogleSansFlex, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: "GoogleSansFlex, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "0.02em"
rounded:
  sm: "8px"
  md: "10px"
  lg: "12px"
  pill: "9999px"
spacing:
  card-pad: "20px"
  field-x: "12px"
  gap: "12px"
components:
  button-primary:
    backgroundColor: "{colors.pine-ink}"
    textColor: "{colors.olive-paper}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "0 16px"
  button-secondary:
    backgroundColor: "{colors.muted-surface}"
    textColor: "{colors.pine-ink}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "0 16px"
  button-outline:
    backgroundColor: "transparent"
    textColor: "{colors.pine-ink}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "0 16px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.pine-ink}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "0 16px"
  card:
    backgroundColor: "{colors.card-white}"
    textColor: "{colors.pine-ink-88}"
    rounded: "{rounded.lg}"
    padding: "20px"
  badge-default:
    backgroundColor: "{colors.pine-ink}"
    textColor: "{colors.pine-ink}"
    rounded: "{rounded.pill}"
    padding: "2px 10px"
  badge-success:
    backgroundColor: "{colors.positive-pine}"
    textColor: "{colors.positive-pine}"
    rounded: "{rounded.pill}"
    padding: "2px 10px"
  input:
    backgroundColor: "transparent"
    textColor: "{colors.pine-ink-88}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "0 12px"
---

# Design System: 跨境运营数据中枢

## 1. Overview

**Creative North Star: "The Olive Study / 橄榄书房"**

这是一间安静、有质感的书房,不是一块冷峻的仪表盘。墨绿近黑的家具落在橄榄暖白的纸面上,数字像账本上的笔迹一样整齐对齐。老板推门进来,一眼就能读懂今天生意的健康;运营在这里久坐,处理告警、翻补货、和 AI 对话查数,不会被刺眼的色彩和密集的表格压垮。温润、可信、克制——书房的气质来自留白与材质,而非装饰。

系统延续既有 StoreClaw 基因:全程单一无衬线 `GoogleSansFlex`(以重量而非字体家族区分层级),前景/填充/描边都走**半透明叠加**真值,以便正确复合到橄榄暖白底上——这是「暖」的来源,不是给底色硬加一层米黄。所有指标与金额走 `tabular-nums` 等宽数字,对齐成账册感:数字是这套系统的主角,身份靠数字呈现立住。

它明确拒绝三样东西(承接 PRODUCT.md 的 anti-references):**老派后台管理**的蓝灰配色与密集表格堆砌;**花哨 SaaS 落地页**的大渐变、英雄大数字、卡片网格、每段一个小标签 eyebrow;**过度活泼消费 app**的强色彩、卡通插画与弹跳动效。

**Key Characteristics:**
- 单一浅色主题:橄榄暖白纸面 + 墨绿近黑家具,无暗色模式
- 半透明叠加的前景/填充/描边三级,复合出暖意
- 等宽数字账册感(`tabular-nums`)是招牌
- 克制的语义色:涨跌/告警分级用色克制,不装饰
- 温润质感:大圆角(12px)、极淡阴影、柔和浮起

## 2. Colors

橄榄暖白纸面托起墨绿近黑的内容,辅以一组克制的经营语义色;主色稀有出现,大面积留给中性。

### Primary
- **深松墨绿 Deep Pine Ink** (`#192420` / `hsl(158 18% 12%)`):签名主色。墨绿近黑,用于主按钮、wordmark、关键文字。它几乎是黑,但在暖白底上透出松林的绿调,是整间书房的「家具色」。代码库中前景文字以同一基色的半透明叠加呈现三级:`rgba(25,36,32,.88)` 正文 / `.70` 次级 / `.50` 三级(对应 `#192420e0/b3/80`)。

### Neutral
- **橄榄暖白 Olive Paper** (`#f7f7f3` / `hsl(58 20% 96%)`):页面纸面底色。`<html>` 实际再压一层极淡橄榄 `rgba(107,104,31,.05)` 衬出卡片浮起。
- **卡片微暖白 Card White** (`#fdfdfb` / `hsl(56 38% 99%)`):卡片/弹层背景,比纸面略亮,自然浮起。
- **柔和暖灰面 Muted Surface** (`#f0eedd` / `hsl(56 16% 92%)`):次级按钮、代码块、表头等低强度填充。
- **暖灰墨 Muted Ink** (`#5e6e67` / `hsl(156 8% 40%)`):副标、提示、占位说明文字。**注意可读性下限**——见下方 Named Rule。
- **发丝描边 Hairline** (`rgba(0,0,0,.1)` / `#0000001a`):默认描边;更浅一级 `rgba(0,0,0,.05)`,更深一级 `rgba(0,0,0,.25)`。填充层同理走 `rgba(0,0,0,.02/.04/.1)`。
- **输入描边 Field Stroke** (`#dbdac7` / `hsl(56 12% 82%)`):输入框边框。

### Tertiary (经营语义色)
克制使用,只在 delta / 告警 / 状态徽章上出现,从不做装饰。告警按严重度三档分级:红 → 橙 → 黄。
- **盈绿 Positive Pine** (`#2c8c5f` / `hsl(152 52% 36%)`):上涨、成功、达成、正常、在线绿点。
- **亏红 Negative Red** (`#db2424` / `hsl(0 72% 50%)`):下跌、缺货、超时(最重一档告警)。
- **危红 Destructive Red** (`#d32222` / `hsl(0 72% 48%)`):删除、严重告警。
- **警橙 Warning Amber** (`#ce7612` / `hsl(32 84% 44%)`):告急、临界、库存预警(中间档)。
- **提示金黄 Caution Gold** (`#af8509` / `hsl(45 90% 36%)`):偏低、演示数据等最轻一档提醒;压暗到 L36% 以便浅底徽章上文字达标。
- **信息蓝 Info Blue** (`#1c6dca` / `hsl(212 76% 45%)`):监控中 / 信息提示(非告警,与红橙黄三档告警区分)。

**The Semantic Badge Rule.** 状态徽章一律「语义色 15% 透明底 + 同色实字」(`bg-negative/15 text-negative`),绝不用 Tailwind 现成调色板(`bg-red-100 text-red-700`)。Tailwind 的红 ≠ 品牌 `hsl(0 72% 50%)`,硬编码即破坏色彩统一。语义色 token 走 `hsl(var(--x) / <alpha-value>)` 格式以支持透明叠加。

### Named Rules
**The One Voice Rule.** 深松墨绿是唯一的强声音,只落在主按钮、wordmark、关键数字上,任意屏幕占比 ≤10%。它的稀有就是它的力量;不要把墨绿铺成大色块。

**The Readable Ink Rule.** 暖白底上的浅灰文字最容易 washed-out。正文对比度必须 ≥4.5:1,大字 ≥3:1,占位文字同样 4.5:1。若接近临界,把文字推向墨绿 ink 端,**绝不为「优雅」牺牲可读**。

**The Quiet Semantics Rule.** 涨跌与告警用色克制,且尽量辅以图标/箭头/形状(▲▼、徽标),不只靠红绿——色盲也要分得清。

## 3. Typography

**Display / Body / Label Font:** `GoogleSansFlex`(自托管可变字重 100–900),回退 `ui-sans-serif, system-ui, -apple-system, PingFang SC, Microsoft YaHei, sans-serif`。

**Character:** 全程一个无衬线家族,层级只靠**字重与字号**区分,不混排第二种字体——干净、现代、不喧哗。数字处处启用 `tabular-nums` 等宽,这是账册感的来源。

### Hierarchy
- **Display** (600, `1.5rem`, line-height 1.25):KPI 大值、招牌指标数字,搭配 `tabnum` 等宽。
- **Headline** (600, `1.15rem`, line-height 1.3):页面/区块主标题。
- **Title** (600, `1rem`, line-height 1, tracking -0.01em):卡片标题(`leading-none tracking-tight`)。
- **Body** (400, `0.875rem`, line-height 1.5):正文、表格、对话气泡;Markdown 正文放宽到 1.65。正文行宽控制在 65–75ch。
- **Label** (500, `0.75rem`, tracking 0.02em):指标卡标签、徽标、次级说明;指标标签可 `uppercase` 但适度,不滥用。

### Named Rules
**The Ledger Numerals Rule.** 一切指标、金额、百分比、环比必须走 `tabular-nums`(`.tabnum`)。数字纵向对齐成账本,是这套系统的招牌;非等宽的金额视为缺陷。

**The Single Family Rule.** 永远只用 `GoogleSansFlex` 一个家族,靠字重分层。禁止为了「设计感」引入第二种字体或装饰性标题字。

## 4. Elevation

混合策略:**色调分层为主,极淡阴影为辅**。纸面、卡片、弹层靠三档明度(橄榄暖白 → 卡片微暖白)自然分层;`<html>` 压一层 `rgba(107,104,31,.05)` 橄榄底让卡片浮起。阴影只用最轻的一档 `shadow-sm`,表达「温润浮起」而非「悬空抬起」。这是「温润质感」的物理来源。

### Shadow Vocabulary
- **轻浮起 soft-sm** (`box-shadow: 0 1px 2px rgba(0,0,0,0.05)`,即 Tailwind `shadow-sm`):卡片、输入框静息态的唯一阴影。
- 交互浮起:hover 时以背景填充变化(`bg-fill` / `bg-accent`)表达,而非加深阴影。

### Named Rules
**The Soft-Lift Rule.** 阴影上限是 `shadow-sm`(blur ≤ 2px)。任何卡片/按钮的阴影 blur ≥ 16px 都过头;深度优先靠色调分层与描边,不靠堆阴影。

**The No Ghost-Card Rule.** 禁止「1px 描边 + 宽柔阴影」同时出现在同一元素上(ghost-card 套路)。二选一:要么单道发丝描边,要么单层 `shadow-sm`,不并用作装饰。

## 4.5 Motion & Interaction（动效与交互手感）

承接 product register「150–250ms、动效传达状态而非装饰、不做编排式入场秀」的基线;以下是本项目在移动端反复打磨后**与用户达成一致**的交互手感约束,新增/改动任何弹层、加载态、手势交互前必读。**移动端是一等公民,这些规则首先为移动端而定。**

### Named Rules

**The Follow-the-Finger Rule（跟手平移）.** 移动端 sheet/抽屉的拖拽必须是**整体跟手平移**(`transform: translateY`),不是「只延伸顶部高度」。上滑下滑都要让整张抽屉跟着手指走;拖拽中不加 transition(跟手),松手吸附/进出场才走过渡。参考实现:`AskAiSheet`(固定高度 + translateY 平移 + vh 偏移)。

**The Flick-to-Dismiss Rule（按速度关闭,不按位移).** 关闭手势要**分速度**:快速下滑(flick,速度阈值 ≈55vh/s)即关;慢拖一点点必须**吸附回最近档**,不能误关。绝不用「越过中点就关」这种纯位移判定——它会让用户稍微一碰就误关。需在拖拽中跟踪速度(`performance.now()` 取最近帧位移/耗时,轻度平滑防抖)。

**The Graceful Enter/Exit Rule（进出场必有过渡).** 弹层/抽屉**不许瞬间出现或消失**。入场:移动端从屏外滑入(translateY)、桌面缩放淡入(scale-95→100 + opacity),遮罩同步淡入。退场:**先播退场动画(≈280ms)再真正卸载**(延迟 `onClose`),所有关闭入口(遮罩/关闭按钮/Esc/下滑)都走同一平滑退场。时长档:移动入场 ~300ms、桌面 ~200ms、退场 ~280ms。

**The Visible-Work Rule（工作中要有明确反馈).** 异步/AI 工作中必须让用户**看得出系统在动**:加载/思考态用**流光文字**(shimmer)+ **转圈图标**(`Loader2` spin)双重信号,而非静止的纯文字。流光对比要够——暗端用三级前景(`foreground-tertiary` 0.5)、亮端用**纯墨绿主色**(`hsl(var(--primary))`),亮带收窄聚焦;匀速扫动。

**The Gentle-Caret Rule（光标柔和不刺眼).** 流式正文光标用**墨绿主色细条 + 柔和呼吸**(透明度 0.15↔0.8 平滑),绝不用「黑块 + `animate-pulse`」那种近黑白硬闪。

**The Don't-Remove-Affordance Rule（改动不砍已有能力).** 视觉/动效改造**不得砍掉已有的交互能力**。典型教训:给「思考中」加流光时,曾误把可点开看步骤的折叠按钮换成纯展示文字,丢了「展开看具体步骤」——必须把新视觉**叠加**在原交互上(流光行本身做成可点击 toggle),而非替换。

**The Resumable-Work Rule（耗时操作状态可保留).** 耗时的异步操作(尤其 AI 流式回答)状态要**可保留、可恢复**:关掉入口再回来,结果还在;关闭**不中断**后台生成(不 `abort`),回答跑完后回来能看到完整结果。做法:把会话状态提到组件外的持久 store(module 级单例 + 订阅),UI 只是它的视图。参考实现:`useAskAiStore` + `AskAiSheet`。

### 减少动态效果

以上动效都须尊重 `prefers-reduced-motion`:流光降为静态前景色、呼吸/转圈/进出场降为瞬时或淡入。全局已在 `index.css` 兜底(把动画/过渡时长压到 0.01ms),自定义动画须各自再给 reduce 分支(见 `.text-shimmer` / `.stream-caret`)。

## 5. Components

组件的统一手感是**温润质感**:大圆角、柔和描边、极淡浮起,过渡顺滑;果断但不冷硬。

### Buttons
- **Shape:** 圆角 `10px`(`rounded-md`);图标按钮 `36×36`。
- **Primary:** 深松墨绿底 `#192420` + 橄榄暖白字,`h-36px px-16px`,hover 降到 `primary/90`。墨绿是签名暗按钮。
- **Secondary:** 柔和暖灰面底 `#f0eedd` + 墨绿字,hover `secondary/80`。
- **Outline:** 透明底 + 输入描边,hover 转 `bg-accent`。
- **Ghost:** 透明,hover 才显 `bg-accent`;用于低强度操作。
- **Focus:** `ring-2 ring-ring` + `ring-offset-1`,焦点环走墨绿调 `hsl(158 14% 25%)`。

### Cards / Containers
两种容器并存,按语境选择,**不要混用同名实现**:
- **通用 `Card`**(`ui/card.tsx`,用于 Chat / Admin / 排程等页):
  - **Corner Style:** `12px`(`rounded-lg`)
  - **Shadow Strategy:** `shadow-sm`(见 Elevation)
  - **Border:** 单道发丝描边 `rgba(0,0,0,.1)`
- **看板 `BoardCard`**(`BoardPage.tsx`,仅看板用,1:1 复刻 fork StoreClaw 观感):
  - **Corner Style:** `16px`(`rounded-2xl`)
  - **Shadow Strategy:** **无阴影**,纯靠色调分层(卡片微暖白浮于橄榄纸面)浮起 —— 最贴「橄榄书房·不靠阴影」精神
  - **Border:** 更浅的发丝描边 `border-shallow`(`rgba(0,0,0,.05)`)
- **共同:** 背景卡片微暖白 `#fdfdfb`;内边距 `20px`(`p-5`);**禁止嵌套卡片。**

### Inputs / Fields
- **Style:** 透明底 + `#dbdac7` 描边,圆角 `10px`,高 `36px`,内边距 `12px`,`shadow-sm`。
- **Focus:** `ring-2 ring-ring`,焦点环墨绿调;描边不跳色,靠环表达。
- **Placeholder:** 走 muted,但仍须满足 4.5:1(见 Readable Ink Rule)。

### Badges / Chips
- **Style:** 全圆角 pill,`px-2.5 py-0.5`,`text-xs`。
- **State:** 默认 = 主色 12% 透明底 + 墨绿字(`bg-primary/12 text-primary`);语义 = 对应色 15% 透明底 + 同色字(`bg-success/15 text-success`、warning、destructive)。透明叠加而非实色块,克制。

### MetricCard (签名组件)
账册感的化身,看板与经营概览复用同一张:标签(label,`text-xs uppercase tracking-wide muted`)+ 等宽大值(`tabnum text-2xl font-semibold`)+ 涨跌(▲绿/▼红 + `环比` 字样)+ 内嵌迷你 Sparkline。loading 走 Skeleton 三段占位。**数据中枢的身份靠这张卡的数字呈现立住。**

### Navigation
- 侧边栏(`Sidebar`)+ 应用外壳(`AppShell`);默认/hover/active 靠 `bg-fill` / `bg-accent` 填充变化区分,不靠重描边或亮色。移动端筛选栏可上划收起。

## 6. Do's and Don'ts

### Do:
- **Do** 一切数字走 `tabular-nums`(`.tabnum`),对齐成账册。
- **Do** 主色深松墨绿稀有使用(≤10%/屏),只给主按钮、wordmark、关键数字。
- **Do** 正文对比度 ≥4.5:1;接近临界时把文字推向墨绿 ink 端。
- **Do** 卡片用单道发丝描边 + 至多 `shadow-sm`,圆角 12px。
- **Do** 涨跌/告警辅以箭头或图标,不只靠红绿(色盲友好)。
- **Do** 移动端逐断点验收:筛选栏收起、图例不压字、触控目标足够、窄屏不溢出。
- **Do** 移动端弹层/抽屉遵循 §4.5:整体跟手平移、按速度关闭、进出场有过渡、工作中有流光+转圈反馈、改动不砍已有交互、耗时操作状态可保留。
- **Do** 用半透明叠加表达前景/填充/描边,让暖意复合到橄榄底上。

### Don't:
- **Don't** 做成**老派后台管理**:蓝灰配色 + 密集表格堆砌。能用图表/指标卡/就近状态表达的,不要默认塞一张表。
- **Don't** 套**花哨 SaaS 落地页**:大渐变、英雄大数字模板、无意义卡片网格、每段顶一个小标签 eyebrow、`01/02/03` 编号小节。
- **Don't** 做成**过度活泼消费 app**:强色彩、卡通/手绘 SVG 插画、弹跳/橡皮筋动效。**例外**:弹性/橡皮筋指的是装饰性 overshoot 缓动(如 `cubic-bezier(.34,1.56,.64,1)`);功能性的「跟手平移 + 松手吸附」不算,但吸附用 ease-out(quart/quint/expo)收尾,不要 overshoot。
- **Don't** 用 `border-left/right` 大于 1px 的彩色侧条做卡片/列表/告警的强调(Markdown 引用块 `blockquote` 的 3px 左条是排版惯例,属唯一例外)。
- **Don't** 用渐变文字(`background-clip:text` + gradient)、默认玻璃拟态(glassmorphism)。**唯一例外**:加载/思考态的**功能性流光**(shimmer,见 §4.5 Visible-Work Rule)——它是状态指示而非装饰,且须带 `prefers-reduced-motion` 降级。除此之外不得用渐变文字。
- **Don't** 把卡片圆角调到 24/28/32px+;卡片上限 12–16px,pill 仅给徽标/按钮。
- **Don't** 同一元素并用「1px 描边 + ≥16px 柔阴影」(ghost-card)。
- **Don't** 引入第二种字体家族;层级只用 `GoogleSansFlex` 的字重分。
- **Don't** 为「优雅」用浅灰正文压在暖白底上,造成 washed-out。
