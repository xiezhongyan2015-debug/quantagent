"""Append-only candidate ledger, results, signatures, pool frames and state."""
from __future__ import annotations
import json
import os
import time
import numpy as np
import pandas as pd


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Store:
    def __init__(self, root: str):
        self.root = root
        for d in ("results", "signatures", "pool", "pool_ranks", "requests", "decisions", "batches"):
            os.makedirs(os.path.join(root, d), exist_ok=True)
        self.cand_path = os.path.join(root, "candidates.jsonl")
        self.state_path = os.path.join(root, "state.json")
        self.protocol_path = os.path.join(root, "protocol.json")

    # ---------- protocol / state ----------
    def protocol(self) -> dict:
        with open(self.protocol_path, encoding="utf-8") as fh:
            return json.load(fh)

    def state(self) -> dict:
        if not os.path.exists(self.state_path):
            return {"batch": 0, "created": _now()}
        with open(self.state_path, encoding="utf-8") as fh:
            return json.load(fh)

    def save_state(self, state: dict):
        state["updated"] = _now()
        with open(self.state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)

    # ---------- candidates ----------
    def candidates(self) -> list[dict]:
        if not os.path.exists(self.cand_path):
            return []
        with open(self.cand_path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def by_canonical(self) -> dict[str, dict]:
        return {c["canonical"]: c for c in self.candidates()}

    def add_candidate(self, record: dict) -> bool:
        existing = self.by_canonical()
        if record["canonical"] in existing:
            return False
        record.setdefault("created", _now())
        with open(self.cand_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True

    # ---------- results ----------
    def result_path(self, cid: str) -> str:
        return os.path.join(self.root, "results", f"{cid}.json")

    def has_result(self, cid: str) -> bool:
        return os.path.exists(self.result_path(cid))

    def save_result(self, cid: str, result: dict):
        result["saved"] = _now()
        with open(self.result_path(cid), "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False)

    def load_result(self, cid: str) -> dict | None:
        if not self.has_result(cid):
            return None
        with open(self.result_path(cid), encoding="utf-8") as fh:
            return json.load(fh)

    def results(self) -> dict[str, dict]:
        out = {}
        for c in self.candidates():
            r = self.load_result(c["id"])
            if r is not None:
                out[c["id"]] = r
        return out

    # ---------- signatures (sampled ranks for redundancy) ----------
    def save_signature(self, cid: str, sig: pd.DataFrame):
        np.save(os.path.join(self.root, "signatures", f"{cid}.npy"), sig.to_numpy(np.float16))

    def load_signature(self, cid: str, index, columns) -> pd.DataFrame | None:
        p = os.path.join(self.root, "signatures", f"{cid}.npy")
        if not os.path.exists(p):
            return None
        return pd.DataFrame(np.load(p).astype(np.float32), index=index, columns=columns)

    # ---------- pool frames (direction-applied raw factor values) ----------
    def save_pool_frame(self, cid: str, frame: pd.DataFrame):
        frame.astype(np.float32).to_parquet(os.path.join(self.root, "pool", f"{cid}.parquet"))

    def rank_path(self, cid: str) -> str:
        return os.path.join(self.root, "pool_ranks", f"{cid}.npy")

    def save_pool_ranks(self, cid: str, pct: np.ndarray):
        np.save(self.rank_path(cid), pct.astype(np.float16))

    def has_pool_ranks(self, cid: str) -> bool:
        return os.path.exists(self.rank_path(cid))

    def load_pool_frame(self, cid: str) -> pd.DataFrame:
        df = pd.read_parquet(os.path.join(self.root, "pool", f"{cid}.parquet"))
        df.index = pd.DatetimeIndex(df.index)
        return df

    def pool_ids(self) -> list[str]:
        ids = {f[:-8] for f in os.listdir(os.path.join(self.root, "pool")) if f.endswith(".parquet")}
        ids |= {f[:-4] for f in os.listdir(os.path.join(self.root, "pool_ranks")) if f.endswith(".npy")}
        return sorted(ids)

    def drop_pool_frame(self, cid: str):
        for p in (os.path.join(self.root, "pool", f"{cid}.parquet"), self.rank_path(cid)):
            if os.path.exists(p):
                os.remove(p)

    # ---------- summaries ----------
    def table(self) -> pd.DataFrame:
        rows = []
        for c in self.candidates():
            r = self.load_result(c["id"])
            row = {"id": c["id"], "name": c.get("name"), "source": c.get("source"), "mechanism": c.get("mechanism_id"),
                   "batch": c.get("batch"), "expression": c["expression"], "status": c.get("status", "pending")}
            if r:
                ar = r.get("annual_rank_ic", {})
                row.update({"direction": r.get("direction"), "train_t": r.get("train_t"),
                            "mean_ric": r.get("target_mean_rank_ic"), "worst_ric": r.get("target_worst_rank_ic"),
                            "icir": r.get("target_icir"), "mean_pic": r.get("target_mean_pearson_ic"),
                            "worst_pic": r.get("target_worst_pearson_ic"), "ls": r.get("long_short_target"),
                            "top_turnover": r.get("top_decile_turnover"), "failure": r.get("failure_class"),
                            "redundant_with": r.get("redundant_with"), "max_pool_corr": r.get("max_pool_corr"),
                            "in_pool": r.get("in_pool", False)})
                for y in range(2019, 2025):
                    v = ar.get(str(y), {}).get("mean")
                    row[f"ric{y}"] = v
                exp = r.get("style_exposure") or {}
                for k, v in exp.items():
                    row[f"exp_{k}"] = v
                lh = r.get("long_hold5") or {}
                row["long_net_sharpe"] = lh.get("net_sharpe"); row["long_net_ann"] = lh.get("net_excess_ann")
                row["long_gross_ann"] = lh.get("gross_excess_ann"); row["long_turnover"] = lh.get("daily_turnover")
            rows.append(row)
        return pd.DataFrame(rows)

    def decision_path(self, name: str) -> str:
        return os.path.join(self.root, "decisions", f"{name}.json")
