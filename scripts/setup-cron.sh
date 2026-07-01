#!/usr/bin/env bash
# 投产 openclaw 定时日报/周报 cron（参数化 + 幂等）。
#
# Why: cron 任务存在 ~/.openclaw/cron/jobs.json，不在本仓库，以前全靠手工
# `openclaw cron add` 配置——导致 prod 客户号一条 cron 都没建（hp 有、prod 空）。
# 本脚本把"每客户的日报/周报 cron"参数化，按客户列表循环 add/edit，纳入投产收尾步骤。
#
# 每条 cron = 定时唤起 agent，发一段固定 prompt 让它写一段定性经营分析，再调
# ops_report_card 工具——工具后端用真实数字拼「飞书原生卡片」直投收件人（KPI 分栏/
# 表格/折叠面板/彩色环比/底部报告按钮）。数字由后端渲染、零编造；agent 只产出分析段。
# cron 是 openclaw 侧（~/.openclaw/cron/），与 deploy.sh 管的 systemd timer 是两套，
# 故独立脚本、不塞进 deploy.sh。
#
# Usage（在服务器上跑）：
#   ./scripts/setup-cron.sh             # add 或 edit 所有 cron（幂等）
#   ./scripts/setup-cron.sh --check     # 仅列出差异，不动
#   ./scripts/setup-cron.sh --disabled  # 创建为 disabled（过审前预演用）
#
# 收件人 open_id 须客户飞书 bootstrap 后才知——故属"客户授权后"投产收尾步骤，
# 同业务 timer enable 时机。

set -euo pipefail

# ── 客户列表：account:agent:open_id:label ──────────────────────────────
# 这是**全集**（hp + prod 两台机器的所有客户）。新增客户往这里加一行即可。
# open_id 是飞书用户 id（cron 收件人 + ops_report 参数）。
# 实际建 cron 时只取**本机租户**那一行——见下方 DEFAULT_ACCOUNT 过滤。
CUSTOMERS_ALL=(
  "ecom-app:ecom:ou_7afe4514b269e5a0abfbd395f3f26410:ecom"
  "ecom-app-gtl:ecom-gtl:ou_5a27000e3e67de797de432a43bac29da:ecom-gtl"
)

# ── 按机器区分客户：每台机只给本机租户建 cron ────────────────────────
# hp 与 prod 共用本脚本，但客户不同：hp=ecom-app（单店 ecom）、prod=ecom-app-gtl。
# 权威信号 = .env 的 TENANCY__DEFAULT_ACCOUNT（prod 覆盖成 ecom-app-gtl，hp 未配→回落
# ecom-app，见 core/config.py TenancyConfig.default_account）。不用 hostname/host_to_account
# 判断——hp 的 host_to_account 仍残留 gtl 子域名映射，会误判。
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_ACCOUNT="ecom-app"
if [[ -f "$REPO_DIR/.env" ]]; then
  # grep 加 || true：hp 的 .env 没这行（回落 ecom-app），无匹配返回非零会在
  # set -e 下让整个脚本静默退出。取值后去掉引号和首尾空格。
  _da="$(grep -E '^TENANCY__DEFAULT_ACCOUNT=' "$REPO_DIR/.env" | tail -1 | cut -d= -f2- || true)"
  _da="${_da//\"/}"; _da="${_da//\'/}"; _da="$(echo "$_da" | xargs 2>/dev/null || true)"
  [[ -n "$_da" ]] && DEFAULT_ACCOUNT="$_da"
fi

CUSTOMERS=()
for _c in "${CUSTOMERS_ALL[@]}"; do
  [[ "${_c%%:*}" == "$DEFAULT_ACCOUNT" ]] && CUSTOMERS+=("$_c")
done
if [[ ${#CUSTOMERS[@]} -eq 0 ]]; then
  echo "⚠️  本机 DEFAULT_ACCOUNT=${DEFAULT_ACCOUNT} 在 CUSTOMERS_ALL 里没有匹配客户，未建任何 cron。" >&2
  echo "    （检查 .env 的 TENANCY__DEFAULT_ACCOUNT 或往 CUSTOMERS_ALL 加该租户一行）" >&2
  exit 1
fi
echo "本机租户 DEFAULT_ACCOUNT=${DEFAULT_ACCOUNT} → 建 ${#CUSTOMERS[@]} 个客户的 cron"

# ── cron 定义：name_suffix|cron_expr|period|template ────────────────────
JOBS=(
  "每日经营日报|30 8 * * *|yesterday|daily_brief"
  "每周经营周报|5 9 * * 1|last_week|weekly_review"
)

CHECK=0
DISABLED=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK=1 ;;
    --disabled) DISABLED=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

TZ="Asia/Shanghai"   # 操作者在 CST，晨报/周报按 CST 调（见 memory operator-cst-vs-shop-wib-timezones）

# cron 不绑定具体模型：报告用 openclaw agent 自己配置的模型（~/.openclaw/openclaw.json /
# .env 决定），代码不写死模型 id（同 services/llm 层原则）。思考独白外泄靠 agent 模型配置里
# 的 "reasoning": true 压住，不靠 cron 的 --thinking off。换模型在配置层改，cron 自动跟随。

# 日报/周报共用 prompt 模板：写一段定性经营分析 → 调 ops_report_card 工具直投卡片。
# 数字不写进 analysis（卡片用真实数据渲染），agent 只做归因+建议，杜绝编造。
build_message() {
  local template="$1" period="$2" open_id="$3"
  cat <<EOF
请调用 ops_report_card 工具生成并投递经营报告卡片。参数：template_name=${template}、period=${period}、open_id=${open_id}（open_id 必须原样使用 ${open_id}，禁止改动或自行编造）、analysis=<你写的一段经营分析>。

analysis 写什么：一段简洁的经营分析与运营建议，发挥运营顾问价值——① 环比归因（GMV/订单升降的可能主因）；② 风险优先级（哪个最该先处理，如某爆款要补货、某 SKU 快断货）；③ 可执行下一步（具体到补哪个 SKU / 关注哪个指标）。用飞书 markdown（**粗体**、换行、列表）写得清晰。

【关键】analysis 里**只写定性分析与建议，不要写具体数字**（GMV/订单/爆款/库存的数字会由卡片用真实数据自动渲染，你写数字反而会与卡片重复或冲突）。你也不需要自己拼卡片或写数字报告——调用 ops_report_card 后，后端会用真实数据渲染出带 KPI/表格/爆款/库存/报告按钮的飞书卡片直接发给用户。

工具返回 {"ok": true, "delivered": true} 即表示卡片已发出，你无需再发任何文字消息。若返回 ok=false，简要说明失败原因即可。

铁律：你对经营情况的定性判断应基于常识与运营经验给建议，但**不得编造任何具体数字**（GMV、订单量、佣金率、ROI 等一律不写进 analysis，交给卡片渲染真实值）。
EOF
}

# 查 job 是否已存在（按 name 精确匹配），返回 id 或空。
find_job_id() {
  local name="$1"
  openclaw cron list --json 2>/dev/null \
    | python3 -c "
import json, sys
try:
    jobs = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for j in (jobs if isinstance(jobs, list) else jobs.get('jobs', [])):
    if j.get('name') == '${name}':
        print(j.get('id', ''))
        break
"
}

for cust in "${CUSTOMERS[@]}"; do
  IFS=':' read -r account agent open_id label <<< "$cust"
  for job in "${JOBS[@]}"; do
    IFS='|' read -r suffix cron_expr period template <<< "$job"
    name="${label} ${suffix}"
    msg="$(build_message "${template}" "${period}" "${open_id}")"

    existing="$(find_job_id "${name}")"
    if [[ $CHECK -eq 1 ]]; then
      if [[ -z "$existing" ]]; then
        echo "  → 缺失：${name}（account=${account}）"
      else
        echo "  ✅ 已存在：${name}（id=${existing}）"
      fi
      continue
    fi

    # --no-deliver：显式关掉 openclaw 的 fallback 文字投递——卡片由 ops_report_card 工具
    # 后端直投收件人，agent 无需再 announce 文字。不关的话 openclaw 默认 announce->last 无路由
    # 会 fail-closed 报错（cron run 记 error，虽卡片已投成功）。--account 提供多租户上下文。
    common_args=(--name "${name}" --agent "${agent}" --account "${account}"
                 --cron "${cron_expr}" --tz "${TZ}" --exact
                 --session isolated --no-deliver --message "${msg}")
    [[ $DISABLED -eq 1 ]] && common_args+=(--disabled)

    if [[ -z "$existing" ]]; then
      echo "  + 创建：${name}"
      openclaw cron add "${common_args[@]}" >/dev/null
    else
      echo "  ~ 更新：${name}（id=${existing}）"
      openclaw cron edit "${existing}" "${common_args[@]}" >/dev/null
    fi
  done
done

if [[ $CHECK -eq 1 ]]; then
  echo
  echo "（--check 模式，未改动。去掉 --check 执行 add/edit。）"
else
  echo
  echo "✅ cron 同步完成（CUSTOMERS=${#CUSTOMERS[@]} × JOBS=${#JOBS[@]}）"
  echo "   验证：openclaw cron list"
  echo "   手动触发测试：openclaw cron run <id>"
fi
