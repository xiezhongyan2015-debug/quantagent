"""Replay the frozen c5 rule on its frozen scores and compare with the numbers recorded at freezing time.

The competition CLI's `eval` only exposes the DEFAULT_CFG keys; c5 also needs veto_mask and oth_universe, so this
script reads the whole cfg from the frozen rule.json (the same call freeze_version made).

python scripts/c5_replay.py                       # research 2019-2024 and forward 2025-2026-09-16
python scripts/c5_replay.py --period forward
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

FROZEN_C5 = "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/frozen/c5_396_turnlabel_ens5_fdveto2_freecap"
PERIODS = {"research": ("/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel", [2019, 2020, 2021, 2022, 2023, 2024]),
           "forward": ("/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_OOS/p2026/panel", [2025, 2026])}
HEADLINE = ("net_excess_ann", "net_sharpe", "gross_excess_ann", "daily_turnover", "max_drawdown_net")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", choices=sorted(PERIODS), nargs="*", default=sorted(PERIODS, reverse=True))
    a = ap.parse_args()
    rule = json.load(open(os.path.join(FROZEN_C5, "rule.json"), encoding="utf-8"))
    recorded = json.load(open(os.path.join(FROZEN_C5, "replay_results.json"), encoding="utf-8"))
    cfg = dict(cp.DEFAULT_CFG, **rule["cfg"])
    for period in a.period:
        panel_dir, years = PERIODS[period]
        panel = Panel(panel_dir); panel._dtype = np.float32
        scores = pd.read_parquet(os.path.join(FROZEN_C5, f"scores_{period}.parquet")); scores.index = pd.DatetimeIndex(scores.index)
        replay = cp.evaluate_prediction(panel, scores, tuple(years), cfg, with_ic=False)["book"]
        frozen = recorded[period]["book"]
        print(f"== {period} {years[0]}-{years[-1]}")
        for k in HEADLINE:
            print(f"  {k:18s} replay {replay[k]:+.4f}   recorded {frozen[k]:+.4f}")
        print("  annual net excess:", {y: round(v.get("net_excess", np.nan), 4) for y, v in replay.get("annual", {}).items()})
        panel.release()


if __name__ == "__main__":
    main()
