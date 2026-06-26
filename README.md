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
| 任务调度 | systemd user timer | 零额外常驻；失败 OnFailure→飞书告警 |
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
├── flows/                      # 定时任务流（业务逻辑，systemd timer 直调）
│   ├── sync_inventory.py       # 库存同步
│   ├── sync_orders.py          # 订单增量同步
│   └── refresh_tokens.py       # Token 自动刷新
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
├── deploy/systemd/             # 定时任务 systemd unit（.service/.timer）
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

### 4. 运行同步任务

**本地调试（直接执行）：**

```bash
uv run python main.py
```

**生产部署（定时调度）：** 用 **systemd user timer**（Prefect 已剥离，见 commit `afe3e60`）。

```bash
./deploy/deploy.sh --pull     # 拉代码 + uv sync + 建表 + 装/启用所有 systemd timer
```

> 详见「服务器定时任务运维」节与 [docs/proactive-push-ops.md](docs/proactive-push-ops.md)。

## 服务器定时任务运维（测试服务器 yamk）

> 测试服务器 `yamk`（局域网 192.168.1.129，本机用 `ssh hp` 免密登录）上的数据拉取
> **不走 cron**，用 **systemd user timer** 跑。下面是日常运维口径。

### 部署了什么

| timer | 跑的 flow | 周期 |
|-------|-----------|------|
| `data-sync-inventory.timer` | `flows.sync_inventory` | 每小时 `*:17` |
| `data-sync-orders.timer`    | `flows.sync_orders`    | 每小时 `*:23` |
| `data-refresh-tokens.timer` | `flows.refresh_tokens` | 每 6h `00,06,12,18:41` |
| `data-scan-alerts.timer`    | `flows.scan_fulfillment_alerts` | 每 30 分 `*:10,40`（待发货超时告警） |

- unit 文件：`~/.config/systemd/user/`（**用户级，不用 sudo**；linger 已开，登出仍跑）
- 每个 service：`Type=oneshot` + `WorkingDirectory=~/code/crossborder-ops-data-hub`
  + `ExecStart=/home/guopeixin/.local/bin/uv run python -m flows.<X>`，`Persistent=true`（补跑错过的）

> 📋 **主动推送（日报 + 待发货超时告警）的完整运维口径**——openclaw cron 改/加日报、告警 timer 首次部署、配置在哪改、文案同步、已知坑——见 **[docs/proactive-push-ops.md](docs/proactive-push-ops.md)**。

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
（当前出口 IP 见 TikTok 后台白名单，动态 IP 会变）。已在 ShellCrash 加了两条直连规则：

```yaml
# /etc/ShellCrash/yamls/config.yaml 的 rules 段（需 sudo 改，改后 sudo systemctl restart shellcrash）
- DOMAIN-SUFFIX,tiktok-shops.com,DIRECT
- DOMAIN-SUFFIX,tiktokglobalshop.com,DIRECT
```

- 验证直连生效：`grep tiktok /tmp/ShellCrash/config.yaml`（运行配置里要有这两行）
- 验证能调通：`uv run python -c "from flows._shop_discovery import discover_single_shop; from platforms.tiktok_shop.client import TikTokShopClient; print(len(TikTokShopClient(**discover_single_shop()).list_products(page_size=5)))"`
- 出口 IP 是住宅联通动态 IP，可能 re-dial 变动；变了要同步更新 TikTok 后台白名单。
- 查出口 IP：运行 `check-outbound-ip` skill，或手动 `curl -s --noproxy '*' https://ddns.oray.com/checkip`（与 `flows/network.py:log_egress_ip` 同源）。

> **注意**：本机开了 ShellCrash 代理，直接 `curl ipinfo.io` 等查到的可能是代理出口 IP。
> 须用**国内直连 echo 服务**（如 oray checkip）才能拿到真实 NAT 出口。
> ⚠️ 别用 `curl www.baidu.com -w "%{remote_ip}"`——`%{remote_ip}` 是对端百度服务器 IP，不是你的出口。

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

### 定时任务流

flow = 普通 Python 函数（systemd timer 直调），ETL 的取数/写库步骤用零依赖
`core.retry.retry` 装饰器加失败重试（语义同原 Prefect `@task(retries=)`）：

```python
from core.retry import retry

@retry(retries=3, delay_seconds=60)   # 取数失败重试 3 次
def fetch_inventory(): ...

@retry(retries=2, delay_seconds=30)   # 写库失败重试 2 次
def save_to_db(data): ...

def sync_inventory_flow():
    raw_data = fetch_inventory()       # E: Extract
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
| `GET /api/data/inventory` | 库存列表 + 低库存（静态阈值，支持平台/国家/店铺过滤） |
| `GET /api/data/inventory/low-stock` | 断货/低库存风险（按可售天数=库存÷日均销速；只列仍有销量的 SKU） |
| `GET /api/data/products` | 商品目录（状态过滤，用于上下架/滞销分析） |
| `GET /api/data/orders/summary` | 订单汇总（GMV/订单量/销量/客单价） |
| `GET /api/data/orders/trend` | 销售趋势（按天 GMV/单量/销量，无单日补 0） |
| `GET /api/data/orders/top-skus` | 热销 SKU 排行 |
| `GET /api/data/overview` | 经营概览（库存 + 近 7 天订单） |
| `GET /api/data/profit/summary` | 利润汇总（规划中，本期返回 503） |
| `GET /api/data/alerts` | 未处理告警（规划中，本期返回 503） |

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

新增定时任务三步（全走 systemd timer）：

**第一步**：在 `flows/` 下新建 flow 文件（普通函数 + `@retry` + `__main__` 入口）

```python
# flows/sync_orders.py
from core.retry import retry

@retry(retries=3, delay_seconds=60)
def fetch_orders() -> list:
    ...

def sync_orders_flow():
    ...

if __name__ == "__main__":
    sync_orders_flow()
```

**第二步**：在 `deploy/systemd/` 加一对 `.service`/`.timer`（照现有单元抄；
`.service` 记得加 `OnFailure=data-onfailure@%n.service` 以接飞书失败告警）。

**第三步**：部署（自动安装并启用新单元）

```bash
./deploy/deploy.sh --pull
```

## 后续扩展

1. **接入更多平台**：在 `platforms/` 下新建目录，继承 `BaseAPIClient`
2. **新增数据表**：在 `models/` 下定义 ORM 模型
3. **新增同步任务**：在 `flows/` 下定义 flow + `deploy/systemd/` 加 timer（见上「新增同步任务」）
4. **要 Web UI / 多 worker 编排**：达到多租户规模再评估迁 Prefect Cloud（见架构记忆）

## License

Internal Use Only
