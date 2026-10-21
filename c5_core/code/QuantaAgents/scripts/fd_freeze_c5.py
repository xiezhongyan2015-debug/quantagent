"""Freeze candidate c5 (A18): c4 scores (turnover-neutral-label 5-seed ensemble) + c4 rule + fundamental distress veto v2 in
the CSI500 bucket (+ optional free-float cap weights).  Mirrors scripts/pv4_freeze_c4.py.

python scripts/fd_freeze_c5.py --rule c5_free --name c5_396_turnlabel_ens5_fdveto2_freecap
"""
import argparse, json, os, shutil, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a import competition as cp  # noqa: E402

R = "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition"; PV = R + "/pv4"; E = R + "/fd/eval"
ap = argparse.ArgumentParser(); ap.add_argument("--rule", required=True); ap.add_argument("--name", required=True); ap.add_argument("--note", default="")
a = ap.parse_args()
out = R + "/frozen/" + a.name
os.makedirs(out, exist_ok=True)
rules_r = json.load(open(E + "/rules_c5.json", encoding="utf-8")); rules_f = json.load(open(E + "/rules_c5_forward.json", encoding="utf-8"))
rule = dict(rules_r[a.rule])
if rule.get("veto_mask") and a.rule in rules_f and rules_f[a.rule].get("veto_mask"):
    rule["veto_mask"] = rules_f[a.rule]["veto_mask"]     # mask built on the full 2015-2026 panel covers both replay periods
# copy the frozen inputs next to the rule so the directory is self-contained
for key, src in [("veto_mask", rule.get("veto_mask")), ("cap_mult", rule.get("cap_mult"))]:
    if src:
        dst = os.path.join(out, os.path.basename(src)); shutil.copyfile(src, dst); rule[key] = dst
fwd = dict(rules_f[a.rule]) if a.rule in rules_f else None
note = ("A18 candidate. Scores = c4's rank-average of 5 seeds (LightGBM, 396 members, turnover-neutral 5/10/20-day label), unchanged. Rule = c4 "
        "(85/13/2, other bucket CSI1000-only top-110 keep 4x, exclusion 20%/35% on turnover-neutral percentiles) + additional veto of the worst 10% "
        "CSI500 names on a rule-based fundamental distress composite (cash-flow accruals, non-standard audit, shareholder-count growth, insider net "
        "selling 60d, pledge ratio, single-quarter revenue growth, SUE x low turnover; direction fixed on 2016-2018, PIT with availability < signal date)"
        + (", CSI500 bucket weighted by vendor free-float cap (free_share x price) instead of float cap x implied multiplier" if rule.get("cap_field") == "fd_free_cap" else "")
        + ". The composite was chosen after the 2019-2024 in-bucket single-factor table (the pre-registered v1 composite had no effect); 2025/2026 "
          "were looked at once, after the rule was fixed. " + a.note)
shadow = {"c4_reference": rules_r["c4"], "lots_2m_price_neutral_rounding": dict(rule, lot_nav=2e6, lot_min_trade=0.001, lot_group_below=True)}
preds = {"research": PV + "/combos/ens_turnlabel_s01234.parquet", "forward": PV + "/combos/ens_turnlabel_2026_s01234.parquet"}
panels = {"research": "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel", "forward": "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026/panel"}
years = {"research": [2019, 2020, 2021, 2022, 2023, 2024], "forward": [2025, 2026]}
if fwd:
    json.dump(fwd, open(os.path.join(out, "rule_forward.json"), "w", encoding="utf-8"), indent=1)
r = cp.freeze_version(out, rule, preds, panels, years, note=note, shadow_cfgs=shadow)
print(r); print("FREEZE_C5_DONE")
