# Skill 开发与编写规范

本目录维护 openclaw 使用的业务 Skill。Skill 的职责是根据用户问题调用本项目暴露的只读 HTTP 数据接口，并把结果解释成运营语言。

业务数据的最终来源是部署服务器上的 MySQL；但 Skill 不允许直连 MySQL。所有数据读取必须经由本项目的 FastAPI 只读接口 `/api/data/*` 完成，由服务端的 `ai_tools`、`analytics`、`services` 提供确定性结果。

## 1. Skill 目录结构

每个 Skill 必须作为独立目录存放，目录名为技能唯一标识，仅使用小写字母、数字和连字符。

```text
[skill-slug]/
├── SKILL.md                # 必须：元数据、触发时机、执行 SOP、安全约束
└── references/             # 可选：HTTP 契约、响应示例、字段说明
```

如无强需求，不要在 Skill 中放业务脚本。若确实需要 `scripts/`，脚本只能做请求封装、格式化或参数校验，不得连接数据库、调用平台 API、写入数据或实现利润、ROI、退款率、库存覆盖等核心公式。

## 2. SKILL.md 编写语法

`SKILL.md` 必须由 YAML Frontmatter 和 Markdown 正文组成。

### Part 1: YAML Frontmatter

Frontmatter 必须置于文件最顶部，并用 `---` 包裹。

必填字段：

- `name`: Skill 唯一 ID，必须与目录名一致，仅允许小写字母、数字和连字符。
- `description`: 一句话说明用途与触发场景，建议不超过 160 字符。
- `version`: 语义化版本号，例如 `1.0.0`。

可选字段：

- `user-invocable`: 是否允许用户直接调用该 Skill，布尔值。
- `metadata.openclaw.requires.env`: 运行所需环境变量。
- `metadata.openclaw.requires.tools`: 运行所需 openclaw 工具，例如 `http_get`。
- `metadata.openclaw.requires.bins`: 只有在 Skill 确实调用本地命令时才声明。

推荐模板：

```yaml
---
name: crossborder-ops-data
description: "查询跨境电商经营概览、库存、利润和告警等只读运营数据；通过本机 Data Hub HTTP API 获取结果。"
version: 1.0.0
user-invocable: true
metadata:
  openclaw:
    requires:
      env:
        - DATA_HUB_URL
        - DATA_HUB_TOKEN
      tools:
        - http_get
---
```

## 3. SKILL.md 正文结构

正文可以使用中文，但必须清晰定义 Agent 执行该 Skill 的 SOP，并包含以下部分：

1. **触发时机**：说明哪些用户问题应触发该 Skill，哪些请求不应触发。
2. **前置检查**：检查 `DATA_HUB_URL`、`DATA_HUB_TOKEN` 是否存在；默认 `DATA_HUB_URL` 应指向本机服务，例如 `http://127.0.0.1:8000`。
3. **请求规则**：只允许 `GET /api/data/*`；必须携带 `X-Internal-Token: {{DATA_HUB_TOKEN}}`；禁止把 token 写入回答。
4. **意图路由**：按用户问题选择对应 HTTP endpoint；必要时可调用多个 endpoint 组合回答。
5. **结果解释**：以接口返回值为准。不得自行发明、重算或改写核心业务公式。
6. **分析输出**：定义事实摘要、异常解释、建议动作和置信边界。Skill 不应只复述 JSON，而应把接口返回值组织成可读的运营分析。
7. **异常处理**：说明 401、503、连接失败、空数据、字段缺失等场景如何向用户解释。
8. **输出与安全约束**：说明表格、摘要、风险提示、脱敏要求等输出规则。

## 4. HTTP 契约规范

Skill 文档必须与 `web/routes/data.py` 暴露的接口保持同步。涉及接口路径、请求参数、响应字段或核心指标含义的变更时，应同步更新：

- 对应 Skill 的 `SKILL.md`
- 对应 Skill 的 `references/` 契约文档
- 本项目服务端测试，尤其是数据契约、财务公式和只读行为相关测试

当前约定：

- 只读数据 API 前缀：`/api/data`
- 认证请求头：`X-Internal-Token`
- Skill 侧环境变量：
  - `DATA_HUB_URL`: Data Hub 本机 HTTP 地址
  - `DATA_HUB_TOKEN`: 对应服务端 `API__INTERNAL_TOKEN`

## 5. 安全与边界

- Skill 不得直连 MySQL，不得读取 `.env`，不得调用平台官方 API。
- Skill 不得调用任何写接口，不得修改订单、库存、价格、商品、广告或告警状态。
- Skill 不得输出或记录 `DATA_HUB_TOKEN`、平台 token、数据库凭据。
- 严禁在回答中暴露任何买家的手机号、姓名、完整收货地址等个人可识别信息（PII）；如果接口未来返回相关字段，必须先脱敏。
- 利润、ROI、退款率、库存覆盖等核心公式必须由服务端确定性 Python/SQL 产出。Skill 只能解释服务端返回的指标；接口未返回的指标必须说明当前数据接口暂不支持。
- 当用户要求执行写操作、越权查询或查看敏感原始数据时，Skill 应明确拒绝，并可建议使用已暴露的只读指标替代。

## 6. 辅助脚本规范

默认不需要辅助脚本。确需使用时必须遵守：

1. 脚本只能做 HTTP 请求封装、参数校验、结果格式化或本地 schema 校验。
2. 必须具备清晰异常处理，错误输出应面向 Agent 可读，例如 `Error: Data Hub API Unauthorized`。
3. 供 Agent 读取的数据必须通过 `stdout` 输出；日志和诊断信息使用 `stderr`。
4. 脚本不得连接数据库、调用平台 API、实现核心公式或写入任何业务数据。
