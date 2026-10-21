"""Reproducible 60-minute single-GRU experiments on an audited real panel.

CLI: python -m gru_research.train --dataset PANEL --output RUN --stage all
The default all stage completes fixed labels, BLO, selection, ablations, freeze and
prediction evaluation. CPU is available only through explicit --device cpu (tests).
"""
from __future__ import annotations

import argparse
from collections import deque
import csv
import hashlib
import json
import os
from pathlib import Path
import random
import time
import numpy as np
import torch

from .evaluate import (correlation, ranks, breakdown, summarize_seed_results,
                       evaluate_frozen, file_hash, write_json)
from .model import PlanGRU, adamw_groups, ic_losses, unrolled_step

NS_DAY = 86_400_000_000_000
CHINA_OFFSET = 8 * 3_600_000_000_000
SPLITS = {
    "inner": ("2019-01-01", "2023-01-01"),
    "outer": ("2023-01-01", "2024-01-01"),
    "train": ("2019-01-01", "2024-01-01"),
    "validation": ("2024-01-01", "2025-01-01"),
    "test_2025": ("2025-01-01", "2026-01-01"),
    "recent_2026": ("2026-01-01", "2026-09-24")}


def local_days(ns: np.ndarray) -> np.ndarray:
    return ((np.asarray(ns, dtype=np.int64) + CHINA_OFFSET) // NS_DAY).astype(np.int64)


def day_number(date: str) -> int:
    return int(np.datetime64(date, "D").astype(np.int64))


def date_text(day: int) -> str:
    return str(np.datetime64(int(day), "D"))


class PanelDataset:
    """Memory-map historical rows; never fetch a future row for a sequence."""
    def __init__(self, path: Path, enforce_audit: bool = True):
        self.path = Path(path)
        self.meta = json.loads((self.path / "meta.json").read_text())
        if enforce_audit:
            if self.meta.get("production_ready") is not True or self.meta.get("causality_audit", {}).get("passed") is not True:
                raise ValueError("Formal training requires production_ready and an actually passed causality_audit")
            if not self.meta.get("data_fingerprint"):
                raise ValueError("Audited metadata must include data_fingerprint")
        if self.meta.get("timestamp_format", "utc_ns") != "utc_ns" or self.meta.get("timezone", "Asia/Shanghai") != "Asia/Shanghai":
            raise ValueError("Panel timestamps must be UTC ns; trading calendar Asia/Shanghai")
        keys = ("X", "hour_complete", "members", "labels", "industry", "times", "label_end_times", "earliest_execution_times")
        for key in keys:
            setattr(self, key, np.load(self.path / (key + ".npy"), mmap_mode="r", allow_pickle=False))
        self.symbols = json.loads((self.path / "symbols.json").read_text())
        if self.X.ndim != 3:
            raise ValueError("X must be [time, universe, continuous_field]")
        self.T, self.U, self.F = self.X.shape
        if self.X.dtype != np.float32 or self.labels.dtype != np.float32:
            raise ValueError("X and labels must be float32")
        if self.hour_complete.dtype != np.bool_ or self.members.dtype != np.bool_:
            raise ValueError("Completeness and membership masks must be boolean")
        for key in ("hour_complete", "members", "industry"):
            if getattr(self, key).shape != (self.T, self.U):
                raise ValueError(key + " must align with [time, universe]")
        if self.labels.shape != (self.T, self.U, 4) or self.label_end_times.shape != (self.T, 4):
            raise ValueError("Four labels/end times required")
        if self.times.shape != (self.T,) or self.earliest_execution_times.shape != (self.T,) or len(self.symbols) != self.U:
            raise ValueError("Timestamps/symbols must align")
        if self.times.dtype != np.int64 or self.label_end_times.dtype != np.int64 or self.earliest_execution_times.dtype != np.int64:
            raise ValueError("Timestamp arrays must be int64 ns")
        if len(set(self.symbols)) != self.U or np.any(np.diff(self.times) <= 0):
            raise ValueError("Symbols/times must be unique and time strictly increasing")
        if np.any(self.earliest_execution_times <= self.times):
            raise ValueError("Every executable entry must be strictly after its completed signal hour")
        self.days = local_days(self.times)
        self.trading_days = np.unique(self.days)
        hours = ((np.asarray(self.times) + CHINA_OFFSET) // 3_600_000_000_000) % 24
        mapping = {10: 0, 11: 1, 14: 2, 15: 3}
        if any(int(h) not in mapping for h in hours):
            raise ValueError("Standard hour ends must be 10:30/11:30/14:00/15:00 in Asia/Shanghai")
        self.slots = np.array([mapping[int(h)] for h in hours], dtype=np.int64)
        minutes = ((np.asarray(self.times) + CHINA_OFFSET) // 60_000_000_000) % 60
        if np.any(minutes != np.where(np.isin(hours, [10, 11]), 30, 0)):
            raise ValueError("Nonstandard 60-minute timestamp")
        if np.any(np.diff(self.days) < 0):
            raise ValueError("Trading days must increase")
        for day in self.trading_days:
            if not np.array_equal(self.slots[self.days == day], np.arange(4)):
                raise ValueError("Every calendar row day must contain all four standard slots")
        raw_groups = self.meta["groups"]
        aliases = {"hour": "hour24", "daily": "daily16", "factors": "factors", "fund": "fund16", "ages": "ages2"}
        self.groups = {k: list(raw_groups[k] if k in raw_groups else raw_groups[alias]) for k, alias in aliases.items()}
        if any(len(self.groups[k]) != n for k, n in [("hour", 24), ("daily", 16), ("fund", 16), ("ages", 2)]):
            raise ValueError("PLAN requires hour24/daily16/fund16/ages2")
        if len(self.groups["factors"]) > 64:
            raise ValueError("At most 64 training-selected factors")
        all_indices = [i for indices in self.groups.values() for i in indices]
        if sorted(all_indices) != list(range(self.F)):
            raise ValueError("Feature groups must partition X exactly")
        self.n_industries = int(self.meta.get("n_industries", int(np.max(self.industry)) + 1))
        if self.n_industries < 1 or np.min(self.industry) < 0 or np.max(self.industry) >= self.n_industries:
            raise ValueError("Industry IDs must be in range; 0 reserved for unknown")
        # Only ~T*U int32 storage; avoid repeatedly inspecting all 80 rows.
        cumulative = np.cumsum(~self.hour_complete, axis=0, dtype=np.int32)
        self._incomplete_prefix = np.concatenate([np.zeros((1, self.U), dtype=np.int32), cumulative], axis=0)
        self._eligibility_cache = {}
        self._split_reports = {}
        canonical = json.dumps(self.meta, sort_keys=True, separators=(",", ":"))
        self.fingerprint = hashlib.sha256(canonical.encode()).hexdigest()

    def eligible_mask(self, t: int, length: int = 80, require_labels: bool = True) -> np.ndarray:
        if t < length - 1:
            return np.zeros(self.U, dtype=np.bool_)
        complete = self._incomplete_prefix[t + 1] == self._incomplete_prefix[t + 1 - length]
        mask = complete & self.members[t]
        # Membership is required only at prediction time, not for all historical rows.
        if require_labels:
            mask &= np.isfinite(self.labels[t]).all(axis=1)
        return mask

    def split_indices(self, split: str, config: dict, require_labels: bool = True) -> np.ndarray:
        start, end = SPLITS[split]
        start_day, end_day = day_number(start), day_number(end)
        candidates = (self.days >= start_day) & (self.days < end_day)
        range_sections = int(candidates.sum())
        if split != "recent_2026":
            preceding = self.trading_days[self.trading_days < end_day]
            embargo = int(config["embargo_trading_days"])
            if len(preceding) < embargo:
                raise ValueError("Insufficient trading calendar for the required embargo")
            cutoff_day = int(preceding[-embargo])
            candidates &= self.days < cutoff_day
            label_cutoff_ns = cutoff_day * NS_DAY - CHINA_OFFSET
        else:
            label_cutoff_ns = end_day * NS_DAY - CHINA_OFFSET
        mature = (self.label_end_times > self.earliest_execution_times[:, None]).all(axis=1)
        mature &= (np.diff(self.label_end_times, axis=1) > 0).all(axis=1)
        mature &= (self.label_end_times < label_cutoff_ns).all(axis=1)
        # End times beyond available market history cannot enter scored evaluation.
        mature &= self.label_end_times[:, 3] <= self.times[-1]
        if require_labels:
            candidates &= mature
        out, low_count, low_variance = [], 0, 0
        for t in np.flatnonzero(candidates):
            mask = self.eligible_mask(int(t), config["sequence_length"], require_labels)
            if int(mask.sum()) < config["min_stocks"]:
                low_count += 1
                continue
            if require_labels and np.any(np.var(np.asarray(self.labels[t, mask], dtype=np.float64), axis=0) <= config["label_variance_floor"]):
                low_variance += 1
                continue
            out.append(t)
        self._split_reports[(split, require_labels)] = {"range_sections": range_sections,
            "excluded_boundary_embargo_or_maturity": range_sections - int(candidates.sum()),
            "excluded_below_min_stocks": low_count, "excluded_label_variance": low_variance,
            "eligible_sections": len(out), "min_stocks": config["min_stocks"],
            "label_variance_floor": config["label_variance_floor"]}
        if not out:
            raise ValueError(f"No eligible {split} samples after complete-window/maturity/embargo filtering")
        return np.asarray(out, dtype=np.int64)

    def batches(self, indices: np.ndarray, batch_sections: int, rng: np.random.Generator | None):
        # A random complete cross section is indivisible; prefer different trading days.
        pools = {}
        order = np.array(indices, copy=True)
        if rng is not None:
            rng.shuffle(order)
        for t in order:
            pools.setdefault(int(self.days[t]), deque()).append(int(t))
        while pools:
            days = list(pools)
            if rng is not None:
                rng.shuffle(days)
            chosen = days[:batch_sections]
            batch = [pools[d].popleft() for d in chosen]
            for d in chosen:
                if not pools[d]:
                    del pools[d]
            yield batch

    def batch(self, indices: list[int], device: str, config: dict, require_labels: bool = True):
        x, industry, slots, labels, counts = [], [], [], [], []
        for t in indices:
            valid = self.eligible_mask(t, config["sequence_length"], require_labels)
            n = int(valid.sum())
            if n < config["min_stocks"]:
                raise ValueError("An incomplete cross section entered a training batch")
            start = t + 1 - config["sequence_length"]
            x.append(np.asarray(self.X[start:t + 1, valid]).transpose(1, 0, 2))
            industry.append(np.asarray(self.industry[start:t + 1, valid], dtype=np.int64).T)
            slots.append(np.broadcast_to(self.slots[start:t + 1], (n, config["sequence_length"])))
            labels.append(np.asarray(self.labels[t, valid]))
            counts.append(n)
        arrays = [np.concatenate(a, axis=0) for a in (x, industry, slots, labels)]
        tensors = [torch.from_numpy(np.array(a, copy=True)).to(device=device) for a in arrays]
        return (*tensors, counts)

    def audit_summary(self, config: dict):
        return {"dataset_fingerprint": self.fingerprint, "shape": list(self.X.shape),
                "groups": self.groups, "n_industries": self.n_industries,
                "unknown_industry_stock_hours": int(np.sum((self.industry == 0) & self.members)),
                "splits": {s: {**self._split_reports[(s, True)], "first_time_ns": int(self.times[ix[0]]),
                                "last_time_ns": int(self.times[ix[-1]])}
                           for s in SPLITS for ix in [self.split_indices(s, config)]}}


def seed_everything(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False


def rng_state() -> dict:
    return {"python": random.getstate(), "numpy": np.random.get_state(), "torch": torch.get_rng_state(),
            "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None}


def restore_rng(state: dict):
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    torch.set_rng_state(state["torch"].cpu())
    if state.get("cuda") is not None and torch.cuda.is_available():
        torch.cuda.set_rng_state_all([s.cpu() for s in state["cuda"]])


def checkpoint_save(path: Path, state: dict):
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save(state, temporary)
    temporary.replace(path)


def checkpoint_load(path: Path, device: str):
    # Checkpoints are generated by this run and include optimizer and RNG state.
    return torch.load(path, map_location=device, weights_only=False)


def event(log: Path, record: dict):
    text = json.dumps(record, ensure_ascii=False, allow_nan=False)
    with log.open("a") as f:
        f.write(text + "\n")
    print(text, flush=True)


def predict_section(model: PlanGRU, dataset: PanelDataset, t: int, config: dict, device: str):
    batch = dataset.batch([t], device, config, require_labels=False)
    with torch.inference_mode():
        scores = model(*batch[:3]).cpu().numpy()
    mask = dataset.eligible_mask(t, config["sequence_length"], False)
    return scores, mask


def validate(model: PlanGRU, dataset: PanelDataset, indices: np.ndarray, config: dict, device: str):
    model.eval()
    records = []
    with torch.inference_mode():
        for times in dataset.batches(indices, config["batch_cross_sections"], None):
            x, industry, slots, labels, counts = dataset.batch(times, device, config)
            scores = model(x, industry, slots).cpu().numpy()
            target = labels[:, 3].cpu().numpy()
            offset = 0
            for t, n in zip(times, counts):
                s, y = scores[offset:offset + n], target[offset:offset + n]
                offset += n
                records.append({"signal_time_ns": int(dataset.times[t]), "date": date_text(dataset.days[t]),
                    "slot": int(dataset.slots[t]), "ic": correlation(s, y, config["min_stocks"]),
                    "rank_ic": correlation(ranks(s), ranks(y), config["min_stocks"]),
                    "pool_count": int(dataset.members[t].sum()), "scored_count": n})
    summary = breakdown(records)
    if summary["overall"]["ic"] is None:
        raise FloatingPointError("No finite validation IC")
    return summary, records


def fit_fixed(dataset: PanelDataset, run_dir: Path, label: int, seed: int, variant: str, config: dict, device: str):
    run_dir.mkdir(parents=True, exist_ok=True)
    signature = {"dataset_fingerprint": dataset.fingerprint, "config": config, "seed": seed, "label": label, "variant": variant}
    result_path, last_path, best_path = run_dir / "result.json", run_dir / "last.pt", run_dir / "best.pt"
    if result_path.exists():
        result = json.loads(result_path.read_text())
        if result["signature"] != signature:
            raise ValueError("Existing run differs from current experiment configuration")
        if not best_path.exists() or result["best_checkpoint_sha256"] != file_hash(best_path):
            raise ValueError("Completed run's best checkpoint is missing or changed")
        return result
    seed_everything(seed)
    model = PlanGRU(dataset.groups, dataset.n_industries, variant).to(device)
    optimizer = torch.optim.AdamW(adamw_groups(model, config["weight_decay"]), lr=config["learning_rate"], betas=tuple(config["betas"]))
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="max", factor=0.5,
        patience=max(0, config["lr_patience"] - 1), threshold=0.0, threshold_mode="abs", min_lr=config["min_learning_rate"])
    train_indices = dataset.split_indices("train", config)
    val_indices = dataset.split_indices("validation", config)
    start_epoch, best_ic, best_epoch, bad_epochs = 0, float("-inf"), -1, 0
    if last_path.exists():
        state = checkpoint_load(last_path, device)
        if state["signature"] != signature:
            raise ValueError("Resume checkpoint does not match dataset/config/seed/label/input groups")
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        start_epoch, best_ic, best_epoch, bad_epochs = state["epoch"] + 1, state["best_ic"], state["best_epoch"], state["bad_epochs"]
        restore_rng(state["rng"])
    for epoch in range(start_epoch, config["epochs"]):
        if bad_epochs >= config["patience"]:
            break
        model.train()
        started, losses, skipped = time.monotonic(), [], 0
        for times in dataset.batches(train_indices, config["batch_cross_sections"], np.random.default_rng(seed + 10_000 * epoch)):
            x, industry, slots, labels, counts = dataset.batch(times, device, config)
            optimizer.zero_grad(set_to_none=True)
            candidate_losses, ignored = ic_losses(model(x, industry, slots), labels, counts,
                                                  config["min_stocks"], config["label_variance_floor"])
            loss = candidate_losses[label - 1]
            if not bool(torch.isfinite(loss)):
                raise FloatingPointError("Non-finite training loss")
            loss.backward()
            norm = torch.nn.utils.clip_grad_norm_(model.parameters(), config["gradient_clip"])
            if not bool(torch.isfinite(norm)):
                raise FloatingPointError("Non-finite model gradient")
            optimizer.step()
            losses.append(float(loss.detach()))
            skipped += ignored
        validation, records = validate(model, dataset, val_indices, config, device)
        ic = validation["overall"]["ic"]
        improved = ic > best_ic
        if improved:
            best_ic, best_epoch, bad_epochs = ic, epoch, 0
        else:
            bad_epochs += 1
        scheduler.step(ic)
        state = {"signature": signature, "model": model.state_dict(), "optimizer": optimizer.state_dict(),
                 "scheduler": scheduler.state_dict(), "epoch": epoch, "best_ic": best_ic, "best_epoch": best_epoch,
                 "bad_epochs": bad_epochs, "rng": rng_state(), "groups": dataset.groups, "n_industries": dataset.n_industries}
        if improved:
            checkpoint_save(best_path, state)
            write_json(run_dir / "best_validation.json", validation)
            write_json(run_dir / "best_validation_sections.json", {"records": records})
        checkpoint_save(last_path, state)
        event(run_dir / "epochs.jsonl", {"event": "epoch", "seed": seed, "label": label, "variant": variant,
              "epoch": epoch + 1, "train_loss": float(np.mean(losses)), "validation_y4_ic": ic,
              "validation_y4_rank_ic": validation["overall"]["rank_ic"], "best_ic": best_ic,
              "learning_rate": optimizer.param_groups[0]["lr"], "bad_epochs": bad_epochs,
              "skipped_sections": skipped, "elapsed_seconds": time.monotonic() - started})
    if not best_path.exists():
        raise RuntimeError("No model checkpoint was produced")
    result = {"signature": signature, "seed": seed, "label": label, "variant": variant,
        "best_validation_ic": best_ic, "best_epoch": best_epoch + 1, "best_checkpoint": str(best_path.resolve()),
        "best_checkpoint_sha256": file_hash(best_path), "epochs_completed": epoch + 1 if 'epoch' in locals() else start_epoch,
        "complete": True, "real_data": bool(dataset.meta.get("production_ready"))}
    write_json(result_path, result)
    return result


def fit_blo(dataset: PanelDataset, run_dir: Path, seed: int, config: dict, device: str):
    run_dir.mkdir(parents=True, exist_ok=True)
    signature = {"dataset_fingerprint": dataset.fingerprint, "config": config, "seed": seed, "mode": "blo"}
    result_path, last_path = run_dir / "result.json", run_dir / "last.pt"
    if result_path.exists():
        result = json.loads(result_path.read_text())
        if result["signature"] != signature:
            raise ValueError("BLO run configuration changed")
        return result
    seed_everything(seed)
    model = PlanGRU(dataset.groups, dataset.n_industries, "full").to(device)
    warm_opt = torch.optim.AdamW(adamw_groups(model, config["weight_decay"]), lr=config["learning_rate"], betas=tuple(config["betas"]))
    logits = torch.zeros(4, device=device, requires_grad=True)
    lambda_opt = torch.optim.Adam([logits], lr=config["blo_lambda_learning_rate"])
    inner = dataset.split_indices("inner", config)
    outer = dataset.split_indices("outer", config)
    warm_epoch, step, tail = 0, 0, deque(maxlen=config["blo_average_tail"])
    if last_path.exists():
        state = checkpoint_load(last_path, device)
        if state["signature"] != signature:
            raise ValueError("BLO resume signature changed")
        model.load_state_dict(state["model"])
        warm_opt.load_state_dict(state["warm_optimizer"])
        with torch.no_grad():
            logits.copy_(state["logits"])
        lambda_opt.load_state_dict(state["lambda_optimizer"])
        warm_epoch, step = state["warm_epoch"], state["step"]
        tail.extend(state["tail"])
        restore_rng(state["rng"])
    def save():
        checkpoint_save(last_path, {"signature": signature, "model": model.state_dict(),
             "warm_optimizer": warm_opt.state_dict(), "logits": logits.detach(), "lambda_optimizer": lambda_opt.state_dict(),
             "warm_epoch": warm_epoch, "step": step, "tail": list(tail), "rng": rng_state(),
             "groups": dataset.groups, "n_industries": dataset.n_industries})
    for epoch in range(warm_epoch, config["blo_warmup_epochs"]):
        model.train()
        losses = []
        for times in dataset.batches(inner, config["batch_cross_sections"], np.random.default_rng(seed + 10_000 * epoch)):
            batch = dataset.batch(times, device, config)
            warm_opt.zero_grad(set_to_none=True)
            values, _ = ic_losses(model(*batch[:3]), batch[3], batch[4], config["min_stocks"])
            loss = values.mean()
            loss.backward()
            norm = torch.nn.utils.clip_grad_norm_(model.parameters(), config["gradient_clip"])
            if not bool(torch.isfinite(norm)):
                raise FloatingPointError("Non-finite BLO warmup gradient")
            warm_opt.step()
            losses.append(float(loss.detach()))
        warm_epoch = epoch + 1
        save()
        event(run_dir / "epochs.jsonl", {"event": "blo_warmup", "seed": seed, "epoch": warm_epoch, "loss": float(np.mean(losses))})
    # Step-index-seeded samplers make checkpoint resumption independent of iterator state.
    model.train()
    while step < config["blo_steps"]:
        began = time.monotonic()
        rng = np.random.default_rng(seed + 1_000_000 + step)
        in_times = next(dataset.batches(inner, config["batch_cross_sections"], rng))
        out_times = next(dataset.batches(outer, config["batch_cross_sections"], rng))
        inner_batch, outer_batch = dataset.batch(in_times, device, config), dataset.batch(out_times, device, config)
        updated, gradient, diagnostics = unrolled_step(model, logits, inner_batch, outer_batch,
            config["blo_inner_learning_rate"], config["blo_inner_weight_decay"], config["blo_entropy_gamma"], config["min_stocks"])
        lambda_opt.zero_grad(set_to_none=True)
        logits.grad = gradient
        lambda_opt.step()
        with torch.no_grad():
            for name, parameter in model.named_parameters():
                parameter.copy_(updated[name].detach())
        weights = logits.softmax(dim=0).detach().cpu().tolist()
        tail.append(weights)
        step += 1
        event(run_dir / "steps.jsonl", {"event": "blo_step", "seed": seed, "step": step, "weights": weights,
              "elapsed_seconds": time.monotonic() - began, **diagnostics})
        if step % 25 == 0 or step == config["blo_steps"]:
            save()
    if len(tail) != config["blo_average_tail"]:
        raise ValueError("BLO budget shorter than required tail average")
    result = {"signature": signature, "seed": seed, "steps": step, "warmup_epochs": warm_epoch,
              "mean_tail_weights": np.mean(np.asarray(tail), axis=0).tolist(), "tail_steps": len(tail), "complete": True}
    write_json(result_path, result)
    return result


def prediction_records(dataset: PanelDataset, indices: np.ndarray, checkpoints: list[str], config: dict,
                       device: str, scores_csv: Path | None = None, model_version: str = "validation",
                       seed_records_out: dict | None = None):
    models = []
    model_seeds = []
    for path in checkpoints:
        state = checkpoint_load(Path(path), device)
        if state["signature"]["dataset_fingerprint"] != dataset.fingerprint:
            raise ValueError("Scoring dataset differs from model dataset")
        model = PlanGRU(state["groups"], state["n_industries"], state["signature"]["variant"]).to(device)
        model.load_state_dict(state["model"])
        model.eval()
        models.append(model)
        model_seeds.append(int(state["signature"]["seed"]))
    if seed_records_out is not None:
        for seed in model_seeds:
            seed_records_out[str(seed)] = []
    records = []
    handle = scores_csv.open("w", newline="") if scores_csv else None
    writer = csv.writer(handle) if handle else None
    if writer:
        writer.writerow(["symbol", "signal_time", "earliest_execution_time", "score", "percentile", "model_version", "data_quality_flag"])
    try:
        for t in indices:
            t = int(t)
            mask = dataset.eligible_mask(t, config["sequence_length"], False)
            x, industry, slots, _, _ = dataset.batch([t], device, config, False)
            seed_scores = []
            with torch.inference_mode():
                for model in models:
                    scores = model(x, industry, slots).cpu().numpy().astype(np.float64)
                    if not np.isfinite(scores).all():
                        raise FloatingPointError("Non-finite model score")
                    std = scores.std()
                    seed_scores.append((scores - scores.mean()) / std if std > 0 else np.zeros_like(scores))
            score = np.mean(seed_scores, axis=0)
            target = np.asarray(dataset.labels[t, mask, 3])
            mature = bool(dataset.label_end_times[t, 3] > dataset.earliest_execution_times[t] and
                          dataset.label_end_times[t, 3] <= dataset.times[-1])
            if not mature:
                target = np.full_like(target, np.nan)
            finite = np.isfinite(target)
            ic = correlation(score, target, config["min_stocks"])
            rank_ic = correlation(ranks(score[finite]), ranks(target[finite]), config["min_stocks"]) if finite.sum() >= config["min_stocks"] else float("nan")
            records.append({"signal_time_ns": int(dataset.times[t]), "date": date_text(dataset.days[t]), "slot": int(dataset.slots[t]),
                "ic": ic, "rank_ic": rank_ic, "pool_count": int(dataset.members[t].sum()), "scored_count": int(mask.sum()),
                "mature_label_count": int(finite.sum()), "label_mature": mature,
                "excluded_incomplete_or_short_window": int((dataset.members[t] & ~mask).sum())})
            if seed_records_out is not None:
                for seed, seed_score in zip(model_seeds, seed_scores):
                    seed_record = {**records[-1], "seed": seed,
                        "ic": correlation(seed_score, target, config["min_stocks"]),
                        "rank_ic": correlation(ranks(seed_score[finite]), ranks(target[finite]), config["min_stocks"])
                                   if finite.sum() >= config["min_stocks"] else float("nan")}
                    seed_records_out[str(seed)].append(seed_record)
            if writer:
                percentiles = (ranks(score) - 0.5) / len(score)
                symbol_indices = np.flatnonzero(mask)
                for j, u in enumerate(symbol_indices):
                    flags = []
                    if np.isnan(dataset.X[t, u]).any():
                        flags.append("feature_missing")
                    if dataset.industry[t, u] == 0:
                        flags.append("industry_unknown")
                    if not mature:
                        flags.append("label_unmatured")
                    writer.writerow([dataset.symbols[u], int(dataset.times[t]), int(dataset.earliest_execution_times[t]),
                                     float(score[j]), float(percentiles[j]), model_version, "|".join(flags) or "ok"])
                for u in np.flatnonzero(dataset.members[t] & ~mask):
                    writer.writerow([dataset.symbols[u], int(dataset.times[t]), int(dataset.earliest_execution_times[t]),
                                     "", "", model_version, "incomplete_hour_window"])
    finally:
        if handle:
            handle.close()
    return records


def freeze_models(dataset: PanelDataset, output: Path, config: dict, selection: dict):
    selected = selection["selected_label"]
    models = {f"fixed_label_{label}": [str((output / "fixed" / f"label_{label}" / f"seed_{s}" / "best.pt").resolve()) for s in config["seeds"]]
              for label in (1, 2, 3, 4)}
    models["blo_selected"] = [str((output / "blo_final" / f"label_{selection['blo_label']}" / f"seed_{s}" / "best.pt").resolve()) for s in config["seeds"]]
    models["selected_full"] = models["blo_selected"] if selection["selected_method"] == "blo" else models["fixed_label_" + str(selected)]
    for variant in ("pv", "pv_factors", "pv_fund"):
        models[variant] = [str((output / "ablations" / variant / f"seed_{s}" / "best.pt").resolve()) for s in config["seeds"]]
    hashes = {path: file_hash(Path(path)) for paths in models.values() for path in paths}
    frozen = {"dataset_fingerprint": dataset.fingerprint, "config": config, "selection": selection,
              "evaluation_models": models, "checkpoint_hashes": hashes, "frozen_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "no_test_driven_tuning": True}
    frozen["freeze_id"] = hashlib.sha256(json.dumps({"data": dataset.fingerprint, "config": config,
                        "selection": selection, "hashes": hashes}, sort_keys=True).encode()).hexdigest()[:20]
    path = output / "freeze.json"
    if path.exists():
        existing = json.loads(path.read_text())
        if existing["freeze_id"] != frozen["freeze_id"]:
            raise ValueError("Frozen experiment changed; do not overwrite or re-tune the historical test")
        return existing
    write_json(path, frozen)
    return frozen


def run_pipeline(dataset: PanelDataset, output: Path, config: dict, device: str, stage: str,
                 only_seed: int | None = None, only_label: int | None = None):
    seeds = [only_seed] if only_seed is not None else config["seeds"]
    labels = [only_label] if only_label is not None else [1, 2, 3, 4]
    if stage in ("all", "baseline"):
        for label in labels:
            for seed in seeds:
                fit_fixed(dataset, output / "fixed" / f"label_{label}" / f"seed_{seed}", label, seed, "full", config, device)
    if stage in ("all", "blo"):
        for seed in seeds:
            fit_blo(dataset, output / "blo" / f"seed_{seed}", seed, config, device)
        all_results = [output / "blo" / f"seed_{s}" / "result.json" for s in config["seeds"]]
        if all(p.exists() for p in all_results):
            weights = np.mean([json.loads(p.read_text())["mean_tail_weights"] for p in all_results], axis=0)
            blo_label = int(np.argmax(weights) + 1)
            write_json(output / "blo_weights.json", {"mean_weights": weights.tolist(), "selected_label": blo_label,
                       "seeds": config["seeds"], "per_seed_tail_steps": config["blo_average_tail"]})
            for seed in config["seeds"]:
                fit_fixed(dataset, output / "blo_final" / f"label_{blo_label}" / f"seed_{seed}", blo_label, seed, "full", config, device)
    if stage in ("all", "select"):
        alternatives = {}
        for label in (1, 2, 3, 4):
            alternatives["fixed_" + str(label)] = summarize_seed_results([output / "fixed" / f"label_{label}" / f"seed_{s}" / "result.json" for s in config["seeds"]])
        blo_label = json.loads((output / "blo_weights.json").read_text())["selected_label"]
        alternatives["blo"] = summarize_seed_results([output / "blo_final" / f"label_{blo_label}" / f"seed_{s}" / "result.json" for s in config["seeds"]])
        best = max(alternatives, key=lambda k: alternatives[k]["mean_ic"])
        gain = alternatives[best]["mean_ic"] - alternatives["fixed_4"]["mean_ic"]
        selected = "fixed_4" if gain < config["label_selection_min_gain"] else best
        selected_label = blo_label if selected == "blo" else int(selected[-1])
        selection = {"selected_method": selected, "selected_label": selected_label, "blo_label": blo_label,
            "validation_year": 2024, "alternatives": alternatives, "best_gain_vs_direct_y4": gain,
            "minimum_gain": config["label_selection_min_gain"],
            "conclusion": "short-label increment not established" if selected == "fixed_4" else "chosen using 2024 validation; test and paired confidence intervals pending"}
        path = output / "selection.json"
        if (output / "freeze.json").exists() and path.exists() and json.loads(path.read_text()) != selection:
            raise ValueError("Selection changed after freeze")
        write_json(path, selection)
    if stage in ("all", "ablations"):
        selection = json.loads((output / "selection.json").read_text())
        for variant in ("pv", "pv_factors", "pv_fund"):
            for seed in seeds:
                fit_fixed(dataset, output / "ablations" / variant / f"seed_{seed}", selection["selected_label"], seed, variant, config, device)
    if stage in ("all", "freeze"):
        selection = json.loads((output / "selection.json").read_text())
        freeze_models(dataset, output, config, selection)
    if stage in ("all", "evaluate"):
        freeze = json.loads((output / "freeze.json").read_text())
        if freeze["dataset_fingerprint"] != dataset.fingerprint or freeze["config"] != config:
            raise ValueError("Frozen dataset or config differs from current evaluation")
        for path, digest in freeze["checkpoint_hashes"].items():
            if file_hash(Path(path)) != digest:
                raise ValueError("Frozen checkpoint changed")
        evaluate_frozen(dataset, output, freeze, config, device)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--stage", choices=["all", "baseline", "blo", "select", "ablations", "freeze", "evaluate", "audit"], default="all")
    parser.add_argument("--seed", type=int, choices=[11, 22, 33, 44, 55])
    parser.add_argument("--label", type=int, choices=[1, 2, 3, 4])
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if args.device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; formal CPU training requires explicit --device cpu")
    if config["dtype"] != "float32" or config["seeds"] != [11, 22, 33, 44, 55]:
        raise ValueError("PLAN requires FP32 and the five fixed seeds")
    if args.stage == "all" and (args.seed is not None or args.label is not None):
        raise ValueError("Use baseline/blo/ablations stage for a single seed or label; all preserves the full protocol")
    args.output.mkdir(parents=True, exist_ok=True)
    dataset = PanelDataset(args.dataset)
    write_json(args.output / "dataset_audit.json", dataset.audit_summary(config))
    write_json(args.output / "run_config.json", config)
    if args.stage != "audit":
        run_pipeline(dataset, args.output, config, args.device, args.stage, args.seed, args.label)


if __name__ == "__main__":
    main()
