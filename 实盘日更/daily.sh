#!/usr/bin/env bash
# 每日一条命令：生成 T+1 的目标文件。T = 信号日（今天的交易日）。
#   实盘日更/daily.sh T [--mode P0|V5|V5_tilt] [--c5 文件] [--account 快照.json] [--skip-gru] [--shadow] [--switch-day] [--beta-tilt 记录目录 [--beta-cache 缓存目录]] [--ens ctrl5|all15] [--refill prorata|industry]
#   --ens all15 --refill industry（2026-10-19 用户批准切换）：实盘信号用十五模型集成的混合信号，c5 剔除的权重按申万一级同行业回填；all15 文件缺失时退回五种子并告警
#   --beta-tilt（2026-10-19 起云机实盘）：放行后按 S7 做 beta 倾斜，倾斜目标过不了闸门或出错就保留未倾斜目标（timing_research_20261017/beta_tilt_live.py）
# 顺序：PCF(T) → 估值日收盘价 → GRU 链(新浪分钟→信号) → 与 c5 混合 → live_targets 闸门 → 目标文件 → 日志
set -u
cd "$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
PY=${PY:-.pcf-research-venv/bin/python}; L=实盘日更; T=${1:?用法: daily.sh T(YYYY-MM-DD) [--mode ..] [--c5 ..] [--account ..] [--skip-gru] [--shadow] [--switch-day]}; shift
MODE=V5_tilt; C5=""; ACCOUNT=""; SKIP_GRU=0; SHADOW=""; MAXTURN=0.40; BETA_OUT=""; BETA_CACHE=""; ENS=ctrl5; REFILL=prorata
while [ $# -gt 0 ]; do case "$1" in
  --mode) MODE=$2; shift 2;; --c5) C5=$2; shift 2;; --account) ACCOUNT=$2; shift 2;; --skip-gru) SKIP_GRU=1; shift;; --shadow) SHADOW="--shadow"; shift;; --switch-day) MAXTURN=0.70; shift;; --beta-tilt) BETA_OUT=$2; shift 2;; --beta-cache) BETA_CACHE=$2; shift 2;; --ens) ENS=$2; shift 2;; --refill) REFILL=$2; shift 2;; *) echo "未知参数 $1"; exit 1;; esac; done
T8=${T//-/}; NEXT=$($PY -c "import json,sys; c=json.load(open('$L/config/calendar_2026.json')); l=[d for d in c if d>'$T']; print(l[0] if l else '')")
[ -z "$NEXT" ] && { echo "日历里没有 $T 之后的交易日"; exit 1; }
OUT=$L/outputs/targets/$NEXT; mkdir -p $OUT $L/outputs/logs
if [ -f $OUT/target_$NEXT.json ]; then B=$OUT/superseded_$(date +%H%M%S); mkdir -p $B; mv $OUT/target_$NEXT.* $OUT/precheck_$NEXT.json $OUT/rejected_$NEXT.json $B/ 2>/dev/null; echo "已有 $NEXT 的目标文件，旧版移到 $B（重跑会覆盖，发布前以日志为准）"; fi; LOG=$L/outputs/logs/daily_$T.log; exec > >(tee -a $LOG) 2>&1
echo "===== $(date '+%F %T') 信号日 $T → 执行日 $NEXT 模式 $MODE"
# 1. PCF
PCF=$L/inputs/pcf/563030_$T8.xml
if [ ! -f $PCF ]; then $PY -I $L/tools/fetch_pcf.py $T || { echo "PCF 下载失败，停止（手册：无 PCF 当日不换目标）"; exit 2; }; fi
VAL=$($PY -c "import xml.etree.ElementTree as ET; r=ET.parse('$PCF').getroot(); d=[x.text for x in r if x.tag=='PreTradingDay'][0]; print(f'{d[:4]}-{d[4:6]}-{d[6:]}')")
# 2. 估值日收盘价
PRICES=$L/inputs/prices/close_${VAL//-/}.csv
# 2026-10-19：骨架 v5 回填需要零股数行的价格；旧版 make_prices 生成的文件没有这些行，所以 v5 开着时每次都重新取价（不再沿用已有文件）
if [ ! -f $PRICES ] || [ -f $L/config/skeleton_v5.json ]; then $PY -I $L/tools/make_prices.py $PCF || { echo "估值日收盘价不全，停止"; exit 2; }; fi
# 3. c5 文件（默认按日期在 inputs/c5 找）
[ -z "$C5" ] && for f in $L/inputs/c5/c5_state_$T8.parquet $L/inputs/c5/c5_state_$T8.csv; do [ -f $f ] && C5=$f; done
[ -z "$C5" ] && echo "警告：没有 $T 的 c5 文件，V5 层将按 --fallback 退回 P0"
# 4. GRU 链 → 信号 → 混合
SIG=""; SIG5=""
if [[ "$MODE" == *tilt* ]]; then
  if [ $SKIP_GRU -eq 0 ]; then gru_live_extend_20261012/run_chain.sh $T "$C5" || echo "警告：GRU 链失败，倾斜层将按 --fallback 退回"; fi
  if [ -n "$C5" ] && [ -f outputs/gru_live_extend_20261012/signals/z_${T}_slot15.csv ]; then $PY gru_live_extend_20261012/blend_signal.py $T "$C5" && SIG=outputs/gru_live_extend_20261012/signals/blend_$T.csv; fi
  [ -z "$SIG" ] && [ -f outputs/gru_live_extend_20261012/signals/z_${T}_slot15.csv ] && SIG=outputs/gru_live_extend_20261012/signals/z_${T}_slot15.csv && echo "注意：无 c5，倾斜只用 GRU z"
  SIG5=$SIG
  if [ "$ENS" = all15 ]; then A15=outputs/gru_live_extend_20261012/signals/blend_${T}_slot15_all15.csv
    if [ -n "$C5" ] && [ -f $A15 ]; then SIG=$A15; else echo "警告：没有十五模型混合信号 $A15，实盘退回五种子 ${SIG##*/}"; fi; fi
  [ -n "$SIG" ] && cp $SIG $L/outputs/signals/
fi
# 5. 账户快照（默认 inputs/account/snapshot_T.json）
[ -z "$ACCOUNT" ] && [ -f $L/inputs/account/snapshot_$T.json ] && ACCOUNT=$L/inputs/account/snapshot_$T.json
[ -z "$ACCOUNT" ] && [ -z "$SHADOW" ] && { echo "没有 $T 收盘后的账户快照：只能出影子文件。加 --shadow 继续，或先用 tools/make_account.py 转换云机快照"; exit 2; }
# 6. 闸门 + 目标文件
# beta 倾斜开着时，持仓本身带着倾斜：未倾斜目标对它的换手会多出约 15%，所以未倾斜这一遍的换手闸门放宽 0.20；
# 最终发布的倾斜目标仍按 $MAXTURN 检查（beta_tilt_live.py --final-max-turnover）。
UTURN=$MAXTURN; [ -n "$BETA_OUT" ] && [ -z "$SHADOW" ] && UTURN=$($PY -c "print(min(0.9, $MAXTURN + 0.20))")
ARGS=(--pcf $PCF --prices $PRICES --prices-date $VAL --calendar $L/config/calendar_2026.json --console-config $L/config/console_config_target_file.json --members $L/config/index_membership_20260917.parquet --signal-date $T --exec-date $NEXT --out $OUT --mode $MODE --fallback --deadband-lots 1 --deadband-cny 10000 --max-turnover $UTURN)
[ "$REFILL" = industry ] && ARGS+=(--refill industry --industry-map $L/config/industry_sw_l1_20260924.json)
# 2026-10-19 用户指令：骨架 v5 = K1 零股数行回填 + √b 加性倾斜；开关和 λ 在 $L/config/skeleton_v5.json（删掉该文件或把 enabled 改 false 即回到旧口径）
V5CFG=$L/config/skeleton_v5.json
if [ -f $V5CFG ]; then V5ARGS=$($PY -c "import json; c=json.load(open('$V5CFG',encoding='utf-8')); a=[]; e=c.get('enabled',False); a+=(['--backfill-holdings',c['backfill_holdings']] if e and c.get('backfill') else []); a+=(['--tilt-form','sqrtb','--tilt-lambda',str(c['tilt_lambda'])] if e and c.get('tilt_form')=='sqrtb' else []); print(' '.join(a))") || { echo "skeleton_v5.json 读取失败，停止"; exit 2; }
  [ -n "$V5ARGS" ] && { ARGS+=($V5ARGS); echo "骨架 v5：$V5ARGS"; }; fi
[ -n "$ACCOUNT" ] && ARGS+=(--lots)   # integer shares need the NAV; shadow runs without a snapshot stay in weights mode
[ -n "$ACCOUNT" ] && ARGS+=(--account $ACCOUNT); [ -n "$SHADOW" ] && ARGS+=($SHADOW); [ -n "$C5" ] && ARGS+=(--c5 $C5); [ -n "$SIG" ] && ARGS+=(--signal $SIG)
$PY pcf_overlay_20261012/live_targets.py "${ARGS[@]}"; RC=$?
# ---- 实盘 beta 倾斜（2026-10-19）：只在未倾斜目标已放行、有账户快照时；永远不改 RC，失败即保留未倾斜目标
if [ $RC -eq 0 ] && [ -n "$BETA_OUT" ] && [ -n "$ACCOUNT" ] && [ -z "$SHADOW" ]; then
  $PY timing_research_20261017/beta_tilt_live.py $T $NEXT $OUT --out "$BETA_OUT" ${BETA_CACHE:+--cache "$BETA_CACHE"} --final-max-turnover $MAXTURN -- "${ARGS[@]}" 2>&1 | tail -2 || echo "beta 倾斜步骤异常，保留未倾斜目标"
fi
# ---- shadow targets (2026-10-15): all15 ensemble + industry refill, never released; failures do not affect RC
if [ -n "$C5" ] && [ -f outputs/gru_live_extend_20261012/signals/blend_${T}_slot15_all15.csv ]; then
  # all15_industry = 10-19 的实盘口径（无 K1 回填、乘法倾斜），10-20 起留作骨架 v5 的对照；ctrl5_prorata = 10-19 切换前的口径
  SH=$L/outputs/shadow/$NEXT; mkdir -p $SH/all15_industry $SH/ctrl5_industry $SH/ctrl5_prorata
  SARGS0=(--pcf $PCF --prices $PRICES --prices-date $VAL --calendar $L/config/calendar_2026.json --console-config $L/config/console_config_target_file.json --members $L/config/index_membership_20260917.parquet --signal-date $T --exec-date $NEXT --mode V5_tilt --fallback --deadband-lots 1 --deadband-cny 10000 --max-turnover $UTURN --c5 $C5)
  [ -n "$ACCOUNT" ] && SARGS0+=(--lots --account $ACCOUNT); [ -n "$SHADOW" ] && SARGS0+=($SHADOW)
  SARGS=("${SARGS0[@]}" --refill industry --industry-map $L/config/industry_sw_l1_20260924.json)
  $PY pcf_overlay_20261012/live_targets.py "${SARGS[@]}" --signal outputs/gru_live_extend_20261012/signals/blend_${T}_slot15_all15.csv --out $SH/all15_industry > $SH/all15_industry/stdout.txt 2>&1 || echo "shadow all15_industry rejected (see $SH/all15_industry)"
  [ -n "$SIG5" ] && { $PY pcf_overlay_20261012/live_targets.py "${SARGS[@]}" --signal $SIG5 --out $SH/ctrl5_industry > $SH/ctrl5_industry/stdout.txt 2>&1 || echo "shadow ctrl5_industry rejected"; }
  [ -n "$SIG5" ] && { $PY pcf_overlay_20261012/live_targets.py "${SARGS0[@]}" --signal $SIG5 --out $SH/ctrl5_prorata > $SH/ctrl5_prorata/stdout.txt 2>&1 || echo "shadow ctrl5_prorata rejected"; }
  $PY $L/tools/shadow_compare.py $NEXT || true
fi
$PY $L/tools/log_day.py $OUT/precheck_$NEXT.json "c5=${C5##*/} sig=${SIG##*/} refill=$REFILL acct=${ACCOUNT##*/}"
if [ $RC -eq 0 ]; then echo "✅ 目标文件: $OUT/target_$NEXT.json  SHA256 $( (shasum -a 256 $OUT/target_$NEXT.json 2>/dev/null || sha256sum $OUT/target_$NEXT.json) | cut -c1-16)  → 上传云机 staging"; else echo "❌ 被闸门拒绝，见 $OUT/rejected_$NEXT.json；按手册沿用前一日目标或人工处理"; fi
exit $RC
