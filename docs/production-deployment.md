# 生产投产手册（Production Deployment）

把 Crossborder Ops Data Hub 部署到**全新的生产服务器**。生产环境与 hp 测试环境**完全独立**：独立机器、独立 DB、独立密钥、独立飞书/TikTok 凭据。

> 本文是当前权威生产指南，**取代** `docs/test-server-deployment.md` 中过时部分（旧文用 `uv pip install -e .`、系统级 systemd、cron、nginx；现状是 `uv sync` + `systemctl --user` 定时器 + cloudflared 命名隧道 + 全套上线合规）。旧文可作 TikTok OAuth/nginx 一节的参考。
>
> 合规实现细节见记忆 `audit-compliance-token-encryption`；部署机制固化在 `deploy/deploy.sh` 与 `deploy/systemd/*`。

---

## 0. 上线时序（先理清依赖，别跳步）

接真实生产店**被 TikTok 审核卡在下游**，不是第一步。正确顺序：

```
①搭生产基础设施(本手册 1–7) → ②配置+授权沙箱/测试店自测 → ③提交发布、TikTok 审核
   → ④审核通过拿到生产权限 → ⑤授权真实生产店 → ⑥正式运营
```

我们做的 token 加密 / 不可篡改审计链 / 加密备份，**正是为了过审核**（数据安全合规项）。所以 1–7 节要在提交审核前完成并自测通过。

---

## 1. 服务器规格与 MySQL 部署方式

### 1.1 2C4G 够用吗 —— 够，但要调优 + 加 swap

MVP 阶段（单租户、单/少店、数据量小：几十到几千订单、定时任务每 30 分钟~6 小时一次的短命进程、对外流量很低）**2C4G 可用**，前提：

- **必须加 swap（建议 4 GB）**。瞬时内存峰值来自定时任务：每个 `flows.*` 用 `uv run python` 起一个新进程。**Prefect 已剥离**（commit `afe3e60`，原先它导入重 + 每跑起临时 server，单跑 ~200–300 MB），现 timer 单跑约 ~50–100 MB；多个 timer 撞点仍会叠加，swap 兜底瞬时峰值。
- **MySQL 内存要压**（见 1.3），别让 buffer pool 吃满。
- **错开 timer 触发分钟**（现有单元已错开，别都改成整点）。

稳态内存（**hp 实测 RSS**，2026-06 隔离本栈各进程）：

| 组件 | 实测 RSS |
|---|---|
| **openclaw 网关**（node，客户对话/飞书推送/登录） | **613 MB** ← 单进程最大头 |
| MySQL（未调优；调优后见 1.3 约同量） | 511 MB |
| data-hub web（FastAPI 常驻） | 144 MB |
| cloudflared | 31 MB |
| OS + systemd + journald | ~0.4 G |
| **稳态合计** | **~1.7 GB** |

瞬时叠加：每个 timer 跑 ~50–100 MB（剥 Prefect 后）；openclaw 对话并发时会从 613 MB 往上涨。

**4 GB 上**：稳态 ~1.7 G，留 ~2.3 G 给瞬时峰值 + 页缓存 → **能跑，但不宽裕**。**4 GB swap 必须有**。风险：openclaw 对话高峰 + 重 timer 撞点可能冲到 2.5–3 G+，靠 swap 兜但有延迟。

**降压杠杆**：① **剥 Prefect**——已完成（commit `afe3e60`，timer 单跑从 ~250 MB 降到 ~50–100 MB，削最大瞬时尖峰；重试由零依赖 `core/retry.py` 顶上）；② **调 MySQL**（1.3）别让它涨。

**CPU 2 核够**：负载是网络/IO 密集（TikTok API、DB），非计算密集。

**结论**：**安静上线（对话量不大）2C4G + swap + 调 MySQL 够用**（Prefect 已剥离）；**预期有真实客户对话量则直接 2C8G**——openclaw（613 MB 固定 + 对话时 spiky）是承载客户对话的进程，也是将来逼你升配的主因。

### 1.2 MySQL 用 Docker 还是原生安装

| | 原生 apt 安装 | Docker 容器 |
|---|---|---|
| 内存开销 | 略低（省 dockerd ~100–150 MB） | 多 dockerd 常驻；MySQL 本身占用两者一样 |
| 复杂度 | 简单，systemd 直管 | 多一层，但与 hp 一致 |
| 备份/恢复演练 | 需 DB 管理账号建库 | 起一次性容器即可还原，DR 最干净（见附录 B） |

**2C4G 推荐：原生 apt 安装 MySQL**，省那 ~100–150 MB、少一层。Docker 的“多占资源”主要就是 dockerd 那点常驻 + 镜像盘占用，MySQL 进程本身两种方式占用相同。若你更看重与 hp 一致和恢复演练顺手，用 Docker 也行——差异不大，**真正的内存大头是 MySQL 自己的配置（1.3），不是 Docker**。

### 1.3 MySQL 低内存调优（2C4G 必做）

`/etc/mysql/mysql.conf.d/zz-datahub.cnf`（原生）或容器 `--config`：

```ini
[mysqld]
innodb_buffer_pool_size = 512M     # 数据量小，512M 足够；别用默认放任增长
innodb_log_file_size    = 128M
max_connections         = 50       # 本应用连接池 pool_size=10 + overflow=20，50 富余
performance_schema      = OFF      # 省 ~200–400M，生产排障用慢日志即可
character-set-server    = utf8mb4
collation-server        = utf8mb4_unicode_ci
```

---

## 2. 架构组件清单

生产机上运行（全部 `systemctl --user`，靠 linger 常驻）：

- **MySQL**：业务库 + 审计库（同库不同表）。
- **data-hub.service**：FastAPI（uvicorn），仅听 `127.0.0.1:8000`。
- **cloudflared-board.service**：命名隧道，把 `127.0.0.1:8000` 暴露成 `https://<你的域名>`（看板/对话/报告/飞书登录入口），按 path 白名单放行。
- **定时任务 timer（共 13 个）**：sync-orders / sync-inventory / sync-fulfillments / sync-sku-variants / sync-ad-spend / sync-unsettled-fees / aggregate-profit / scan-alerts / push-replenishment / refresh-tokens / **anchor-audit** / **verify-audit-chain** / **backup-db**。
- **data-onfailure@.service**：任一 timer 失败 → 飞书告警（模板单元，不单独 enable）。
- **openclaw**：飞书网关（外部 node 项目），同机 `127.0.0.1:8000/api/data/*` 调本服务；负责告警直投、飞书登录、客户对话。

安全边界：`/api/data/*` 仅本机带 `X-Internal-Token` 可调，公网隧道不放行；隧道按 path 正则白名单（见 `deploy/cloudflared/config.yml`）。

---

## 3. 前置准备（动手前先备齐）

1. **服务器**：Ubuntu 22.04/24.04，非 root sudo 用户（下文设其为部署用户）。
2. **域名 + Cloudflare**：一个受 Cloudflare 托管的域名（命名隧道 + 自动 HTTPS 证书）。**用一级子域名**（如 `board.example.com`），别用三级（Universal SSL SAN 不覆盖三级，TLS 握手失败——见 cloudflared config 注释）。
3. **TikTok Shop Partner**：生产 app 的 `app_key`/`app_secret`；OAuth 回调地址；**出口 IP 白名单**（生产机直连出口 IP，见记忆 `tiktok-api-direct-connect`）。
4. **飞书 app**：每个租户一组 `app_id`/`app_secret`；后台登记 OAuth 回调白名单、开通 `contact:user.base:readonly` 并发版。
5. **LLM key**：Web 对话用（DeepSeek/Qwen/Claude 等任一，见 `LLMConfig`）。
6. **新生成的密钥**（生产独立，**勿复用 hp 的**）：见 5.2。

---

## 4. 系统初始化

```bash
# 4.1 系统依赖（Ubuntu）
sudo apt update
sudo apt install -y git curl build-essential mysql-server gnupg

# 4.2 uv（部署用户身份，非 sudo）
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc && uv --version

# 4.3 时区——定时任务 OnCalendar 按服务器 OS 本地时区解读（单元注释假定 CST）
sudo timedatectl set-timezone Asia/Shanghai
#   若生产机定在别的时区，要么改这里，要么逐个改 deploy/systemd/*.timer 的 OnCalendar

# 4.4 linger——关键！没它则部署用户登出后 systemctl --user 的服务/定时器全停
sudo loginctl enable-linger "$USER"

# 4.4b swap——阿里云/云 ECS 镜像默认【不带 swap】，2C4G 必须手动建（瞬时峰值兜底）
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swap.conf && sudo sysctl -p /etc/sysctl.d/99-swap.conf
#   swap 在云盘上比内存慢，是安全网不是常态；若被持续吃 → 该升 RAM

# 4.5 cloudflared（装到 ~/.local/bin，单元用绝对路径）
mkdir -p ~/.local/bin
curl -L -o ~/.local/bin/cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x ~/.local/bin/cloudflared

# 4.6 openclaw（飞书网关，外部 node 项目）——按其自身文档装 node + openclaw，
#     记下可执行路径(常在 ~/.nvm/.../bin/openclaw)，5.1 写进 .env 的 OPENCLAW_BIN
```

**MySQL 建库建账号**（注意：应用账号刻意**最小权限**，但**留一个能 `CREATE DATABASE` 的管理账号**用于恢复演练/重建，见附录 B 教训）：

```sql
CREATE DATABASE ai_data_pipeline
  DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci;
CREATE USER 'appuser'@'localhost' IDENTIFIED BY '<强随机密码>';
GRANT ALL PRIVILEGES ON ai_data_pipeline.* TO 'appuser'@'localhost';
FLUSH PRIVILEGES;
```

应用与 DB 同机时 `DB__HOST=127.0.0.1`。把 1.3 的调优文件放好后 `sudo systemctl restart mysql`。

---

## 5. 克隆与配置

### 5.1 克隆到约定路径（systemd 单元写死此路径）

> **单元文件用 `%h/code/crossborder-ops-data-hub`**（`WorkingDirectory`/`ExecStart`），所以仓库**必须**在 `~/code/crossborder-ops-data-hub`。换路径就得改所有单元。

```bash
mkdir -p ~/code && cd ~/code
git clone git@github.com:agentiker/crossborder-ops-data-hub.git
cd crossborder-ops-data-hub
uv sync          # 装依赖（生产入口，非 uv pip install -e .）
```

### 5.2 生成生产独立密钥（**勿复用 hp**）

```bash
# token 加密 Fernet key（44 字符）
uv run python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
# 备份 GPG 口令
openssl rand -base64 32
# 内部 token / 各 HMAC secret
python3 -c "import secrets;print(secrets.token_urlsafe(32))"   # 跑 4 次，分别用于下面 4 个
```

> 生成后**立刻把 `TOKEN_ENCRYPTION_KEY` 和 `BACKUP_GPG_PASSPHRASE` 存进密码管理器**（off-box）。丢机 = token 不可解 + 备份打不开。详见 6.4。

### 5.3 `.env` 完整清单

```env
# ── 数据库 ──
DB__HOST=127.0.0.1
DB__PORT=3306
DB__USER=appuser
DB__PASSWORD=<强随机>
DB__DATABASE=ai_data_pipeline

# ── TikTok（生产 app）──
TIKTOK__APP_KEY=<生产 app_key>
TIKTOK__APP_SECRET=<生产 app_secret>
TIKTOK__BASE_URL=https://open-api.tiktokglobalshop.com
TIKTOK__AUTH_BASE_URL=https://auth.tiktok-shops.com

# ── 内部 API（openclaw 调用）──
API__HOST=127.0.0.1                 # 永远回环，别 0.0.0.0
API__PORT=8000
API__INTERNAL_TOKEN=<secrets#1>     # 须与 openclaw 侧 DATA_HUB_TOKEN 完全一致

# ── 看板签名链接（飞书 H5 / 报告）──
DASHBOARD__LINK_SECRET=<secrets#2>
DASHBOARD__PUBLIC_BASE_URL=https://board.example.com

# ── 飞书 OAuth 网页免登（多租户用 JSON 串）──
FEISHU_OAUTH__APPS={"ecom-app":{"app_id":"cli_xxx","app_secret":"yyy"}}
FEISHU_OAUTH__SESSION_SECRET=<secrets#3>
FEISHU_OAUTH__COOKIE_SECURE=true    # 生产 HTTPS 必须 true
# 多租户子域名映射（按需，单租户可省）
TENANCY__HOST_TO_ACCOUNT={"board.example.com":"ecom-app"}
TENANCY__PUBLIC_BASE_URL={"ecom-app":"https://board.example.com"}

# ── Web 对话 LLM ──
LLM__PROVIDER=openai
LLM__BASE_URL=https://api.deepseek.com/v1
LLM__API_KEY=<llm key>
LLM__MODEL=deepseek-chat

# ── openclaw 直投（绝对路径，systemd PATH 无 nvm）──
OPENCLAW_BIN=/home/<user>/.nvm/versions/node/vXX/bin/openclaw

# ── 上线合规（本轮新增）──
TOKEN_ENCRYPTION_KEY=<Fernet key>           # 配好后再授权店铺，token 即密文落库
BACKUP_GPG_PASSPHRASE=<openssl rand 口令>
AUDIT_ANCHOR_ENABLED=true
AUDIT_ANCHOR_ACCOUNT=<飞书 openclaw account 键，如 main-app>
AUDIT_ANCHOR_OPEN_ID=<运维飞书 open_id ou_xxx>   # 同 notify-failure 收件人
```

> 可选阈值（告警/补货/利润等）都有默认值，无需配；要调见 `core/config.py` 注释。`deploy.sh` 会自动补 `OPENCLAW_BIN`（若探测到）。

---

## 6. 部署与合规初始化

### 6.1 一键部署

```bash
cd ~/code/crossborder-ops-data-hub
./deploy/deploy.sh --restart-web
```

`deploy.sh` 幂等做：`uv sync` → `init_db`（建全部表，**含两审计表，空表**）→ 装 `deploy/systemd/*` → `daemon-reload` + enable **全部 timer** + `data-hub.service` + `cloudflared-board.service` → 重启 web。

> **审计链格式**：全新生产库的审计表是空的，从第一行起就是当前 canonical 格式 → **无需 truncate**（hp 当初要清是因为有旧格式历史行；全新库不存在该问题）。

### 6.2 token 加密——全新库无需迁移

全新生产库**没有存量明文 token**：只要 `TOKEN_ENCRYPTION_KEY` 在**授权店铺之前**就配好（5.3 已配），授权时 token 直接密文落库。
`scripts/migrate_encrypt_tokens.py` **仅用于迁移已有明文 DB**，全新部署不用跑。

### 6.3 cloudflared 命名隧道（**不用 nginx**）

公网入口全部由 cloudflared 承担，**不需要 nginx/certbot**。云 ECS 上这更优：隧道是**出站**连 Cloudflare → ECS **不开任何入站端口**（安全组 80/443 全关，只留 SSH），无公网攻击面、无证书运维。

```bash
cloudflared tunnel login                          # 浏览器授权 Cloudflare（选你的域名 zone）
cloudflared tunnel create board                   # 生成隧道 + 凭据 json（记下 UUID）
cloudflared tunnel route dns board board.example.com
# 编辑 deploy/cloudflared/config.yml：改 tunnel 名/UUID、credentials-file 路径、ingress 里的 hostname
systemctl --user restart cloudflared-board.service
```

Cloudflare SSL 模式设 **Full**（勿 Full strict，源站无证书）。

> **TikTok OAuth 回调也走隧道**（无需 nginx）：在 `deploy/cloudflared/config.yml` 的 ingress 加一条放行回调路径，TikTok Partner Center 回调登记 `https://board.example.com/auth/callback/tiktok`：
> ```yaml
>   - hostname: board.example.com
>     path: "^/auth/callback/tiktok($|/)"
>     service: http://127.0.0.1:8000
> ```
> 仓库内 `deploy/nginx.conf` 是早期方案遗留，生产不用。

### 6.4 密钥托管（off-box，硬要求）

把 `TOKEN_ENCRYPTION_KEY` + `BACKUP_GPG_PASSPHRASE` 存进**密码管理器**（iCloud 钥匙串 / Google PM 可用，须强密码 + 2FA；团队级用 Bitwarden/1Password）。两条铁律：

1. **锁钥分离**：若把生产加密备份也异地推到 hp，则 `BACKUP_GPG_PASSPHRASE` **不能也放 hp**（拿下 hp 即解全部数据）——放密码管理器。
2. 每个环境独立密钥（生产 ≠ hp）。

---

## 7. 验证（提交审核前自测）

```bash
# 7.1 web 健康
systemctl --user is-active data-hub.service
curl -s http://127.0.0.1:8000/health

# 7.2 多租户隔离回归（命门，必跑绿）
uv run pytest tests/test_tenant_filter.py -q
uv run pytest -q                                   # 全量

# 7.3 授权店铺（沙箱/测试店先行）后，跑一轮同步
systemctl --user start data-sync-inventory.service
systemctl --user start data-sync-orders.service

# 7.4 审计链完好
uv run python -m scripts.verify_audit_chain

# 7.5 锚定能发飞书（看到 "✅ 已投递运维"）
uv run python -m flows.anchor_audit_chain

# 7.6 加密备份 + 恢复演练（见附录 B，证明备份真能还原）
uv run python -m scripts.backup_db
```

对照清单：
- [ ] `API__HOST=127.0.0.1`、`API__INTERNAL_TOKEN` 与 openclaw `DATA_HUB_TOKEN` 一致
- [ ] 隧道公网只放行白名单 path，`/api/data/*` 公网不可达
- [ ] TikTok Partner Center：OAuth 回调地址 + **出口 IP 白名单**含生产机 IP
- [ ] 飞书后台：回调白名单 + `contact:user.base:readonly` 已发版
- [ ] `TOKEN_ENCRYPTION_KEY`/`BACKUP_GPG_PASSPHRASE` 已存密码管理器
- [ ] `loginctl enable-linger` 已开（登出后服务不停）
- [ ] 13 个 timer `systemctl --user list-timers 'data-*'` 全部 enabled

---

## 8. 上线后

- **日报 openclaw cron**：不在 deploy.sh 内，手工配，见 `docs/proactive-push-ops.md` B 节。
- **监控**：timer 失败自动飞书告警（OnFailure）；审计链每 6h verify、每日 02:43 备份、02:07 锚定。
- **提交 TikTok 审核** → 通过后授权**真实生产店** → 正式运营。

---

## 9. 更新与回滚

```bash
# 更新（改了 web 加载的代码才 --restart-web；只改 flows 不用）
cd ~/code/crossborder-ops-data-hub
git pull && ./deploy/deploy.sh --restart-web      # deploy.sh 内含 uv sync + init_db

# 回滚
git checkout <上一个好 tag/commit> && ./deploy/deploy.sh --restart-web
```

> 改了 `frontend/` 要单独 `npm run build`（dist 不入库），见 deploy-hp skill §2.5。

---

## 附录 A：systemd 单元一览

`deploy/deploy.sh` 自动安装 enable `deploy/systemd/` 下全部 `*.service`/`*.timer`。新增 timer 只要丢进该目录重跑 deploy 即生效。常驻服务：`data-hub.service`、`cloudflared-board.service`。定时任务时刻见各 `.timer` 的 `OnCalendar`（CST）。

## 附录 B：恢复演练 / DR 手册（已在 hp 验证）

**恢复三要素**：`BACKUP_GPG_PASSPHRASE`（解密）+ **DB 管理权限**（建库——应用账号 `appuser` 最小权限**不能** `CREATE DATABASE`）+ `TOKEN_ENCRYPTION_KEY`（还原后应用读 token）。

最干净的演练 = 起一次性 MySQL 容器还原（等同全新机，不碰现网）：

```bash
PF=$(mktemp); chmod 600 "$PF"; grep '^BACKUP_GPG_PASSPHRASE=' .env | cut -d= -f2- > "$PF"
BK=$(ls -t ~/backups/data-hub/datahub-*.sql.gz.gpg | head -1)
RPW=$(openssl rand -hex 12)
docker run -d --name dh_restore_test -e MYSQL_ROOT_PASSWORD="$RPW" \
  -e MYSQL_ROOT_HOST=% -e MYSQL_DATABASE=restore_test -p 127.0.0.1:3307:3306 mysql:8.0
# 等就绪后：解密+解压+灌入
gpg --batch --passphrase-file "$PF" --decrypt "$BK" | gunzip \
  | docker exec -i dh_restore_test mysql -uroot -p"$RPW" restore_test
# 核对 + 应用读测试（指向 3307）
DB__HOST=127.0.0.1 DB__PORT=3307 DB__USER=root DB__PASSWORD="$RPW" DB__DATABASE=restore_test \
  PYTHONPATH="$PWD" uv run python -c "from core.tenancy import *; set_current_account('__bypass__'); \
from core.db import SessionLocal; from models.base_models import OrderHeader, PlatformToken; \
s=SessionLocal(); print('orders', len(s.query(OrderHeader.id).all())); \
print('tokens decrypted', sum(1 for t in s.query(PlatformToken).all() if t.access_token and not t.access_token.startswith('fcr1:')))"
docker rm -f dh_restore_test; rm -f "$PF"     # 焚毁
```

预期：表数/行数合理、token 全部成功解密（证明端到端可恢复）。
