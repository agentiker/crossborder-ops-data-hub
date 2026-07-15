#!/usr/bin/env bash
# 定时任务失败 → 飞书告警（由 systemd OnFailure=data-onfailure@%n.service 触发）。
#
# 用法：notify-failure.sh <失败的 unit 全名>   （%i，如 data-sync-orders.service）
# 取该 unit 最近日志，用 openclaw message send 发给运维（不经 LLM，本地 gateway RPC）。
#
# 谁来收：默认运维本人（main-app / NOTIFY_OPENID）。可用环境变量覆盖：
#   NOTIFY_ACCOUNT（默认 main-app）、NOTIFY_OPENID（默认运维 open_id）、OPENCLAW_BIN。
#
# 自爆防护：本脚本任何失败都吞掉并 exit 0——告警发不出去不能反过来把 OnFailure 链搞挂。

set -uo pipefail

UNIT="${1:-unknown.service}"
ACCOUNT="${NOTIFY_ACCOUNT:-main-app}"
TARGET_OPENID="${NOTIFY_OPENID:-ou_9be99e1b5948d895c1775d27b9876d0e}"

# openclaw 路径：环境优先，否则 PATH，否则探测 nvm（systemd PATH 不含 nvm）。
OPENCLAW="${OPENCLAW_BIN:-}"
[ -z "$OPENCLAW" ] && OPENCLAW="$(command -v openclaw 2>/dev/null || true)"
[ -z "$OPENCLAW" ] && OPENCLAW="$(ls "$HOME"/.nvm/versions/node/*/bin/openclaw 2>/dev/null | head -1 || true)"
[ -z "$OPENCLAW" ] && { echo "notify-failure: 找不到 openclaw，跳过告警" >&2; exit 0; }

# openclaw 是 node CLI，内部会调 `node`；systemd service PATH 不含 nvm 目录，
# 把 openclaw 所在目录（同目录就有 node）加进 PATH，否则 message send 失败。
export PATH="$(dirname "$OPENCLAW"):${PATH:-/usr/bin:/bin}"

LOG="$(journalctl --user -u "$UNIT" -n 12 --no-pager 2>/dev/null | tail -12)"
[ -z "$LOG" ] && LOG="(无日志)"

MSG="🔴 定时任务失败：${UNIT}
主机：$(hostname) · $(date '+%m-%d %H:%M')
最近日志：
${LOG}"

"$OPENCLAW" message send --channel feishu --account "$ACCOUNT" \
  --target "user:${TARGET_OPENID}" --message "$MSG" >/dev/null 2>&1 \
  || echo "notify-failure: message send 失败（已忽略）" >&2

exit 0
