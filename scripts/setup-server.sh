#!/usr/bin/env bash
# 全新生产机基础环境一键初始化（对应 docs/production-deployment.md §4）。
# 在目标机以**部署用户**（非 root，但能 sudo）身份跑：
#   bash scripts/setup-server.sh [--dry-run]
#
# 做什么（幂等，每步 detect-then-act，可重复跑）：
#   1) apt 系统依赖（git/build/mysql-server/gnupg…）          [sudo]
#   2) swap 4G（云 ECS 镜像默认不带，2C4G 必须）              [sudo]
#   3) 时区 Asia/Shanghai（跟操作者 CST，非店铺/机房）         [sudo]
#   4) linger（登出后 systemctl --user 不停）                  [sudo]
#   5) MySQL 低内存调优 cnf（2C4G）                            [sudo]
#   6) uv（部署用户身份）
#   7) cloudflared → ~/.local/bin
#   8) nvm + node(LTS) + openclaw（飞书网关）
#
# 不做（刻意，需人工决策/含密钥）：建库建账号、写 .env、装 systemd 单元、配 openclaw.json、
#   授权店铺。这些见手册 §4(建库)/§5(.env)/§6(deploy.sh) 与 docs/openclaw-setup.md。
# 跑完按结尾打印的「下一步」继续。

set -euo pipefail

DRY=0
for a in "$@"; do case "$a" in
  --dry-run) DRY=1 ;;
  -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
  *) echo "未知参数：$a" >&2; exit 2 ;;
esac; done

run()  { echo "+ $*"; [ "$DRY" -eq 1 ] || "$@"; }
have() { command -v "$1" >/dev/null 2>&1; }
step() { echo; echo "── $* ──"; }

[ "$(id -u)" -eq 0 ] && { echo "请以普通部署用户（非 root）身份跑，脚本内部按需 sudo。" >&2; exit 2; }
[ "$DRY" -eq 1 ] && echo "（dry-run：只打印不执行）"

# ── 1. apt 系统依赖 ──
step "1. apt 系统依赖"
if [ "$DRY" -eq 0 ]; then sudo apt-get update -qq; fi
run sudo apt-get install -y git curl build-essential mysql-server gnupg ca-certificates

# ── 2. swap 4G ──
step "2. swap 4G"
if swapon --show=NAME --noheadings 2>/dev/null | grep -q .; then
  echo "  已有 swap，跳过：$(swapon --show 2>/dev/null | tail -n +2 | awk '{print $1, $3}')"
else
  run sudo fallocate -l 4G /swapfile
  run sudo chmod 600 /swapfile
  run sudo mkswap /swapfile
  run sudo swapon /swapfile
  if ! grep -q '^/swapfile' /etc/fstab 2>/dev/null; then
    run sudo sh -c 'echo "/swapfile none swap sw 0 0" >> /etc/fstab'
  fi
  run sudo sh -c 'echo "vm.swappiness=10" > /etc/sysctl.d/99-swap.conf'
  run sudo sysctl -p /etc/sysctl.d/99-swap.conf
fi

# ── 3. 时区 CST（跟操作者，非店铺/机房；业务日 WIB 由 core/timezone 独立写死，不受影响）──
step "3. 时区 Asia/Shanghai"
TZ_NOW="$(timedatectl show -p Timezone --value 2>/dev/null || echo '')"
if [ "$TZ_NOW" = "Asia/Shanghai" ]; then
  echo "  已是 Asia/Shanghai，跳过"
else
  run sudo timedatectl set-timezone Asia/Shanghai
fi

# ── 4. linger ──
step "4. linger"
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo no)" = "yes" ]; then
  echo "  已开启，跳过"
else
  run sudo loginctl enable-linger "$USER"
fi

# ── 5. MySQL 低内存调优 ──
step "5. MySQL 低内存调优"
CNF=/etc/mysql/mysql.conf.d/zz-datahub.cnf
if [ -f "$CNF" ]; then
  echo "  已存在 $CNF，跳过（要改手动编辑）"
else
  TMP="$(mktemp)"
  cat > "$TMP" <<'EOF'
[mysqld]
innodb_buffer_pool_size = 512M
innodb_log_file_size    = 128M
max_connections         = 50
performance_schema      = OFF
character-set-server    = utf8mb4
collation-server        = utf8mb4_unicode_ci
EOF
  run sudo cp "$TMP" "$CNF"
  rm -f "$TMP"
  run sudo systemctl restart mysql
fi

# ── 6. uv ──
step "6. uv"
if have uv || [ -x "$HOME/.local/bin/uv" ]; then
  echo "  已安装：$("${HOME}/.local/bin/uv" --version 2>/dev/null || uv --version)"
else
  if [ "$DRY" -eq 0 ]; then curl -LsSf https://astral.sh/uv/install.sh | sh; fi
  echo "  装好；新 shell 或 source ~/.bashrc 后 uv 进 PATH"
fi

# ── 7. cloudflared ──
step "7. cloudflared → ~/.local/bin"
if [ -x "$HOME/.local/bin/cloudflared" ]; then
  echo "  已安装：$("$HOME/.local/bin/cloudflared" --version 2>/dev/null | head -1)"
else
  run mkdir -p "$HOME/.local/bin"
  run curl -L -o "$HOME/.local/bin/cloudflared" \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  run chmod +x "$HOME/.local/bin/cloudflared"
fi

# ── 8. nvm + node + openclaw ──
step "8. nvm + node(LTS) + openclaw"
export NVM_DIR="$HOME/.nvm"
if [ -s "$NVM_DIR/nvm.sh" ]; then
  echo "  nvm 已装"
else
  if [ "$DRY" -eq 0 ]; then
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
  fi
fi
if [ "$DRY" -eq 0 ] && [ -s "$NVM_DIR/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"
  if ! have node; then nvm install --lts; nvm alias default 'lts/*'; fi
  echo "  node $(node -v 2>/dev/null)"
  if have openclaw; then
    echo "  openclaw 已装：$(openclaw --version 2>/dev/null | head -1)"
  else
    npm install -g openclaw
  fi
  echo "  openclaw 路径（写进 .env 的 OPENCLAW_BIN）：$(command -v openclaw 2>/dev/null || echo '未找到')"
else
  echo "  （dry-run 或 nvm 未就绪，跳过 node/openclaw 安装）"
fi

cat <<'NEXT'

══ 基础环境就绪。下一步（见 docs/production-deployment.md）══
  1) MySQL 建库建账号（§4 末，注意留一个能 CREATE DATABASE 的管理账号）
  2) git clone 仓库到 ~/code/crossborder-ops-data-hub && uv sync
  3) 生成密钥 + 写 .env（§5；模板 .env.example）
  4) uv run python -m scripts.preflight        # 部署前预检，绿了再继续
  5) ./deploy/deploy.sh --restart-web          # 一键部署（建表/装 timer/起服务）
  6) cloudflared 命名隧道（§6.3）+ openclaw 网关配置（docs/openclaw-setup.md）
  7) 飞书后台 / TikTok Partner Center 登记回调（§5 飞书后台配置）
NEXT
