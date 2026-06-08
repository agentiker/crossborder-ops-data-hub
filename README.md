# Crossborder Ops Data Hub

跨境电商 ETL 中间件，定时从电商平台拉取 API 数据，清洗后存入 MySQL，供 AI Agent 进行业务查询与决策分析。

## 技术栈

| 组件 | 技术选型 | 说明 |
|------|----------|------|
| 语言 | Python 3.10+ | - |
| 包管理 | uv | 极速依赖管理 |
| ORM | SQLAlchemy 2.0 | 配合 pymysql |
| 数据校验 | Pydantic V2 | API 响应清洗 |
| HTTP 客户端 | Requests | 统一请求封装 |
| 任务编排 | Prefect 3.x | 自带 Web UI 监控 |
| Web 框架 | FastAPI | OAuth 回调 & 数据查询 API |
| 配置管理 | python-dotenv | .env 文件加载 |

## 目录结构

```
crossborder-ops-data-hub/
├── core/                       # 核心基础组件
│   ├── config.py               # 环境变量与配置读取
│   ├── db.py                   # SQLAlchemy Engine 与 Session
│   └── base_client.py          # API 客户端基类（签名/Token/重试）
├── platforms/                  # 平台 SDK
│   └── tiktok_shop/            # TikTok Shop
│       ├── client.py           # 继承 BaseAPIClient
│       └── schemas.py          # Pydantic 数据模型
├── models/                     # SQLAlchemy ORM 模型
│   └── base_models.py          # 库存、订单、利润、Token 等表
├── services/                   # 业务逻辑服务层
│   ├── token_store.py          # Token 持久化与加载
│   ├── inventory_store.py      # 库存数据写入
│   ├── order_store.py          # 订单数据写入
│   ├── order_metrics.py        # 订单指标计算（GMV/Top SKU）
│   ├── raw_sync_store.py       # 原始 API 响应归档
│   ├── sync_state.py           # 同步游标管理
│   ├── metrics_store.py        # 指标数据存储
│   └── scoping.py              # 平台/国家/店铺维度管理
├── flows/                      # Prefect 任务流（业务逻辑）
│   ├── sync_inventory.py       # 库存同步 Flow
│   ├── sync_orders.py          # 订单增量同步 Flow
│   └── refresh_tokens.py       # Token 自动刷新 Flow
├── web/                        # FastAPI Web 服务
│   ├── app.py                  # FastAPI 应用入口
│   ├── security.py             # 内部 API 鉴权（X-Internal-Token）
│   └── routes/
│       ├── auth.py             # OAuth 回调端点
│       └── data.py             # 数据查询 API（库存/利润/告警/订单）
├── ai_tools/                   # AI Agent 工具接口
│   └── operations_read.py      # 只读运营数据查询
├── analytics/                  # 分析模块
│   └── profit_alerts.py        # 利润告警逻辑
├── scripts/                    # 工具脚本
│   ├── inspect_api.py          # API 调试工具
│   └── get_shop_cipher.py      # 获取 Shop Cipher
├── tests/                      # 测试用例
├── main.py                     # 启动入口（本地调试用）
├── auth.py                     # CLI 手动授权脚本
├── prefect.yaml                # Prefect 部署配置（定时调度）
├── pyproject.toml              # 项目配置与依赖声明
├── requirements.txt            # 兼容 pip 的依赖列表
├── .env.example
└── README.md
```

## 快速开始

### 1. 安装依赖（使用 uv）

```bash
cd crossborder-ops-data-hub

# 方式一：使用 uv（推荐，速度极快）
uv venv                          # 创建虚拟环境
uv pip install -e .              # 安装项目依赖

# 方式二：使用 pip（兼容）
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入真实配置：

```bash
# 数据库配置
DB__HOST=localhost
DB__PORT=3306
DB__USER=root
DB__PASSWORD=your_password
DB__DATABASE=crossborder_ops_data_hub

# TikTok Shop 配置
TIKTOK__APP_KEY=your_app_key
TIKTOK__APP_SECRET=your_app_secret
TIKTOK__BASE_URL=https://open-api.tiktokglobalshop.com
```

### 3. TikTok Shop 授权（首次使用）

首次运行前，需要通过 OAuth 授权获取 Token，之后 Token 会自动持久化到数据库。

有两种授权方式：

#### 方式一：Web 回调（推荐，自动化）

1. 启动 Web 服务：

```bash
python main.py --task web
```

服务默认监听 `0.0.0.0:8000`，可通过 `--host`、`--port` 参数自定义。

2. 在 TikTok Shop 开发者中心，将应用的回调地址设置为：

```
http://你的公网域名或IP:8000/auth/callback/tiktok/html
```

> **本地调试**需要使用 ngrok 等工具将 localhost 暴露到公网，或直接部署到服务器。

3. 在平台发起授权，商家同意后 TikTok 会自动回调此地址，系统自动完成 Token 交换并存入数据库。

#### 方式二：CLI 手动授权

从 TikTok Shop 卖家中心的开放平台页面完成 OAuth 授权，获取 `auth_code`，然后运行：

```bash
python auth.py <auth_code>
```

> **注意**：`auth_code` 是一次性授权码，只能使用一次。授权完成后 Token 会自动持久化到 MySQL，后续定时任务会自动加载并刷新 Token，无需重复授权。

### 4. 启动 Prefect Server（可选，提供 Web UI）

```bash
prefect server start
```

访问 `http://127.0.0.1:4200` 查看任务管理界面。

### 5. 运行同步任务

**本地调试（直接执行）：**

```bash
uv run python main.py
```

**生产部署（定时调度）：**

```bash
prefect deploy --all                       # 部署所有任务
prefect deploy --name tiktok-inventory-sync  # 部署单个任务
```

## 服务器定时任务运维（测试服务器 yamk）

> 测试服务器 `yamk`（局域网 192.168.1.129，本机用 `ssh hp` 免密登录）上的数据拉取
> **不走 Prefect server，也不走 cron**，用 **systemd user timer** 跑。下面是日常运维口径。

### 部署了什么

| timer | 跑的 flow | 周期 |
|-------|-----------|------|
| `data-sync-inventory.timer` | `flows.sync_inventory` | 每小时 `*:17` |
| `data-sync-orders.timer`    | `flows.sync_orders`    | 每小时 `*:23` |
| `data-refresh-tokens.timer` | `flows.refresh_tokens` | 每 6h `00,06,12,18:41` |

- unit 文件：`~/.config/systemd/user/`（**用户级，不用 sudo**；linger 已开，登出仍跑）
- 每个 service：`Type=oneshot` + `WorkingDirectory=~/code/crossborder-ops-data-hub`
  + `ExecStart=/home/guopeixin/.local/bin/uv run python -m flows.<X>`，`Persistent=true`（补跑错过的）

### 常用命令（在服务器上，或 `ssh hp '...'`）

```bash
# 看下次/上次触发时间
systemctl --user list-timers 'data-*'

# 看某个任务最近日志（实时加 -f）
journalctl --user -u data-sync-inventory -n 50 --no-pager

# 立刻手动跑一次（不影响 timer 排程）
systemctl --user start data-sync-orders.service

# 看上次跑的结果（success/失败码）
systemctl --user show -p Result -p ExecMainStatus data-sync-orders.service

# 暂停 / 恢复某个定时
systemctl --user disable --now data-sync-orders.timer
systemctl --user enable  --now data-sync-orders.timer
```

### 改周期

编辑 `~/.config/systemd/user/<name>.timer` 的 `OnCalendar=`，然后：

```bash
systemctl --user daemon-reload
systemctl --user restart <name>.timer
```

### ⚠️ 前置依赖：TikTok 出口 IP 必须在白名单内

服务器装了 **ShellCrash 透明代理**（iptables TPROXY，网络层劫持全部流量）。TikTok 调用
要能成功，必须让 TikTok 域名**绕过代理走直连**——这样出口才是局域网真实 NAT IP
`112.94.74.178`（已在 TikTok 后台白名单内）。已在 ShellCrash 加了两条直连规则：

```yaml
# /etc/ShellCrash/yamls/config.yaml 的 rules 段（需 sudo 改，改后 sudo systemctl restart shellcrash）
- DOMAIN-SUFFIX,tiktok-shops.com,DIRECT
- DOMAIN-SUFFIX,tiktokglobalshop.com,DIRECT
```

- 验证直连生效：`grep tiktok /tmp/ShellCrash/config.yaml`（运行配置里要有这两行）
- 验证能调通：`uv run python -c "from flows._shop_discovery import discover_single_shop; from platforms.tiktok_shop.client import TikTokShopClient; print(len(TikTokShopClient(**discover_single_shop()).list_products(page_size=5)))"`
- `112.94.74.178` 是住宅联通 IP，可能 re-dial 变动；变了要同步更新 TikTok 后台白名单。

> **注意日志里的「出口 IP」行**：flow 开头会打印出口 IP（查 `ddns.oray.com`），正常应是
> `112.94.74.178`。若看到 `104.28.x` 或 `2a09:bac5:...`，说明这次查询被代理兜走了，
> 不一定代表 TikTok 调用失败——以实际 flow 是否 `Completed` 为准。

## 核心架构

### BaseAPIClient 基类

统一的 API 客户端基类，内置：

- **HMAC-SHA256 签名**：自动生成请求签名
- **Token 生命周期管理**：过期检查、自动刷新
- **401 自动重试**：Token 失效时自动换取新 Token
- **指数退避**：请求失败时的重试策略

```python
class BaseAPIClient(ABC):
    def _generate_sign(self, path: str, params: dict) -> str ...
    def _ensure_token(self) ...
    def request(self, method, path, params, data, max_retries) -> dict ...
```

### 扩展新平台

继承 `BaseAPIClient`，实现两个抽象方法即可：

```python
from core.base_client import BaseAPIClient

class ShopeeClient(BaseAPIClient):
    def authenticate(self, **kwargs) -> dict:
        # Shopee 特有的鉴权逻辑
        pass

    def refresh_access_token(self) -> dict:
        # Shopee Token 刷新逻辑
        pass
```

### Prefect 任务流

使用 `@task` 和 `@flow` 装饰器定义 ETL 流程：

```python
@flow(name="tiktok-inventory-sync", log_prints=True)
def sync_inventory_flow():
    raw_data = fetch_inventory()      # E: Extract
    valid_data = validate_inventory()  # T: Transform
    count = save_to_db(valid_data)     # L: Load
```

### Web API 服务

基于 FastAPI 提供 HTTP 接口，分为两类路由：

- **OAuth 回调** (`/auth/callback/tiktok`)：处理 TikTok Shop 授权回调，自动完成 Token 交换
- **数据查询** (`/api/data/*`)：供 AI Agent 和内部系统查询业务数据

```bash
# 启动 Web 服务
uvicorn web.app:app --host 0.0.0.0 --port 8000
```

数据查询 API 需通过 `X-Internal-Token` 头鉴权，确保只有授权的内部服务可访问。
完整参数、`curl` 与响应示例见 [docs/mvp-api.md](docs/mvp-api.md)：

| 端点 | 说明 |
|------|------|
| `GET /api/data/inventory` | 库存列表 + 低库存（支持平台/国家/店铺过滤） |
| `GET /api/data/products` | 商品目录（状态过滤，用于上下架/滞销分析） |
| `GET /api/data/orders/summary` | 订单汇总（GMV/订单量/销量/客单价） |
| `GET /api/data/orders/trend` | 销售趋势（按天 GMV/单量/销量，无单日补 0） |
| `GET /api/data/orders/top-skus` | 热销 SKU 排行 |
| `GET /api/data/overview` | 经营概览（库存 + 近 7 天订单） |
| `GET /api/data/profit/summary` | 利润汇总（规划中，本期返回 503） |
| `GET /api/data/alerts` | 未处理告警（规划中，本期返回 503） |

## Prefect Web UI 功能

启动 `prefect server start` 后访问 `http://127.0.0.1:4200`：

- 任务运行历史查看
- 实时日志流
- 任务状态监控（成功/失败/运行中）
- 手动触发任务
- 定时调度配置

## 扩展指南

### 新增平台接口

在 `platforms/tiktok_shop/client.py` 中新增方法即可，基类自动处理签名、Token、重试：

```python
def get_orders(self, start_time: int, end_time: int) -> dict:
    """获取订单列表"""
    return self.get("/api/orders/search", params={
        "start_time": start_time,
        "end_time": end_time,
    })
```

### 新增同步任务

**第一步**：在 `flows/` 下新建 Flow 文件

```python
# flows/sync_orders.py
from prefect import flow, task

@task(name="fetch-orders")
def fetch_orders() -> list:
    ...

@flow(name="tiktok-orders-sync", log_prints=True)
def sync_orders_flow():
    ...
```

**第二步**：在 `prefect.yaml` 中注册调度

```yaml
deployments:
  # 已有任务
  - name: tiktok-inventory-sync
    entrypoint: flows/sync_inventory.py:sync_inventory_flow
    schedule:
      interval: 3600

  # 新增任务
  - name: tiktok-orders-sync
    entrypoint: flows/sync_orders.py:sync_orders_flow
    schedule:
      interval: 1800
```

**第三步**：部署

```bash
prefect deploy --all
```

## 后续扩展

1. **接入更多平台**：在 `platforms/` 下新建目录，继承 `BaseAPIClient`
2. **新增数据表**：在 `models/` 下定义 ORM 模型
3. **新增同步任务**：在 `flows/` 下定义新的 Prefect Flow
4. **分布式部署**：Prefect 支持多 Worker 分布式执行

## License

Internal Use Only
