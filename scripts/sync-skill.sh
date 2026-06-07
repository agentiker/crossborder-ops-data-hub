#!/usr/bin/env bash
# Sync openclaw-skills/crossborder-ops-data → ~/.openclaw/workspace-ecom/skills/
#
# Why: openclaw 的 skill loader 拒绝软链跳出 workspace root（log 关键字
# "Skipping escaped skill path outside its configured root: reason=symlink-escape"），
# 所以 skill 不能用 symlink，必须在 workspace 内是实文件。这个脚本把仓库副本
# 同步到 workspace。
#
# Usage (在服务器上跑)：
#   git pull
#   ./scripts/sync-skill.sh           # 同步
#   ./scripts/sync-skill.sh --check   # 仅 diff，不动文件
#
# 同步后，飞书 ecom 对话发 /new 才能看到新指令（/reset 不重载文件）。

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO_DIR/openclaw-skills/crossborder-ops-data"
DST="$HOME/.openclaw/workspace-ecom/skills/crossborder-ops-data"

if [[ ! -d "$SRC" ]]; then
  echo "ERROR: skill source not found at $SRC" >&2
  exit 1
fi

if [[ "${1-}" == "--check" ]]; then
  if [[ ! -d "$DST" ]]; then
    echo "DST 不存在：$DST （首次同步请去掉 --check）"
    exit 1
  fi
  echo "比较 $SRC ↔ $DST："
  if diff -rq "$SRC" "$DST" >/dev/null 2>&1; then
    echo "  ✅ 完全一致，无需同步"
  else
    diff -rq "$SRC" "$DST" || true
    echo
    echo "→ 跑 ./scripts/sync-skill.sh 同步"
    exit 1
  fi
  exit 0
fi

# 如果 DST 还是软链（首次迁移），先删
if [[ -L "$DST" ]]; then
  echo "检测到 $DST 是软链，先删除（openclaw 会拒绝它）"
  rm "$DST"
fi

mkdir -p "$DST"
rsync -av --delete "$SRC/" "$DST/" | tail -10

echo
echo "✅ Synced  $SRC"
echo "      →   $DST"
echo
echo "下一步：在飞书 ecom 对话发 /new（开新会话，重载 SKILL.md）"
echo "       发 /reset 只清上下文，不重载文件——不够。"
