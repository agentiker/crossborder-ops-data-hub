# crossborder-onboarding（OpenClaw 命令插件）

把跨境电商运营 bot 的「指引/onboarding」做成**确定性命令**：注册 `/start`，handler 直出固定文案，
**完全绕开 LLM**。解决弱模型（mimo）逐字复制 onboarding 不可靠（自由生成/漏字/混入禁用 bullet/乱码）的问题。

## 行为

- 飞书账号 `ecom-app` / `ecom-app-gtl` 收到 `/start` → 直接回固定 onboarding 文案（`index.js` 的 `ONBOARDING_ZH`）。
- 其它账号（`main-app` 等）→ `continueAgent: true` 回落给 agent，不污染。
- 命令必须 `/` 前缀且 ASCII，裸词「指引」无法做成命令 → 仍走 SKILL.md 的 LLM 兜底。飞书菜单「指引」按钮应改为发送 `/start`。

## 文案权威来源

`index.js` 的 `ONBOARDING_ZH` 是权威文案。`openclaw-skills/crossborder-ops-data/SKILL.md` 的
`===ONBOARDING_BEGIN/END===` 块是 LLM 兜底副本，**必须与此逐字一致**——改一处同步另一处。

## 部署（服务器，user 级 openclaw，无需 sudo）

```bash
cd ~/code/crossborder-ops-data-hub && git pull
openclaw plugins install --link ~/code/crossborder-ops-data-hub/openclaw-plugins/crossborder-onboarding
openclaw plugins enable crossborder-onboarding
systemctl --user restart openclaw-gateway
```

核对：`~/.openclaw/openclaw.json` 的 `plugins.entries.crossborder-onboarding.enabled=true`，
`plugins.allow` 含 `crossborder-onboarding`。`--link` 指向仓库目录，`git pull` 即可更新文案。

## 验证

飞书 ecom/ecom-gtl 发 `/start` → 逐字一致、无乱码、无多余 bullet；连发多次完全相同。
main/crayfish 发 `/start` → 不返回电商文案。gateway 日志应见 `system command detected, plain-text dispatch`，无 LLM/工具调用。
