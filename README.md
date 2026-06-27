# Crossborder Ops Data Hub

跨境电商 ETL 中间件：定时从电商平台拉取 API 数据，清洗后存入 MySQL，经只读接口供 AI Agent（openclaw，飞书渠道）做业务查询与决策分析。当前接入 TikTok Shop（印尼），多租户（每个飞书 app = 一租户），生产以 cloudflared 命名隧道 + systemd user timer 部署。

## 文档导航

| 文档 | 用途 |
|------|------|
| [AGENTS.md](AGENTS.md) | **开发规范**：架构、实现规则、代码风格、Skill 规范（动手前先读） |
| [docs/production-deployment.md](docs/production-deployment.md) | **生产投产手册**：从零搭机到上线收尾的权威流程 |
| [docs/openclaw-setup.md](docs/openclaw-setup.md) | **openclaw 网关配置**：飞书接入、多租户 agent、MCP、人格、新租户 checklist |
| [docs/ops-runbook.md](docs/ops-runbook.md) | **运维手册**：日常巡检、常见操作、排坑库 |
| [docs/business-rules.md](docs/business-rules.md) | **业务规则与数据口径基线**（改口径前先读、改完回写） |
| [docs/proactive-push-ops.md](docs/proactive-push-ops.md) | 日报 + 监控告警的主动推送运维口径 |
| [docs/feishu-bot-onboarding.md](docs/feishu-bot-onboarding.md) | 飞书机器人欢迎文案规范 |
| [docs/board-data-backlog.md](docs/board-data-backlog.md) | 运营看板演示模块的数据管道 backlog |
| [docs/mabang-erp-api.md](docs/mabang-erp-api.md) | 马帮 ERP 接口参考（调研，未接入） |
| `plan/` | 需求/设计计划（已完成的归在 `plan/archive/`） |

> 大型 API dump（`docs/tiktok-shop-openapi-*.json/.md`、`docs/mabang-erp-api-full.json`）体积大、不入库、仅本地保留；tiktok 两份可由 `scripts/generate_tiktok_api_docs.py` 从 `material/` 重新生成。

## 技术栈

| 组件 | 技术选型 | 说明 |
|------|----------|------|
| 语言 | Python 3.10+ | - |
| 包管理 | uv | `uv sync` 装依赖 |
| ORM | SQLAlchemy 2.0 | 配合 pymysql |
| 数据校验 | Pydantic V2 | API 响应清洗 |
| HTTP 客户端 | Requests | 统一请求封装 |
| 任务调度 | systemd user timer | 零额外常驻；失败 OnFailure→飞书告警 |
| Web 框架 | FastAPI | OAuth 回调 / 数据 API / 看板 / 报告 / 对话 |
| 配置管理 | pydantic-settings | 读 `.env`，支持嵌套配置（`__` 分隔） |
| 公网入口 | cloudflared 命名隧道 | 出站连 Cloudflare，源站不开入站端口 |

## 目录结构

```
core/          # 配置、DB、API 客户端基类、领域模型(domain)、多租户(tenancy)、重试
platforms/     # 平台 SDK（tiktok_shop: client/schemas/normalize）
models/        # SQLAlchemy ORM 模型
services/      # 业务服务、持久化编排、确定性指标查询
analytics/     # 确定性分析公式与告警规则（利润/ROI/库存覆盖等）
flows/         # 定时任务流（普通函数，systemd timer 直调）
web/           # FastAPI：OAuth 回调 / 数据 API / 看板 / 报告 / 对话控制台
frontend/      # 运营看板 + 对话控制台 SPA（Vite，构建产物挂 /app）
ai_tools/      # 面向 AI/openclaw 的只读查询辅助
scripts/       # 运维/迁移/预检脚本（setup-server、preflight、*_admin 等）
deploy/        # systemd 单元 + cloudflared 隧道配置 + deploy.sh 一键部署
openclaw-skills|openclaw-docs|openclaw-plugins/  # openclaw agent 配置（部署时同步到 ~/.openclaw）
docs/          # 手册与接口参考（见上「文档导航」）
plan/          # 需求/设计计划（archive/ 为已完成）
auth.py main.py pyproject.toml uv.lock .env.example
```

## 快速开始

### 1. 安装依赖

```bash
cd crossborder-ops-data-hub
uv sync                          # 创建虚拟环境并装依赖
```

### 2. 配置环境变量

```bash
cp .env.example .env             # 按注释逐项填写
```

字段与 `core/config.py` 一一对应；生产完整清单与密钥生成见 [docs/production-deployment.md](docs/production-deployment.md) §5。

### 3. TikTok Shop 授权（首次）

首次需经 OAuth 拿 Token，之后自动持久化到 DB 并定时刷新。两种方式：

- **Web 回调（推荐）**：启动 Web 服务（`uvicorn web.app:app`，监听 `127.0.0.1:8000`），公网入口由 cloudflared 隧道放行 `/auth/callback/tiktok`（见生产手册 §5.4/§6.3）。在 TTS 后台发起平台授权，商家同意后自动回调换 Token 入库。
- **CLI 手动**：拿到一次性 `auth_code` 后 `python auth.py <auth_code>`（一次性，用完即弃）。

### 4. 运行同步任务

```bash
uv run python main.py                       # 本地调试，直接执行
./deploy/deploy.sh --pull                    # 生产：拉代码 + uv sync + 建表 + 装/启用 systemd timer
```

## 部署与运维

生产用 **systemd user timer**（Prefect 已剥离，commit `afe3e60`）+ **cloudflared 命名隧道**，全部 `systemctl --user`、靠 linger 常驻。

- **从零投产**：[docs/production-deployment.md](docs/production-deployment.md)（服务器规格→.env→部署→合规→收尾），一键基础环境 `bash scripts/setup-server.sh`，部署前预检 `uv run python -m scripts.preflight`。
- **日常巡检 / 排坑**：[docs/ops-runbook.md](docs/ops-runbook.md)（含 13 个 timer 时间表、负载基线、排坑库）。
- **日报 + 告警推送**：[docs/proactive-push-ops.md](docs/proactive-push-ops.md)。
- **openclaw 网关**：[docs/openclaw-setup.md](docs/openclaw-setup.md)。

## 核心架构

### BaseAPIClient 基类

统一的 API 客户端基类，内置 HMAC-SHA256 签名、Token 生命周期管理（过期检查/自动刷新）、401 自动重试、指数退避。

```python
class BaseAPIClient(ABC):
    def _generate_sign(self, path: str, params: dict) -> str ...
    def _ensure_token(self) ...
    def request(self, method, path, params, data, max_retries) -> dict ...
```

扩展新平台：继承 `BaseAPIClient`，实现 `authenticate` / `refresh_access_token` 两个抽象方法即可（基类自动处理签名、Token、重试）。平台怪癖收敛在 `platforms/<platform>/`（client/schemas/normalize），向 `services` 只暴露平台中立的 `core/domain` DTO（见 AGENTS.md「代码风格」）。

### 定时任务流

flow = 普通 Python 函数（systemd timer 直调），取数/写库用零依赖 `core.retry.retry` 装饰器加失败重试：

```python
from core.retry import retry

@retry(retries=3, delay_seconds=60)   # 取数失败重试 3 次
def fetch_inventory(): ...

def sync_inventory_flow():
    raw_data = fetch_inventory()       # E: Extract（先记 raw 审计）
    valid_data = validate_inventory()  # T: Transform（to_domain_*）
    count = save_to_db(valid_data)     # L: Load（幂等 upsert）
```

### Web API 服务

FastAPI 提供 HTTP 接口，两类路由：

- **OAuth 回调** (`/auth/callback/tiktok`)：处理 TTS 授权回调，自动换 Token。
- **数据查询** (`/api/data/*`)：供 AI Agent / 内部系统读业务数据，需 `X-Internal-Token` 头鉴权，仅本机可调（公网隧道不放行）。

数据查询端点（库存/低库存/商品/订单汇总·趋势·Top SKU/经营概览/待发货/利润/告警等）以 `web/routes/data.py` 为准（同源经 `/mcp` 暴露为 openclaw 的 `ops_*` MCP 工具，见 docs/openclaw-setup.md §2.4）。核心公式始终在 `analytics`/`services` 以确定性 Python/SQL 实现，HTTP 与 skill 都不是公式的真相来源。

## 扩展指南

**新增平台接口**：在 `platforms/tiktok_shop/client.py` 加方法即可，基类自动处理签名/Token/重试。

**新增同步任务**（三步，全走 systemd timer）：

1. `flows/` 下新建 flow 文件（普通函数 + `@retry` + `__main__` 入口）。
2. `deploy/systemd/` 加一对 `.service`/`.timer`（照现有单元抄；`.service` 加 `OnFailure=data-onfailure@%n.service` 接飞书失败告警）。
3. `./deploy/deploy.sh --pull` 部署（自动安装并启用新单元）。

## License

Internal Use Only
