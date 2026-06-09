# 测试服务器部署手册

本文用于把 Crossborder Ops Data Hub 部署到测试服务器，并把 `openclaw-skills/` 下的 skill 同步给 openclaw 使用。

## 部署建议

测试服务器建议采用 **git clone 源码部署**，不要先做 wheel/容器打包。

原因：

- 当前项目仍在频繁调试 TTS 授权、订单同步、库存同步和 skill 契约，源码部署便于 `git pull` 后快速验证。
- 服务器与本开发环境在同一局域网，排查网络、MySQL、openclaw 调用链路会更直接。
- 本项目运行入口简单，`uv` 已能稳定创建隔离环境；测试期没必要引入打包和发布流程的额外复杂度。

后续进入稳定生产后，再考虑 systemd + 固定 tag 部署，或打包成容器/制品。

## 目标拓扑

测试服务器上运行：

- MySQL：业务数据库。
- Data Hub FastAPI：只监听 `127.0.0.1:8000`。
- openclaw：同机通过 `http://127.0.0.1:8000/api/data/*` 调用 Data Hub。
- nginx：仅公网转发 TikTok OAuth 回调 `/auth/callback/tiktok`，不转发 `/api/data/*`。

关键安全边界：

- `/api/data/*` 只允许本机 openclaw skill 调用。
- Data Hub 与 openclaw 使用共享内部 token：服务端 `API__INTERNAL_TOKEN`，openclaw 侧 `DATA_HUB_TOKEN`。
- TikTok OAuth 回调需要公网 HTTPS，nginx 只放行回调路径。

## 1. 准备系统依赖

以下命令以 Ubuntu/Debian 为例：

```bash
sudo apt update
sudo apt install -y git curl build-essential python3.10 python3.10-venv nginx mysql-client
```

安装 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
uv --version
```

如果服务器默认 Python 不是 3.10+，安装对应版本后用 `uv python pin 3.10` 或直接让 `uv` 管理 Python。

## 2. 克隆项目

建议放在固定目录，例如 `/opt/crossborder-ops-data-hub`：

```bash
sudo mkdir -p /opt
sudo chown "$USER":"$USER" /opt
cd /opt
git clone git@github.com:agentiker/crossborder-ops-data-hub.git
cd crossborder-ops-data-hub
```

安装依赖：

```bash
uv venv
uv pip install -e .
```

验证：

```bash
uv run pytest
```

## 3. 配置 MySQL

创建数据库和用户，示例：

```sql
CREATE DATABASE crossborder_ops_data_hub
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

CREATE USER 'datahub'@'localhost' IDENTIFIED BY 'replace-with-strong-password';
GRANT ALL PRIVILEGES ON crossborder_ops_data_hub.* TO 'datahub'@'localhost';
FLUSH PRIVILEGES;
```

如果 MySQL 在同机，`DB__HOST=localhost` 即可；如果是 Docker 或独立主机，按实际网络地址配置。

## 4. 配置 Data Hub `.env`

复制示例配置：

```bash
cp .env.example .env
```

生成内部 token：

```bash
python3 -c "import secrets;print(secrets.token_urlsafe(32))"
```

编辑 `.env`：

```env
DB__HOST=localhost
DB__PORT=3306
DB__USER=datahub
DB__PASSWORD=replace-with-strong-password
DB__DATABASE=crossborder_ops_data_hub

TIKTOK__APP_KEY=replace-with-app-key
TIKTOK__APP_SECRET=replace-with-app-secret
TIKTOK__BASE_URL=https://open-api.tiktokglobalshop.com
TIKTOK__AUTH_BASE_URL=https://auth.tiktok-shops.com

API__HOST=127.0.0.1
API__PORT=8000
API__INTERNAL_TOKEN=replace-with-generated-token
```

注意：

- `API__HOST` 保持 `127.0.0.1`，不要改成 `0.0.0.0`。
- `API__INTERNAL_TOKEN` 必须和 openclaw 侧 `DATA_HUB_TOKEN` 完全一致。
- `TIKTOK__REDIRECT_URI` 不需要配置；回调地址以 TikTok Shop Partner Center 后台登记值为准。

初始化数据库表：

```bash
uv run python -c "from core.db import init_db; init_db()"
```

## 5. 启动 Data Hub Web 服务

调试启动：

```bash
uv run python main.py --task web --no-reload
```

本机验证：

```bash
curl http://127.0.0.1:8000/health
curl -H "X-Internal-Token: $API_INTERNAL_TOKEN" \
  "http://127.0.0.1:8000/api/data/overview?platform=tiktok_shop&country=ID"
```

如果 token 没放进 shell 环境，可以直接替换 `$API_INTERNAL_TOKEN` 为 `.env` 里的值。不要把 token 写入公开日志或聊天记录。

## 6. 用 systemd 托管 Data Hub

创建服务文件：

```bash
sudo tee /etc/systemd/system/data-hub.service >/dev/null <<'EOF'
[Unit]
Description=Crossborder Ops Data Hub
After=network.target mysql.service

[Service]
Type=simple
WorkingDirectory=/opt/crossborder-ops-data-hub
ExecStart=/opt/crossborder-ops-data-hub/.venv/bin/python main.py --task web --no-reload
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
```

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now data-hub
sudo systemctl status data-hub
journalctl -u data-hub -f
```

## 7. TikTok OAuth 回调与 nginx

如果今天只是测试本机 openclaw 查询，nginx 可以稍后搭建。但如果需要完成 TikTok Shop 授权回调，必须配置公网 HTTPS。

复制 nginx 配置：

```bash
sudo cp deploy/nginx.conf /etc/nginx/conf.d/data-hub.conf
sudo sed -i 's/your-domain.com/你的真实域名/g' /etc/nginx/conf.d/data-hub.conf
```

申请证书：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d 你的真实域名
```

验证并重载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

在 TikTok Shop Partner Center 登记回调：

```text
https://你的真实域名/auth/callback/tiktok
```

可选 HTML 回调：

```text
https://你的真实域名/auth/callback/tiktok/html
```

确认 nginx 不暴露数据接口：

```bash
curl -i https://你的真实域名/api/data/overview
```

预期返回 `404`。

## 8. TikTok 授权

推荐走平台发起授权，让 TikTok 回调 nginx 公网地址。

授权成功后，服务端会把 access token、refresh token、shop_cipher 保存到 MySQL。

也可以使用 CLI 手动授权：

```bash
uv run python auth.py <一次性auth_code>
```

注意：`auth_code` 只能使用一次。

## 9. 同步任务

手动调试：

```bash
uv run python -m flows.sync_orders
uv run python -m flows.sync_inventory
uv run python -m flows.refresh_tokens
```

订单和库存 flow 会打印出口 IP，用于核对 TikTok IP 白名单。

如果要传国家/店铺等参数，当前最直接方式是 Python 调用：

```bash
uv run python -c "from flows.sync_orders import sync_orders_flow; sync_orders_flow(country='ID', shop_id='replace-shop-id')"
uv run python -c "from flows.sync_inventory import sync_inventory_flow; sync_inventory_flow(country='ID', shop_id='replace-shop-id')"
```

定时调度可以先用 cron，测试期更直观：

```bash
crontab -e
```

示例：

```cron
0 * * * * cd /opt/crossborder-ops-data-hub && /opt/crossborder-ops-data-hub/.venv/bin/python -m flows.sync_orders >> /var/log/data-hub-orders.log 2>&1
5 * * * * cd /opt/crossborder-ops-data-hub && /opt/crossborder-ops-data-hub/.venv/bin/python -m flows.sync_inventory >> /var/log/data-hub-inventory.log 2>&1
*/30 * * * * cd /opt/crossborder-ops-data-hub && /opt/crossborder-ops-data-hub/.venv/bin/python -m flows.refresh_tokens >> /var/log/data-hub-tokens.log 2>&1
```

如果需要 Prefect UI，再启用 Prefect：

```bash
uv run prefect server start
uv run prefect deploy --all
```

测试期不强制上 Prefect，先用手动命令/cron 更容易排错。

## 10. 部署 openclaw skill

项目内 skill 源目录：

```text
/opt/crossborder-ops-data-hub/openclaw-skills/crossborder-ops-data
```

openclaw 的实际 skills 目录以服务器安装方式为准。建议用软链，避免复制后版本漂移：

```bash
mkdir -p /path/to/openclaw/skills
ln -sfn /opt/crossborder-ops-data-hub/openclaw-skills/crossborder-ops-data \
  /path/to/openclaw/skills/crossborder-ops-data
```

如果 openclaw 不支持软链，再复制：

```bash
rsync -a --delete \
  /opt/crossborder-ops-data-hub/openclaw-skills/crossborder-ops-data/ \
  /path/to/openclaw/skills/crossborder-ops-data/
```

openclaw 运行环境必须配置：

```env
DATA_HUB_URL=http://127.0.0.1:8000
DATA_HUB_TOKEN=replace-with-same-value-as-API__INTERNAL_TOKEN
```

如果 openclaw 用 systemd 启动，把环境变量写入 openclaw 的 service，而不是只在当前 shell `export`：

```ini
Environment=DATA_HUB_URL=http://127.0.0.1:8000
Environment=DATA_HUB_TOKEN=replace-with-same-value-as-API__INTERNAL_TOKEN
```

重启 openclaw 后验证 skill：

- 问："查看 TikTok 印尼近 7 天 GMV"
- 预期：openclaw 调用 `GET http://127.0.0.1:8000/api/data/orders/summary?...`
- 如果返回 401：检查 `DATA_HUB_TOKEN` 与 `API__INTERNAL_TOKEN` 是否一致。
- 如果返回 503：检查 Data Hub `.env` 是否配置 `API__INTERNAL_TOKEN`，并重启 Data Hub。
- 如果连接失败：检查 `data-hub.service` 是否运行、端口是否监听 `127.0.0.1:8000`。

## 11. 常用排查命令

查看 Data Hub：

```bash
systemctl status data-hub
journalctl -u data-hub -n 200 --no-pager
ss -lntp | grep 8000
curl http://127.0.0.1:8000/health
```

查看数据库：

```bash
mysql -u datahub -p crossborder_ops_data_hub
SHOW TABLES;
SELECT platform, country, shop_id, shop_cipher, token_expire_at FROM platform_tokens;
SELECT resource, window_end, last_synced_at FROM sync_cursors;
```

验证内部接口：

```bash
curl -H "X-Internal-Token: <token>" \
  "http://127.0.0.1:8000/api/data/orders/summary?platform=tiktok_shop&country=ID"
```

验证公网只暴露 OAuth：

```bash
curl -i https://你的真实域名/auth/callback/tiktok
curl -i https://你的真实域名/api/data/overview
```

## 12. 更新流程

测试期源码部署更新：

```bash
cd /opt/crossborder-ops-data-hub
git pull
uv pip install -e .
uv run pytest
uv run python -c "from core.db import init_db; init_db()"
sudo systemctl restart data-hub
```

如果 skill 使用软链，不需要额外同步；如果使用复制方式，需要重新 `rsync` 到 openclaw skills 目录并重启 openclaw。

## 13. 上线前检查清单

- MySQL 表已初始化。
- `.env` 中 `API__HOST=127.0.0.1`。
- `.env` 中 `API__INTERNAL_TOKEN` 已配置且强随机。
- openclaw 环境中 `DATA_HUB_URL=http://127.0.0.1:8000`。
- openclaw 环境中 `DATA_HUB_TOKEN` 与服务端 token 一致。
- Data Hub `/health` 正常。
- `/api/data/*` 本机带 token 可访问，不带 token 返回 401 或 503。
- nginx 公网只放行 `/auth/callback/tiktok` 和 `/auth/callback/tiktok/html`。
- TikTok Partner Center 的 IP 白名单包含服务器出口 IP。
- TikTok Partner Center 的 OAuth 回调地址指向 nginx HTTPS 域名。
