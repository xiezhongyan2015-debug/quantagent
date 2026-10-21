"""Select member sets from the training-period (2016-2018) in-bucket evaluation (no exposure to 2019+).
python scripts/competition_select_members.py --eval /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/eval/member_eval_500_train.csv --out-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/members
Rules (all on 2016-2018, inside CSI500 unless noted):
  ic_t3      : |t| of daily RankIC inside CSI500 >= 3 and coverage >= 0.8
  ic_t2_long : t >= 2 and top-decile excess inside CSI500 > 0 (long side usable)
  union_t3   : t >= 3 inside the union
  long_top   : top-decile excess inside CSI500 > +5% annualised and t >= 1.5 (long-side specialists)
  short_bot  : bottom-decile excess inside CSI500 < -8% annualised and t >= 1.5 (short-side specialists, for the exclusion model)
Each set is written as a members json ({"members": [...]}) with a summary line.
"""
import argparse, json, os
import pandas as pd


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--eval", required=True); ap.add_argument("--out-dir", required=True); ap.add_argument("--min-cov", type=float, default=0.8)
    a = ap.parse_args()
    df = pd.read_csv(a.eval)
    os.makedirs(a.out_dir, exist_ok=True)
    cov = df["csi500_coverage"] >= a.min_cov
    sets = {
        "ic_t3": df[cov & (df["csi500_ic_t"] >= 3)],
        "ic_t2_long": df[cov & (df["csi500_ic_t"] >= 2) & (df["csi500_top_mean"] > 0)],
        "union_t3": df[(df["union_coverage"] >= a.min_cov) & (df["union_ic_t"] >= 3)],
        "long_top": df[cov & (df["csi500_top_mean"] > 0.05) & (df["csi500_ic_t"] >= 1.5)],
        "short_bot": df[cov & (df["csi500_bot_mean"] < -0.08) & (df["csi500_ic_t"] >= 1.5)],
        "ic_t2": df[cov & (df["csi500_ic_t"] >= 2)],
    }
    for name, g in sets.items():
        ids = g["id"].tolist()
        with open(os.path.join(a.out_dir, f"members_{name}.json"), "w", encoding="utf-8") as fh:
            json.dump({"tag": name, "rule": "training-period 2016-2018 in-bucket statistics", "n": len(ids), "members": ids}, fh, ensure_ascii=False, indent=1)
        print("%-12s n=%3d | train 500 IC mean %.4f | top %+.1f%% bot %+.1f%% | sources %s" % (name, len(ids), g["csi500_ic_mean"].mean(), g["csi500_top_mean"].mean() * 100, g["csi500_bot_mean"].mean() * 100,
                                                                                                dict(g["source"].value_counts().head(6))))


if __name__ == "__main__":
    main()
