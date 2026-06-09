#!/usr/bin/env bash
# Sync openclaw-skills/crossborder-ops-data → 每个 openclaw workspace 的 skills/
#
# Why: openclaw 的 skill loader 拒绝软链跳出 workspace root（log 关键字
# "Skipping escaped skill path outside its configured root: reason=symlink-escape"），
# 所以 skill 不能用 symlink，必须在 workspace 内是实文件。这个脚本把仓库副本
# 同步到每个 workspace。
#
# 为什么是"每个"：内部测试用 workspace-ecom，客户用 workspace-ecom-gtl。
# 以前这里写死了 workspace-ecom，导致客户那个长期漏更新（用旧 skill），
# 我们自测的是新版——口径不一致。现在统一循环 WORKSPACES 列表，加新客户
# workspace 只需往列表里加一行。
#
# Usage (在服务器上跑)：
#   git pull
#   ./scripts/sync-skill.sh           # 同步所有 workspace
#   ./scripts/sync-skill.sh --check   # 仅 diff，不动文件
#
# 同步后，飞书 ecom 对话发 /new 才能看到新指令（/reset 不重载文件）。

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO_DIR/openclaw-skills/crossborder-ops-data"

# 通用人格文件源（行为规范，所有 ecom workspace 应当一致）。
# 只同步 AGENTS.md / SOUL.md；USER.md / IDENTITY.md / DREAMS.md / HEARTBEAT.md / TOOLS.md
# 是 per-workspace 客户专属（含各自称呼、业务、身份），本脚本绝不碰——否则会把某个
# workspace 的个人称呼（如 main 的"阿🌟"）误推到客户 bot。
PERSONA_SRC="$REPO_DIR/openclaw-docs"
PERSONA_FILES=(AGENTS.md SOUL.md)

# 需要同步 skill 的 workspace 名（~/.openclaw/<name>）。
# 新增客户 workspace 时往这里加一行即可。
WORKSPACES=(
  workspace-ecom       # 内部测试
  workspace-ecom-gtl   # 客户（gtl）
)

if [[ ! -d "$SRC" ]]; then
  echo "ERROR: skill source not found at $SRC" >&2
  exit 1
fi

# dst_for <workspace> —— 拼出该 workspace 的 skill 目标路径
dst_for() { echo "$HOME/.openclaw/$1/skills/crossborder-ops-data"; }

if [[ "${1-}" == "--check" ]]; then
  rc=0
  for ws in "${WORKSPACES[@]}"; do
    dst="$(dst_for "$ws")"
    ws_root="$HOME/.openclaw/$ws"
    echo "── $ws ──"
    if [[ ! -d "$dst" ]]; then
      echo "  ⚠️  skill DST 不存在：$dst （首次同步请去掉 --check）"
      rc=1
    elif diff -rq "$SRC" "$dst" >/dev/null 2>&1; then
      echo "  ✅ skill 一致"
    else
      diff -rq "$SRC" "$dst" || true
      echo "  → skill 有差异"
      rc=1
    fi
    for pf in "${PERSONA_FILES[@]}"; do
      if [[ ! -f "$ws_root/$pf" ]]; then
        echo "  ⚠️  $pf 不存在：$ws_root/$pf"
        rc=1
      elif diff -q "$PERSONA_SRC/$pf" "$ws_root/$pf" >/dev/null 2>&1; then
        echo "  ✅ $pf 一致"
      else
        echo "  → $pf 有差异"
        rc=1
      fi
    done
  done
  [[ $rc -eq 0 ]] && echo && echo "全部一致，无需同步。"
  exit $rc
fi

for ws in "${WORKSPACES[@]}"; do
  dst="$(dst_for "$ws")"
  ws_root="$HOME/.openclaw/$ws"
  if [[ ! -d "$ws_root" ]]; then
    echo "⚠️  跳过 $ws：workspace 目录不存在（$ws_root）"
    continue
  fi
  echo "── 同步 → $ws ──"
  # 如果 dst 还是软链（首次迁移），先删
  if [[ -L "$dst" ]]; then
    echo "  检测到 $dst 是软链，先删除（openclaw 会拒绝它）"
    rm "$dst"
  fi
  mkdir -p "$dst"
  rsync -av --delete "$SRC/" "$dst/" | tail -5
  echo "  ✅ skill → $dst"
  # 同步通用人格文件（AGENTS.md/SOUL.md）；绝不碰 USER.md/IDENTITY.md 等 per-workspace 文件
  for pf in "${PERSONA_FILES[@]}"; do
    if [[ -f "$PERSONA_SRC/$pf" ]]; then
      cp "$PERSONA_SRC/$pf" "$ws_root/$pf"
      echo "  ✅ persona → $ws_root/$pf"
    else
      echo "  ⚠️  人格源缺失：$PERSONA_SRC/$pf（跳过）"
    fi
  done
  echo
done

echo "✅ Synced skill   $SRC"
echo "✅ Synced persona ${PERSONA_FILES[*]}  ← $PERSONA_SRC"
echo "      →   ${WORKSPACES[*]}"
echo
echo "下一步：在每个飞书对话发 /new（开新会话，重载 SKILL.md）"
echo "       发 /reset 只清上下文，不重载文件——不够。"
