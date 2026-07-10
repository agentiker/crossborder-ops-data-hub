#!/usr/bin/env bash
# Data Hub 生产部署（systemd user timer）。在目标机仓库根或任意处跑：
#   ./deploy/deploy.sh [--pull] [--sync-skill] [--restart-web] [--restart-gateway] [--restart-tunnel] [--no-business-timers] [--no-alert-timers] [--dry-run]
#
# --no-business-timers：过审前用。只 enable 基建 timer（审计锚定/备份/校验链）+ data-hub，
#   不 enable 业务同步/聚合/告警 timer——避免部署顺手把业务任务全拉起（含 scan-alerts 误发告警）。
#   不会 disable 已启用的业务 timer（只是"不新启"）；要停回过审前用 docs/ops-runbook.md 的 disable 循环。
#
# --no-alert-timers：中间态。enable 同步/聚合 timer（数据先跑起来），但跳过会主动给客户发飞书的
#   告警类 timer（scan-alerts 超时/低库存、push-replenishment 采购单）。过审前「想跑数据、不发客户」用它。
#   与 --no-business-timers 互斥语义上更细：前者只挡告警两枚，后者挡掉全部业务 timer。
#
# 另有 NOT_READY_TIMERS（脚本内常量，非 flag）：需额外授权/接通才能跑的 timer，**默认永远跳过并
#   disable**，避免部署把它们空跑刷错误日志。当前含 GMV Max 花费同步（待 Marketing API 授权）。
#   就绪后从常量移除即恢复正常 enable。
#
# 做什么（幂等）：
#   1) （--pull）git pull
#   2) uv sync 装依赖
#   2.5) 构建前端 SPA（frontend/dist；node/npm 在 nvm，脚本自动加载 PATH）
#   3) init_db 建新表（create_all，只建不存在的）
#   4) 确保项目 .env 有 OPENCLAW_BIN 绝对路径（告警/直投需要，systemd PATH 无 nvm）
#   5) 安装 deploy/systemd/*.{service,timer} 到 ~/.config/systemd/user/（unit 用 %h，无机器绝对路径）
#   6) daemon-reload + enable --now 所有 timer（NOT_READY_TIMERS 除外）+ data-hub.service
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

PULL=0; SYNC_SKILL=0; RESTART_WEB=0; RESTART_GW=0; RESTART_TUNNEL=0; NO_BIZ_TIMERS=0; NO_ALERT_TIMERS=0; DRY=0
for a in "$@"; do case "$a" in
  --pull) PULL=1 ;;
  --sync-skill) SYNC_SKILL=1 ;;
  --restart-web) RESTART_WEB=1 ;;
  --restart-gateway) RESTART_GW=1 ;;
  --restart-tunnel) RESTART_TUNNEL=1 ;;
  --no-business-timers) NO_BIZ_TIMERS=1 ;;
  --no-alert-timers) NO_ALERT_TIMERS=1 ;;
  --dry-run) DRY=1 ;;
  *) echo "未知参数：$a" >&2; exit 2 ;;
esac; done

# 过审前只起这些基建 timer（审计锚定/备份/校验链）；其余 data-* 视为业务 timer。
INFRA_TIMERS="data-anchor-audit.timer data-backup-db.timer data-verify-audit-chain.timer"
# 会主动给客户发飞书的告警类 timer：--no-alert-timers 时跳过（数据照跑、不发客户）。
ALERT_TIMERS="data-scan-alerts.timer data-push-replenishment.timer"
# 「尚未就绪」timer：需要额外授权/接通才能跑，**默认永远跳过**（不受任何 flag 控制），
# 避免部署把它们空跑报错。就绪后从此列表移除即恢复正常 enable。
#   - data-sync-gmv-max-spend.timer：GMV Max 花费须独立 Marketing API 授权(advertiser_id)，
#     未授权跑只会 API 失败刷日志（见 memory roi-roas-alert-data-source）。授权真打后删掉即可。
NOT_READY_TIMERS="data-sync-gmv-max-spend.timer"

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

if [ "$NO_BIZ_TIMERS" -eq 1 ]; then
  echo "-- enable timers + data-hub（过审前模式：仅基建 timer，跳过业务 timer）--"
else
  echo "-- enable timers + data-hub --"
fi
for t in "$UNIT_SRC"/*.timer; do
  name="$(basename "$t")"
  # 「尚未就绪」timer：无条件跳过（不 enable、也不 start），且主动 disable 掉——防止上一次部署
  # 或手动残留的启用态在本次 daemon-reload 后仍在跑。就绪后从 NOT_READY_TIMERS 移除即恢复。
  case " $NOT_READY_TIMERS " in
    *" $name "*)
      echo "  跳过未就绪 timer：${name}（需授权/接通，见 NOT_READY_TIMERS 注释）"
      run systemctl --user disable --now "$name" 2>/dev/null || true
      continue ;;
  esac
  # 过审前模式：业务 timer 不 enable（不在 INFRA 白名单内的一律跳过）。两端补空格做整词匹配。
  if [ "$NO_BIZ_TIMERS" -eq 1 ]; then
    case " $INFRA_TIMERS " in
      *" $name "*) : ;;  # 基建 timer，照常 enable
      *) echo "  跳过业务 timer：${name}（--no-business-timers）"; continue ;;
    esac
  fi
  # 中间态：只挡会给客户发飞书的告警类 timer，同步/聚合照常 enable。
  if [ "$NO_ALERT_TIMERS" -eq 1 ]; then
    case " $ALERT_TIMERS " in
      *" $name "*) echo "  跳过客户告警 timer：${name}（--no-alert-timers）"; continue ;;
    esac
  fi
  run systemctl --user enable --now "$name"
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
