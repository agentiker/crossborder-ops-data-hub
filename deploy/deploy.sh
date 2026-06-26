#!/usr/bin/env bash
# Data Hub 生产部署（systemd user timer）。在目标机仓库根或任意处跑：
#   ./deploy/deploy.sh [--pull] [--sync-skill] [--restart-web] [--restart-gateway] [--restart-tunnel] [--dry-run]
#
# 做什么（幂等）：
#   1) （--pull）git pull
#   2) uv sync 装依赖
#   2.5) 构建前端 SPA（frontend/dist；node/npm 在 nvm，脚本自动加载 PATH）
#   3) init_db 建新表（create_all，只建不存在的）
#   4) 确保项目 .env 有 OPENCLAW_BIN 绝对路径（告警/直投需要，systemd PATH 无 nvm）
#   5) 安装 deploy/systemd/*.{service,timer} 到 ~/.config/systemd/user/（unit 用 %h，无机器绝对路径）
#   6) daemon-reload + enable --now 所有 timer + data-hub.service
#   7) 打印 list-timers
#
# 不做：日报 openclaw cron（一次性/低频、直接动客户推送，手工配，见 docs/proactive-push-ops.md B 节）。
# 重启 web / gateway 默认不做（outward-facing），用 --restart-web / --restart-gateway 显式触发。

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$REPO_DIR/deploy/systemd"
UNIT_DST="$HOME/.config/systemd/user"
ENV_FILE="$REPO_DIR/.env"
UV="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"

PULL=0; SYNC_SKILL=0; RESTART_WEB=0; RESTART_GW=0; RESTART_TUNNEL=0; DRY=0
for a in "$@"; do case "$a" in
  --pull) PULL=1 ;;
  --sync-skill) SYNC_SKILL=1 ;;
  --restart-web) RESTART_WEB=1 ;;
  --restart-gateway) RESTART_GW=1 ;;
  --restart-tunnel) RESTART_TUNNEL=1 ;;
  --dry-run) DRY=1 ;;
  *) echo "未知参数：$a" >&2; exit 2 ;;
esac; done

run() { echo "+ $*"; [ "$DRY" -eq 1 ] || "$@"; }

cd "$REPO_DIR"
echo "== Data Hub 部署 @ $REPO_DIR =="

[ "$PULL" -eq 1 ] && run git pull

echo "-- 依赖 --"; run "$UV" sync

echo "-- 构建前端 SPA（frontend/dist）--"
# node/npm 在 nvm 里，systemd/非交互登录 PATH 通常没有；先加载 nvm，再退化到探测 node bin。
if ! command -v npm >/dev/null 2>&1; then
  export NVM_DIR="$HOME/.nvm"
  # shellcheck disable=SC1091
  [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" >/dev/null 2>&1 || true
  if ! command -v npm >/dev/null 2>&1; then
    NODE_BIN="$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1 || true)"
    [ -n "$NODE_BIN" ] && PATH="$NODE_BIN:$PATH"
  fi
fi
if command -v npm >/dev/null 2>&1; then
  # package-lock 可能与 package.json 不同步（npm ci 会拒绝），用 install 自行调和。
  run bash -c "cd '$REPO_DIR/frontend' && npm install --no-audit --no-fund && npm run build"
else
  echo "  ⚠️ 未探测到 npm，跳过前端构建（/app 将 404）。装 node 后重跑或手动 cd frontend && npm run build。"
fi

echo "-- 建表（init_db，幂等）--"
run "$UV" run python -c "from core.db import init_db; init_db()"

echo "-- 确保 .env 有 OPENCLAW_BIN --"
if [ ! -f "$ENV_FILE" ] || ! grep -q '^OPENCLAW_BIN=' "$ENV_FILE" 2>/dev/null; then
  OPENCLAW="$(command -v openclaw 2>/dev/null || ls "$HOME"/.nvm/versions/node/*/bin/openclaw 2>/dev/null | head -1 || true)"
  if [ -n "$OPENCLAW" ]; then
    if [ "$DRY" -eq 1 ]; then echo "+ echo OPENCLAW_BIN=$OPENCLAW >> .env";
    else echo "OPENCLAW_BIN=$OPENCLAW" >> "$ENV_FILE"; echo "  写入 OPENCLAW_BIN=$OPENCLAW"; fi
  else
    echo "  ⚠️ 未探测到 openclaw，请手动在 .env 加 OPENCLAW_BIN=<绝对路径>"
  fi
else
  echo "  已有 OPENCLAW_BIN，跳过"
fi

echo "-- 安装 systemd unit --"
run mkdir -p "$UNIT_DST"
for f in "$UNIT_SRC"/*.service "$UNIT_SRC"/*.timer; do
  run cp "$f" "$UNIT_DST/"
done
run systemctl --user daemon-reload

echo "-- enable timers + data-hub --"
for t in "$UNIT_SRC"/*.timer; do
  run systemctl --user enable --now "$(basename "$t")"
done
run systemctl --user enable data-hub.service
# 看板公网入口隧道（plan/14）：enable 开机自启；首次/更新靠 --restart-tunnel 拉起，
# 与 data-hub 同风格（不自动 start，避免对外动作被部署脚本静默触发）。
run systemctl --user enable cloudflared-board.service

[ "$SYNC_SKILL" -eq 1 ] && run "$REPO_DIR/scripts/sync-skill.sh"
[ "$RESTART_WEB" -eq 1 ] && run systemctl --user restart data-hub.service
[ "$RESTART_GW" -eq 1 ] && run systemctl --user restart openclaw-gateway
[ "$RESTART_TUNNEL" -eq 1 ] && run systemctl --user restart cloudflared-board.service

echo
[ "$DRY" -eq 1 ] || systemctl --user list-timers 'data-*' --no-pager
echo
echo "✅ 部署完成。"
echo "  · 日报 openclaw cron 仍手工配（docs/proactive-push-ops.md B 节）"
echo "  · 改了 plugin 文案才需 --restart-gateway；改 skill 后客户飞书 /new 重载"
