"""Freeze candidate c4 (A17): turnover-neutral-label 5-seed scores + c3-A rule + implied index weights + CSI1000-only other bucket."""
import json, os, shutil, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a import competition as cp
R = "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition"; PV = R + "/pv4"
out = R + "/frozen/c4_396_turnlabel_ens5_iw_o1000_85_13_n110"
os.makedirs(out, exist_ok=True)
cm = out + "/index_weight_mult_a1.parquet"
if not os.path.exists(cm):
    shutil.copyfile(PV + "/indexw/index_weight_mult_a1.parquet", cm)
c3a = json.load(open(PV + "/eval/core_rules.json", encoding="utf-8"))["c3A"]
rule = dict(c3a, cap_mult=cm, oth_universe="csi1000")
shadow = {"lots_2m_price_neutral_rounding": dict(rule, lot_nav=2e6, lot_min_trade=0.001, lot_group_below=True), "lots_5m_price_neutral_rounding": dict(rule, lot_nav=5e6, lot_min_trade=0.001, lot_group_below=True),
          "exclude_10pct": dict(rule, x_out=0.10, x_in=0.25), "c3A_reference": c3a}
note = ("A17 candidate. Scores = rank-average of 5 seeds (LightGBM, 396 members, turnover-neutral 5/10/20-day label). Rule = c3-A (85/13/2, other bucket top-110 keep 4x, exclusion 20%/35% on turnover-neutral "
        "percentiles) with two structural changes selected on 2019-2024 only: implied CSI500 index weights (ridge, 500-day window, alpha 1, prices only) and the other bucket drawn from CSI1000 only. "
        "Forward years were used to DIAGNOSE the float-cap weight mismatch and the high-priced-stock rounding loss, so 2025/2026 are not a clean confirmation of those two fixes.")
r = cp.freeze_version(out, rule, {"research": PV + "/combos/ens_turnlabel_s01234.parquet", "forward": PV + "/combos/ens_turnlabel_2026_s01234.parquet"},
                      {"research": "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel", "forward": "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026/panel"}, {"research": [2019, 2020, 2021, 2022, 2023, 2024], "forward": [2025, 2026]}, note=note, shadow_cfgs=shadow)
print(r); print("FREEZE_C4_DONE")
