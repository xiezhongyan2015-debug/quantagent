# Rebuild the live grid on the cloud, assemble, and compare the veto mask with today's chain output (temp file only).
set -uo pipefail
cd /c/ChoiceBridge/c5_stage_20261012/payload/c5_core/code/QuantaAgents; export PYTHONPATH=src PYTHONUTF8=1
PY=/c/ChoiceBridge/runtime/c5-venv/Scripts/python.exe; LIVE=C:/ChoiceBridge/c5_stage_20261012/payload/c5_core/data/A_Layer_Live; G=$LIVE/fundamental_grid
for part in income balancesheet cashflow fina_indicator ratios events analyst daily; do $PY scripts/fd_features.py build --parts $part --grid-dir $G --grid-panels live 2>&1 | tail -n 1 || echo "BUILD FAILED $part"; done
$PY scripts/fd_features.py assemble --panels live --grid-dir $G --ffill-limit 15 2>&1 | tail -n 1
VEXPR="cs_rank(-fd_accrual_cf) + cs_rank(-fd_audit_nonstd) + cs_rank(-clip(fd_hnum_chg, -1, 3)) + cs_rank(-fd_hsell_60) + cs_rank(-fd_pledge) + cs_rank(clip(fd_rev_q_yoy, -3, 3)) + cs_rank(cs_rank(clip(fd_sue, -5, 5)) * cs_rank(-rolling_mean(turnover, 20)))"
$PY scripts/fd_arm_inputs.py veto --panel-dir $LIVE/panel --direction 1 --q 0.10 --out C:/ChoiceBridge/staging/veto_check.parquet --expr "$VEXPR" 2>&1 | tail -n 1
$PY - <<'PYEOF'
import pandas as pd, json
L='C:/ChoiceBridge/c5_stage_20261012/payload/c5_core/data/A_Layer_Live'
old=pd.read_parquet(L+'/fd_veto_distress10_v2.parquet'); new=pd.read_parquet('C:/ChoiceBridge/staging/veto_check.parquet')
d=new.index[-1]; o=old.loc[d] if d in old.index else None
print('last date', d, 'vetoed new', int(new.loc[d].sum()), 'vetoed old', int(o.sum()) if o is not None else None, 'overlap', int(((new.loc[d]>0)&(o>0)).sum()) if o is not None else None)
g=json.load(open(L+'/fundamental_grid/grid.json')); print('live grid last date', g['dates'][-1])
fs=pd.read_parquet(L+'/panel/fd_free_share.parquet'); print('free_share non-null on last 3 days', [int(fs.iloc[i].notna().sum()) for i in (-3,-2,-1)], fs.index[-1])
for f in ('fd_accrual_cf','fd_pledge','fd_hnum_chg','fd_hsell_60','fd_sue'):
    x=pd.read_parquet(f'{L}/panel/{f}.parquet'); print(f, int(x.iloc[-1].notna().sum()))
PYEOF
