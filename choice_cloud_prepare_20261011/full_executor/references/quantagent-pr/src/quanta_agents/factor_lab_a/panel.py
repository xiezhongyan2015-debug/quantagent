"""Wide date x stock panel built from the frozen parquet source.

Never loads 2025 numeric values (project rule). Cached as float32 parquet.
"""
from __future__ import annotations
import glob
import json
import os
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

RAW_FIELDS = ["open", "high", "low", "close", "volume", "amount", "vwap_qfq", "prev_close",
              "float_shares", "total_shares", "float_market_cap", "total_market_cap"]
BOOL_FIELDS = ["is_st", "is_delisting"]
DERIVED = ["turnover", "ret", "log_cap", "log_total_cap", "mkt_ret", "listed_days"]
LABELS = {"label_1": (2, 1), "label_5": (6, 1), "label_10": (11, 1), "label_20": (21, 1)}
# label_k = open[t+1+k] / open[t+1] - 1 (enter next open, exit k sessions later at open)


class Panel:
    def __init__(self, directory: str):
        self.dir = directory
        self._cache: dict[str, pd.DataFrame] = {}
        with open(os.path.join(directory, "meta.json"), encoding="utf-8") as fh:
            self.meta = json.load(fh)
        self.dates = pd.DatetimeIndex(self.meta["dates"])
        self.codes = list(self.meta["codes"])

    def __getitem__(self, name: str) -> pd.DataFrame:
        if name not in self._cache:
            path = os.path.join(self.dir, f"{name}.parquet")
            if not os.path.exists(path):
                raise KeyError(f"unknown panel field: {name}")
            df = pd.read_parquet(path)
            df.index = pd.DatetimeIndex(df.index)
            self._cache[name] = df.astype(getattr(self, "_dtype", np.float64))
        return self._cache[name]

    def has(self, name: str) -> bool:
        return os.path.exists(os.path.join(self.dir, f"{name}.parquet"))

    def fields(self) -> list[str]:
        return sorted(f[:-8] for f in os.listdir(self.dir) if f.endswith(".parquet"))

    def mask(self, universe: str) -> pd.DataFrame:
        base = self["eligible"] > 0
        if universe == "all_a":
            return base
        if universe == "union":  # competition universe: CSI300 U CSI500 U CSI1000 (point-in-time membership fields)
            return base & ((self["member_csi300"] > 0) | (self["member_csi500"] > 0) | (self["member_csi1000"] > 0))
        return base & (self[f"member_{universe}"] > 0)

    def release(self, keep: tuple[str, ...] = ()):
        for k in list(self._cache):
            if k not in keep:
                del self._cache[k]


def _save(directory: str, name: str, df: pd.DataFrame):
    df.astype(np.float32).to_parquet(os.path.join(directory, f"{name}.parquet"))


def build_panel(source_root: str, out_dir: str, instruments_dir: str, start="2015-01-01", end="2024-12-31",
                min_listed_days=120) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    cols = ["date", "code"] + RAW_FIELDS + BOOL_FIELDS
    frames = []
    for f in sorted(glob.glob(os.path.join(source_root, "*.parquet"))):
        t = pq.read_table(f, columns=cols).to_pandas()
        t = t[(t["date"] >= start) & (t["date"] <= end)]
        if len(t):
            frames.append(t)
    df = pd.concat(frames, ignore_index=True)
    del frames
    df["code"] = df["code"].str.upper()
    df = df.sort_values(["code", "date"])
    df["listed_days"] = df.groupby("code").cumcount() + 1
    fields = {}
    for name in RAW_FIELDS + ["listed_days"]:
        fields[name] = df.pivot(index="date", columns="code", values=name)
    for name in BOOL_FIELDS:
        fields[name] = df.pivot(index="date", columns="code", values=name).astype(float)
    dates = fields["close"].index
    codes = list(fields["close"].columns)
    # derived
    fields["turnover"] = fields["volume"] / fields["float_shares"]
    fields["ret"] = fields["close"] / fields["prev_close"] - 1
    fields["log_cap"] = np.log(fields["float_market_cap"])
    fields["log_total_cap"] = np.log(fields["total_market_cap"])
    fields["vwap"] = fields.pop("vwap_qfq")
    eligible = ((fields["is_st"] != 1) & (fields["is_delisting"] != 1) & (fields["amount"] > 0)
                & (fields["listed_days"] >= min_listed_days) & fields["close"].notna()).astype(float)
    fields["eligible"] = eligible
    mkt = fields["ret"].where(eligible > 0).mean(axis=1)
    fields["mkt_ret"] = pd.DataFrame(np.repeat(mkt.values[:, None], len(codes), axis=1), index=dates, columns=codes)
    for name, (exit_shift, enter_shift) in LABELS.items():
        fields[name] = fields["open"].shift(-exit_shift) / fields["open"].shift(-enter_shift) - 1
    # index memberships (point in time)
    for uni in ("csi300", "csi500", "csi1000"):
        path = os.path.join(instruments_dir, f"{uni}.txt")
        m = pd.read_csv(path, sep="\t", header=None, names=["code", "start", "end"])
        m["start"] = pd.to_datetime(m["start"]); m["end"] = pd.to_datetime(m["end"])
        member = pd.DataFrame(0.0, index=dates, columns=codes)
        for code, g in m.groupby("code"):
            if code not in member.columns:
                continue
            col = np.zeros(len(dates), bool)
            for a, b in zip(g["start"], g["end"]):
                col |= (dates >= a) & (dates <= b)
            member[code] = col.astype(float)
        fields[f"member_{uni}"] = member
    # year-end purge mask: last 6 signal dates of each calendar year are not evaluated
    year = pd.Series(dates.year, index=dates)
    purge = pd.Series(False, index=dates)
    for y, idx in year.groupby(year).groups.items():
        purge.loc[idx[-6:]] = True
    fields["eval_ok"] = pd.DataFrame(np.repeat((~purge).values[:, None].astype(float), len(codes), axis=1),
                                     index=dates, columns=codes)
    for name, frame in fields.items():
        _save(out_dir, name, frame)
    meta = {"source_root": source_root, "start": start, "end": end, "dates": [d.strftime("%Y-%m-%d") for d in dates],
            "codes": codes, "fields": sorted(fields), "min_listed_days": min_listed_days,
            "label_definition": "label_k = open[t+1+k]/open[t+1]-1", "purge": "last 6 signal dates per calendar year"}
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False)
    return {"dates": len(dates), "codes": len(codes), "fields": sorted(fields)}
