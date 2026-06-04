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
│   └── base_models.py          # 库存表等
├── flows/                      # Prefect 任务流（业务逻辑）
│   └── sync_inventory.py       # 库存同步 Flow
├── main.py                     # 启动入口（本地调试用）
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
