"""c5 daily state export for the teammate (500-bucket exclusion layer).

For every scored session on the live panel from --start on, writes one row per union stock:
  date, code (600000.SH), score_pct, state, bucket, bucket_pct, raw_score, model_version
state is defined on CSI500 members only (vetoed / excluded / overweight / core), the same way live.build_target
labels the c5 target file: exclusion / overweight states replayed over the last `state_window` sessions with
hysteresis, the fundamental veto taking precedence.  Other stocks get an empty state.

python scripts/c5_states_export.py --start 2026-09-29 --out /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Live/c5_states
  -> <out>/c5_state_<yyyymmdd>.parquet / .csv per date, and <out>/c5_states_all.parquet (merged, one row per date x code)
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a import competition as cp  # noqa: E402
from quanta_agents.factor_lab_a.panel import Panel  # noqa: E402
from quanta_agents.factor_lab_a.trading import trading_mask  # noqa: E402

LIVE = "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Live"
FROZEN_C5 = "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/frozen/c5_396_turnlabel_ens5_fdveto2_freecap"
MODEL_VERSION = "live_final_seed0_labels_to_2026-04-22"


def in_bucket_pct(score_row, member_row):
    """Percentile of the score among the bucket members that have one (tilted_bucket_book._pct)."""
    pct = np.full(score_row.shape, np.nan)
    idx = np.where(np.isfinite(score_row) & member_row)[0]
    if len(idx):
        order = np.argsort(score_row[idx], kind="stable")
        ranks = np.empty(len(idx)); ranks[order] = (np.arange(len(idx)) + 1) / len(idx)
        pct[idx] = ranks
    return pct


def replay_states(bucket_score, member, t_from, t_to, rule):
    """Exclusion / overweight states with hysteresis, cold start at row t_from, returned at row t_to."""
    excluded = np.zeros(bucket_score.shape[1], bool); overweight = np.zeros(bucket_score.shape[1], bool)
    for t in range(t_from, t_to + 1):
        pct = in_bucket_pct(bucket_score[t], member[t]); has = np.isfinite(pct)
        excluded = np.where(has, np.where(excluded, pct <= rule["x_in"], pct < rule["x_out"]), excluded) & member[t]
        overweight = np.where(has, np.where(overweight, pct >= rule["y_out"], pct >= rule["y_in"]), overweight) & member[t]
    return excluded, overweight


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="first date to export (YYYY-MM-DD)")
    ap.add_argument("--out", default=LIVE + "/c5_states")
    ap.add_argument("--panel-dir", default=LIVE + "/panel")
    ap.add_argument("--prediction", default=LIVE + "/pred_live_turnlabel.parquet")
    ap.add_argument("--veto-mask", default=LIVE + "/fd_veto_distress10_v2.parquet")
    ap.add_argument("--state-window", type=int, default=250)
    a = ap.parse_args()
    rule = json.load(open(FROZEN_C5 + "/rule.json", encoding="utf-8"))["cfg"]

    panel = Panel(a.panel_dir); panel._dtype = np.float32
    raw_score = pd.read_parquet(a.prediction); raw_score.index = pd.DatetimeIndex(raw_score.index)
    raw_score = raw_score.reindex(index=panel.dates, columns=panel.codes)
    member, _ = cp.membership(panel)
    tradable = trading_mask(panel)
    in_500 = tradable & member["csi500"]
    in_other = tradable & member["union"] & ~member["csi500"] & member[rule["oth_universe"]]
    in_union = tradable & member["union"]
    score_pct = cp.smooth_scores(raw_score, in_union, rule["smooth"])
    bucket_pct = cp.neutral_scores(panel, score_pct, in_500, in_other, rule["neutral_field"], rule["neutral_q"])
    veto = pd.read_parquet(a.veto_mask); veto.index = pd.DatetimeIndex(veto.index)
    veto = veto.reindex(index=panel.dates, columns=panel.codes).ffill(limit=10).fillna(0).to_numpy() != 0

    bucket_np = bucket_pct.to_numpy(np.float32); member_500 = in_500.to_numpy(bool)
    codes = np.array([c[2:] + "." + c[:2] for c in panel.codes])
    os.makedirs(a.out, exist_ok=True)
    days = []
    for t, d in enumerate(panel.dates):
        if d < pd.Timestamp(a.start) or not np.isfinite(raw_score.iloc[t].to_numpy()).any():
            continue
        excluded, overweight = replay_states(bucket_np, member_500, max(0, t - a.state_window + 1), t, rule)
        state = np.where(veto[t], "vetoed", np.where(excluded, "excluded", np.where(overweight, "overweight", "core")))
        state = np.where(member_500[t], state, "")
        bucket = np.where(member_500[t], "csi500", np.where(in_other.iloc[t].to_numpy(bool), "other_csi1000", "csi300_only"))
        keep = in_union.iloc[t].to_numpy(bool) & np.isfinite(score_pct.iloc[t].to_numpy())
        day = pd.DataFrame({"date": d.strftime("%Y-%m-%d"), "code": codes[keep], "score_pct": score_pct.iloc[t].to_numpy()[keep].round(6),
                            "state": state[keep], "bucket": bucket[keep], "bucket_pct": bucket_pct.iloc[t].to_numpy()[keep].round(6),
                            "raw_score": raw_score.iloc[t].to_numpy()[keep], "model_version": MODEL_VERSION})
        stem = os.path.join(a.out, "c5_state_" + d.strftime("%Y%m%d"))
        day.to_parquet(stem + ".parquet", index=False); day.to_csv(stem + ".csv", index=False, float_format="%.6g")
        days.append(day)
        counts = day[day.bucket == "csi500"]["state"].value_counts().to_dict()
        print(d.strftime("%Y-%m-%d"), len(day), "names; csi500 states", counts, flush=True)

    merged_path = os.path.join(a.out, "c5_states_all.parquet")
    merged = pd.concat(([pd.read_parquet(merged_path)] if os.path.exists(merged_path) else []) + days, ignore_index=True)
    merged = merged.drop_duplicates(subset=["date", "code"], keep="last").sort_values(["date", "code"])
    merged.to_parquet(merged_path, index=False)
    print("merged", merged["date"].min(), "->", merged["date"].max(), len(merged), "rows")


if __name__ == "__main__":
    main()
