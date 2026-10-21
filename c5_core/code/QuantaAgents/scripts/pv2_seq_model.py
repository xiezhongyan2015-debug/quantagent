"""A15 item 9 (research arm): end-to-end sequence model. The last 60 sessions of eight price/volume channels (each
cross-sectionally rank-normalised per day) feed a small GRU (or 1-D CNN) that predicts the per-date rank of the
5/10-day blended label. All-A training, expanding window per target year, early stopping on the last 10% of the
training dates (never on the test year), year-end purge of 11 signal days. Output: a date x code prediction parquet
in the same format as the tree models (higher = better), one file per seed, per-year checkpoints.

python scripts/pv2_seq_model.py --panel-dir /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/panel --out /Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Research/competition/pv2/combos/pred_pv2_gru_s0.parquet \
    --target-years 2019 2020 2021 2022 2023 2024 --seed 0 [--arch gru|cnn] [--hidden 48] [--epochs 12] [--day-step 4]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from quanta_agents.factor_lab_a.panel import Panel  # noqa: E402
from quanta_agents.factor_lab_a.trading import blended_label  # noqa: E402
from quanta_agents.factor_lab_a.evaluate import rank_ic, annual_table  # noqa: E402

CHANNELS = ("ret", "range", "overnight", "intraday", "vol_rel20", "turnover", "log_amount", "vwap_dev")


def _log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def build_channels(panel) -> np.ndarray:
    """(dates x codes x channels) float32, each channel cross-sectionally rank-normalised per date among eligible names
    (percentile - 0.5), NaN -> 0 (the median)."""
    elig = panel["eligible"] > 0
    o, h, l, c, pc = panel["open"], panel["high"], panel["low"], panel["close"], panel["prev_close"]
    v, amt, to, vw = panel["volume"], panel["amount"], panel["turnover"], panel["vwap"]
    raw = {"ret": c / pc - 1, "range": np.log(h / l), "overnight": o / pc - 1, "intraday": c / o - 1,
           "vol_rel20": np.log(v / v.rolling(20, min_periods=10).mean()), "turnover": to, "log_amount": np.log(amt), "vwap_dev": vw / c - 1}
    out = np.zeros((len(panel.dates), len(panel.codes), len(CHANNELS)), np.float32)
    for k, name in enumerate(CHANNELS):
        f = raw[name].replace([np.inf, -np.inf], np.nan).where(elig)
        r = f.rank(axis=1, pct=True) - 0.5
        out[:, :, k] = np.nan_to_num(r.to_numpy(np.float32), nan=0.0)
        del f, r
    return out


def make_model(arch: str, n_ch: int, hidden: int, seq: int):
    import torch.nn as nn
    if arch == "gru":
        class GRUNet(nn.Module):
            def __init__(self):
                super().__init__()
                self.gru = nn.GRU(n_ch, hidden, num_layers=1, batch_first=True)
                self.head = nn.Sequential(nn.Dropout(0.1), nn.Linear(hidden, 1))
            def forward(self, x):
                _, hN = self.gru(x)
                return self.head(hN[-1]).squeeze(-1)
        return GRUNet()
    class CNN(nn.Module):
        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(nn.Conv1d(n_ch, hidden, 5, padding=2), nn.ReLU(), nn.Conv1d(hidden, hidden, 5, padding=2, dilation=1), nn.ReLU(),
                                     nn.Conv1d(hidden, hidden, 3, padding=2, dilation=2), nn.ReLU(), nn.AdaptiveAvgPool1d(1))
            self.head = nn.Sequential(nn.Dropout(0.1), nn.Linear(hidden, 1))
        def forward(self, x):
            return self.head(self.net(x.transpose(1, 2)).squeeze(-1)).squeeze(-1)
    return CNN()


def gather(X: np.ndarray, di: np.ndarray, ci: np.ndarray, seq: int) -> np.ndarray:
    """Windows ending at date index di (inclusive) for code index ci: (n x seq x channels)."""
    offs = np.arange(-seq + 1, 1)
    return X[di[:, None] + offs[None, :], ci[:, None], :]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel-dir", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--target-years", nargs="*", type=int, default=[2019, 2020, 2021, 2022, 2023, 2024]); ap.add_argument("--train-from", type=int, default=2016)
    ap.add_argument("--label-blend", nargs="*", type=int, default=[5, 10]); ap.add_argument("--seq", type=int, default=60); ap.add_argument("--day-step", type=int, default=4)
    ap.add_argument("--arch", default="gru", choices=["gru", "cnn"]); ap.add_argument("--hidden", type=int, default=48); ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--batch", type=int, default=4096); ap.add_argument("--lr", type=float, default=1e-3); ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--patience", type=int, default=2); ap.add_argument("--threads", type=int, default=16); ap.add_argument("--max-train-samples", type=int, default=2_500_000)
    a = ap.parse_args(argv)
    import torch
    torch.set_num_threads(a.threads); torch.manual_seed(a.seed); np.random.seed(a.seed)
    panel = Panel(a.panel_dir); panel._dtype = np.float32
    dates = panel.dates; codes = panel.codes; years = np.array(dates.year)
    t0 = time.time()
    X = build_channels(panel)
    _log(f"channels {X.shape} ({time.time() - t0:.0f}s)")
    mask = panel.mask("all_a")
    lab = blended_label(panel, horizons=tuple(a.label_blend), purge_last=max(a.label_blend) + 1, mask=mask)
    y_all = (lab.rank(axis=1, pct=True) - 0.5).to_numpy(np.float32)      # NaN where purged / unlabelled
    valid_row = np.isfinite(X[:, :, 0]).all(axis=2) if False else None   # placeholder (channels are NaN-free)
    elig_np = mask.to_numpy(bool)
    # a window needs the name to have been eligible (traded) on at least 50 of its 60 sessions
    elig_cnt = pd.DataFrame(elig_np.astype(np.float32)).rolling(a.seq, min_periods=1).sum().to_numpy()
    ok_win = (elig_cnt >= min(50, a.seq)) & elig_np
    panel.release(keep=("label_5",))
    ckpt_dir = os.path.splitext(a.out)[0] + "_ckpt"; os.makedirs(ckpt_dir, exist_ok=True)
    preds = []; fits = {}
    for Y in a.target_years:
        cp_path = os.path.join(ckpt_dir, f"pred_{Y}.parquet")
        if os.path.exists(cp_path):
            df = pd.read_parquet(cp_path); df.index = pd.DatetimeIndex(df.index); preds.append(df); _log(f"year {Y}: checkpoint reused"); continue
        tr_dates = np.where((years >= a.train_from) & (years < Y))[0]
        tr_dates = tr_dates[tr_dates >= a.seq - 1][::a.day_step]
        tr_dates = tr_dates[np.isfinite(y_all[tr_dates]).any(axis=1)]          # purged / unlabelled dates carry no samples
        n_val = max(1, int(0.1 * len(tr_dates))); va_dates = tr_dates[-n_val:]; tr_dates = tr_dates[:-n_val]
        def samples(dsel):
            di, ci = np.where(ok_win[dsel] & np.isfinite(y_all[dsel]))
            return dsel[di], ci
        tr_d, tr_c = samples(tr_dates); va_d, va_c = samples(va_dates)
        if len(tr_d) > a.max_train_samples:
            keep = np.random.default_rng(a.seed).choice(len(tr_d), a.max_train_samples, replace=False); tr_d, tr_c = tr_d[keep], tr_c[keep]
        _log(f"year {Y}: train {len(tr_d)} samples over {len(tr_dates)} dates, val {len(va_d)} over {len(va_dates)} dates")
        model = make_model(a.arch, len(CHANNELS), a.hidden, a.seq)
        opt = torch.optim.Adam(model.parameters(), lr=a.lr)
        best = (np.inf, None); bad = 0
        for ep in range(a.epochs):
            model.train(); perm = np.random.permutation(len(tr_d)); tl = 0.0; nb = 0
            for s in range(0, len(perm), a.batch):
                idx = perm[s:s + a.batch]
                xb = torch.from_numpy(gather(X, tr_d[idx], tr_c[idx], a.seq)); yb = torch.from_numpy(y_all[tr_d[idx], tr_c[idx]])
                opt.zero_grad(); loss = torch.mean((model(xb) - yb) ** 2); loss.backward(); opt.step()
                tl += float(loss.detach()); nb += 1
            model.eval(); vl = 0.0; vn = 0; ic_num = []
            with torch.no_grad():
                for s in range(0, len(va_d), a.batch * 2):
                    sl = slice(s, s + a.batch * 2)
                    xb = torch.from_numpy(gather(X, va_d[sl], va_c[sl], a.seq)); yb = y_all[va_d[sl], va_c[sl]]
                    p = model(xb).numpy(); vl += float(((p - yb) ** 2).sum()); vn += len(yb)
            vl = vl / vn if vn > 0 else tl / max(nb, 1)                          # no validation samples: fall back to the training loss
            _log(f"  year {Y} epoch {ep + 1}: train mse {tl / max(nb, 1):.5f} val mse {vl:.5f} (val samples {vn}) ({time.time() - t0:.0f}s)")
            if vl < best[0] - 1e-6:
                best = (vl, {k: v.clone() for k, v in model.state_dict().items()}); bad = 0
            else:
                bad += 1
                if bad >= a.patience:
                    break
        model.load_state_dict(best[1]); model.eval()
        te_dates = np.where(years == Y)[0]
        out = np.full((len(te_dates), len(codes)), np.nan, np.float32)
        with torch.no_grad():
            for j, d in enumerate(te_dates):
                ci = np.where(ok_win[d])[0]
                if len(ci) == 0:
                    continue
                for s in range(0, len(ci), a.batch * 2):
                    cc = ci[s:s + a.batch * 2]
                    out[j, cc] = model(torch.from_numpy(gather(X, np.full(len(cc), d), cc, a.seq))).numpy()
        df = pd.DataFrame(out, index=dates[te_dates], columns=codes); df.to_parquet(cp_path); preds.append(df)
        fits[str(Y)] = {"train_samples": int(len(tr_d)), "val_mse": float(best[0]), "epochs_run": ep + 1}
        _log(f"year {Y}: predicted {len(te_dates)} dates ({time.time() - t0:.0f}s)")
    pred = pd.concat(preds).reindex(dates)
    pred.astype(np.float32).to_parquet(a.out)
    ics = {}
    for name in ("all_a", "union", "csi500"):
        try:
            msk = panel.mask(name) & (panel["eval_ok"] > 0)
        except KeyError:
            continue
        r, n = rank_ic(pred, panel["label_5"], msk, min_stocks=50)
        ics[name] = {str(y): v["mean"] for y, v in annual_table(r, tuple(a.target_years)).items()}
        vals = [x for x in ics[name].values() if x is not None]
        _log("  IC %-8s mean %.4f worst %.4f | %s" % (name, np.mean(vals), np.min(vals), " ".join(f"{y}:{x:+.3f}" for y, x in ics[name].items() if x is not None)))
    meta = {"arch": a.arch, "hidden": a.hidden, "seq": a.seq, "channels": CHANNELS, "label_blend": a.label_blend, "day_step": a.day_step, "seed": a.seed, "epochs": a.epochs,
            "panel_dir": a.panel_dir, "target_years": a.target_years, "fits": fits, "annual_rank_ic_by_universe": ics, "out": a.out, "finished": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with open(os.path.splitext(a.out)[0] + ".json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    _log(f"saved {a.out}")


if __name__ == "__main__":
    main()
