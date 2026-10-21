"""Bounded R2 research: 30 chronological fits, one LR decision, five fixed refits.

Without --execute this prints the contract and does not import Torch or inspect
data. Actual execution requires an out-of-band reviewed causal receipt, closed
transport map, and new source seal. Historical evaluation is already exposed
research, and IC .07 is neither a guarantee nor a training-loop condition.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from .protocol import (ARMS, FOLDS, SEEDS, PROTOCOL_SHA256, final_budget,
                       load_protocol, plans, require, require_completed_diagnostics, select_lr, sha)
from .causal_gate import source_seal, verify_gate


def utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read(path):
    return json.loads(Path(path).read_text())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(path):
    h = __import__("hashlib").sha256()
    with Path(path).open("rb") as handle:
        for part in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def immutable(path, value):
    """Completed documents are immutable. Retrying only reuses identical bytes."""
    path = Path(path)
    blob = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode() + b"\n"
    if path.exists():
        require(path.read_bytes() == blob, "immutable R2 document differs: " + str(path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(blob)


def cleaned(value):
    """Represent invalid evaluation metrics explicitly, never as winning values."""
    if isinstance(value, dict):
        return {str(k): cleaned(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [cleaned(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def prepare_contract():
    return {"status": "PREPARED_ONLY_NO_TRAINING_OR_DATA_READ", "protocol_sha256": PROTOCOL_SHA256,
            "diagnostic_fits": 30, "final_fits": 5, "fit_cap": 35,
            "variant": "pv", "training_label": 3, "evaluation_label": 4,
            "arms": ARMS, "seeds": list(SEEDS), "folds": FOLDS,
            "causal_receipt_and_transport_and_source_seal_required": True,
            "IC07_guaranteed": False, "historical_evaluation_is_virgin_OOS": False,
            "new_future_ledger_records": 0, "actual_training_started": False}


class Runtime:
    def __init__(self, args):
        self.args = args
        self.root, self.output = args.root.resolve(), args.output.resolve()
        require(Path(__file__).resolve() == self.root / "gru_research" / "r2_research" / "runner.py",
                "executed runner is not the explicitly guarded ROOT source")
        self.protocol = load_protocol(args.protocol)
        self.new_source = source_seal(self.root, args.source_manifest, args.expected_source_manifest_sha256)
        self.gate = verify_gate(self.root, args.dataset, args.causal_evidence_directory,
                                args.expected_causal_receipt_sha256,
                                args.expected_causal_evidence_manifest_sha256, self.protocol)
        require(self.output.parent == self.root / "outputs" and self.output.name.startswith("r2_"),
                "R2 output must be an explicitly named new ROOT/outputs/r2_* directory")
        # No frozen tree, original dataset, or original formal results are writable.
        dataset_path = args.dataset.resolve()
        forbidden = [dataset_path, self.root / "gru_research", self.root / "outputs" / "formal"]
        require(all(self.output != p and p not in self.output.parents for p in forbidden),
                "R2 output must be separate from original data/source/formal results")
        self.binding = {"protocol_sha256": PROTOCOL_SHA256,
                        "new_source_manifest_sha256": self.new_source["manifest_sha256"],
                        "causal_receipt_sha256": self.gate["causal_receipt_sha256"],
                        "causal_transport_manifest_sha256": self.gate["causal_transport_manifest_sha256"],
                        "dataset_fingerprint": self.protocol["original_dataset"]["canonical_metadata_fingerprint"]}
        self.output.mkdir(parents=True, exist_ok=True)
        immutable(self.output / "run_identity.json", self.binding)
        immutable(self.output / "qualified_permission.json", self.gate)
        # These imports must remain below the actual permission and identity checks.
        import numpy as np
        import torch
        from gru_research import train, model, evaluate
        from .folds import period_indices
        for module in (train, model, evaluate):
            require(Path(module.__file__).resolve() == self.root / "gru_research" / (module.__name__.split(".")[-1] + ".py"),
                    "imported original Torch implementation differs from guarded source root")
        self.np, self.torch, self.train, self.model_module, self.evaluate = np, torch, train, model, evaluate
        self.period_indices = period_indices
        self.device = args.device
        require(not self.device.startswith("cuda") or torch.cuda.is_available(), "CUDA unavailable; no automatic CPU fallback")
        self.config = read(self.root / "gru_research" / "config.json")
        require(self.config["learning_rate"] == ARMS["control"] and self.config["dtype"] == "float32"
                and self.config["seeds"] == list(SEEDS), "original config differs")
        self.dataset = train.PanelDataset(args.dataset)
        require(self.dataset.fingerprint == self.binding["dataset_fingerprint"], "runtime original panel metadata differs")
        self.assert_guards()

    def assert_guards(self):
        current = source_seal(self.root, self.args.source_manifest, self.args.expected_source_manifest_sha256)
        gate = verify_gate(self.root, self.args.dataset, self.args.causal_evidence_directory,
                           self.args.expected_causal_receipt_sha256,
                           self.args.expected_causal_evidence_manifest_sha256, self.protocol)
        require(current == self.new_source and gate == self.gate, "small source/evidence guards changed during execution")

    def indices(self, period):
        values, report = self.period_indices(self.dataset, *period, self.config)
        require(len(values) > 0, "chronological interval has no eligible complete mature sections")
        return values, report

    def signature(self, stage, arm, seed, **fields):
        config = copy.deepcopy(self.config)
        config["learning_rate"] = ARMS[arm]
        return {**self.binding, "config": config, "seed": seed, "label": 3, "variant": "pv",
                "stage": stage, "arm": arm, **fields}

    def setup(self, signature, directory, diagnostic):
        t, m = self.torch, self.model_module
        self.train.seed_everything(signature["seed"])
        network = m.PlanGRU(self.dataset.groups, self.dataset.n_industries, "pv").to(self.device)
        optimizer = t.optim.AdamW(m.adamw_groups(network, self.config["weight_decay"]),
                                 lr=ARMS[signature["arm"]], betas=tuple(self.config["betas"]))
        scheduler = t.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="max", factor=0.5,
            patience=max(0, self.config["lr_patience"] - 1), threshold=0.0, threshold_mode="abs",
            min_lr=self.config["min_learning_rate"]) if diagnostic else None
        directory.mkdir(parents=True, exist_ok=True)
        immutable(directory / "identity.json", signature)
        log = directory / "epochs.jsonl"
        rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        require([r["epoch"] for r in rows] == list(range(1, len(rows) + 1)), "epoch log is not contiguous")
        state = None
        if (directory / "last.pt").exists():
            state = self.train.checkpoint_load(directory / "last.pt", self.device)
            require(state["signature"] == signature, "resume signature differs")
            actual = state["epoch"] + 1
            # A crash between atomic checkpoint replacement and log append may
            # recover exactly its stored real event; never fabricate an epoch.
            require(len(rows) in (actual - 1, actual), "checkpoint/log gap is not one recoverable stored event")
            if len(rows) == actual - 1:
                record = state["epoch_record"]
                require(record["epoch"] == actual, "stored recovery epoch differs")
                self.train.event(log, record)
                immutable(directory / ("recovery_epoch_%02d.json" % actual),
                          {"record": record, "checkpoint_sha256": digest(directory / "last.pt"),
                           "recovered_stored_real_event_only": True})
                rows.append(record)
            require(rows[-1] == state["epoch_record"], "last actual log differs from checkpoint event")
            network.load_state_dict(state["model"], strict=True)
            optimizer.load_state_dict(state["optimizer"])
            if diagnostic:
                scheduler.load_state_dict(state["scheduler"])
            self.train.restore_rng(state["rng"])
        else:
            require(not rows, "epochs exist without resumable checkpoint")
        return network, optimizer, scheduler, state, rows

    def train_epoch(self, network, optimizer, indices, seed, epoch):
        network.train()
        losses, sections = [], 0
        for times in self.dataset.batches(indices, self.config["batch_cross_sections"],
                                          self.np.random.default_rng(seed + 10_000 * epoch)):
            x, industry, slots, labels, counts = self.dataset.batch(times, self.device, self.config)
            optimizer.zero_grad(set_to_none=True)
            scores = network(x, industry, slots)
            require(bool(self.torch.isfinite(scores).all()), "nonfinite training score cannot be silently masked")
            candidate, ignored = self.model_module.ic_losses(scores, labels, counts,
                self.config["min_stocks"], self.config["label_variance_floor"])
            require(ignored == 0, "preselected training cross section was silently skipped")
            loss = candidate[2]  # Registered label3; all4 labels have shared eligibility.
            require(bool(self.torch.isfinite(loss)), "nonfinite training loss")
            loss.backward()
            norm = self.torch.nn.utils.clip_grad_norm_(network.parameters(), self.config["gradient_clip"])
            require(bool(self.torch.isfinite(norm)), "nonfinite gradient")
            optimizer.step()
            require(all(bool(self.torch.isfinite(p).all()) for p in network.parameters()), "optimizer produced nonfinite model weight")
            losses.append((float(loss.detach()), len(times)))
            sections += len(times)
        require(sections == len(indices) and losses, "training epoch failed to visit each complete section once")
        return sum(value * count for value, count in losses) / sections, sections

    def checkpoint_state(self, signature, network, optimizer, epoch, record, **fields):
        return {"signature": signature, "model": network.state_dict(), "optimizer": optimizer.state_dict(),
                "epoch": epoch, "epoch_record": record, "rng": self.train.rng_state(),
                "groups": self.dataset.groups, "n_industries": self.dataset.n_industries, **fields}

    def diagnostic(self, fold, arm, seed):
        directory = self.output / "diagnostic" / fold["id"] / arm / ("seed_%s" % seed)
        fit, fit_report = self.indices(fold["fit"])
        inner, inner_report = self.indices(fold["inner"])
        signature = self.signature("diagnostic", arm, seed, fold=fold["id"],
                                   fit_period=list(fold["fit"]), inner_period=list(fold["inner"]))
        if (directory / "result.json").exists():
            result = self.checked_result(directory, signature, "best_inner.pt", "best_inner_checkpoint_sha256")
        else:
            network, optimizer, scheduler, state, rows = self.setup(signature, directory, True)
            best_ic, best_epoch, bad = (state["best_ic"], state["best_epoch"], state["bad_epochs"]) if state else (-math.inf, -1, 0)
            best_model = state["best_model"] if state else None
            best_records = state["best_records"] if state else None
            best_summary = state["best_summary"] if state else None
            for epoch in range(len(rows), self.config["epochs"]):
                if bad >= self.config["patience"]:
                    break
                started = time.monotonic()
                loss, sections = self.train_epoch(network, optimizer, fit, seed, epoch)
                summary, records = self.train.validate(network, self.dataset, inner, self.config, self.device)
                ic = summary["overall"]["ic"]
                require(type(ic) in (int, float) and math.isfinite(ic), "inner IC invalid")
                if ic > best_ic:
                    best_ic, best_epoch, bad = ic, epoch, 0
                    best_model = {k: v.detach().cpu().clone() for k, v in network.state_dict().items()}
                    best_records, best_summary = records, summary
                else:
                    bad += 1
                scheduler.step(ic)
                record = {"phase": "r2_diagnostic", "fold": fold["id"], "arm": arm, "seed": seed,
                          "epoch": epoch + 1, "loss_label3": loss, "trained_sections": sections,
                          "inner_y4_IC": ic, "best_inner_epoch": best_epoch + 1, "best_inner_IC": best_ic,
                          "bad_epochs": bad, "next_epoch_learning_rate": optimizer.param_groups[0]["lr"],
                          "elapsed_seconds": time.monotonic() - started}
                state = self.checkpoint_state(signature, network, optimizer, epoch, record,
                    scheduler=scheduler.state_dict(), best_ic=best_ic, best_epoch=best_epoch, bad_epochs=bad,
                    best_model=best_model, best_records=best_records, best_summary=best_summary)
                self.train.checkpoint_save(directory / "last.pt", state)
                self.train.event(directory / "epochs.jsonl", record)
                rows.append(record)
                self.assert_guards()
            require(state and best_model is not None, "diagnostic has no actual checkpoint")
            best_state = {"signature": signature, "model": best_model, "epoch": best_epoch,
                          "best_ic": best_ic, "groups": self.dataset.groups, "n_industries": self.dataset.n_industries}
            self.train.checkpoint_save(directory / "best_inner.pt", best_state)
            immutable(directory / "best_inner_sections.json", cleaned({"records": best_records}))
            immutable(directory / "best_inner_summary.json", cleaned(best_summary))
            result = {**signature, "signature": signature, "complete": True, "epochs_completed": len(rows),
                      "best_inner_epoch": best_epoch + 1, "best_inner_IC": best_ic,
                      "best_inner_checkpoint_sha256": digest(directory / "best_inner.pt"),
                      "inner_records_sha256": digest(directory / "best_inner_sections.json"),
                      "inner_summary_sha256": digest(directory / "best_inner_summary.json"),
                      "epoch_log_sha256": digest(directory / "epochs.jsonl"),
                      "fit_boundary": fit_report, "inner_boundary": inner_report}
            immutable(directory / "result.json", result)
        self.outer_once(directory, fold, arm, seed, result)
        return result

    def checked_result(self, directory, signature, checkpoint_name, checkpoint_hash_field):
        result = read(directory / "result.json")
        require(result.get("complete") is True and result["signature"] == signature, "completed result binding differs")
        require(digest(directory / checkpoint_name) == result[checkpoint_hash_field], "completed checkpoint changed")
        require(digest(directory / "epochs.jsonl") == result["epoch_log_sha256"], "completed epochs changed")
        if signature["stage"] == "diagnostic":
            require(digest(directory / "best_inner_sections.json") == result["inner_records_sha256"]
                    and digest(directory / "best_inner_summary.json") == result["inner_summary_sha256"], "inner evidence changed")
        return result

    def outer_once(self, directory, fold, arm, seed, inner_result):
        path = directory / "outer_result.json"
        expected = {**self.binding, "fold": fold["id"], "arm": arm, "seed": seed,
                    "checkpoint_role": "inner_best", "checkpoint_sha256": inner_result["best_inner_checkpoint_sha256"],
                    "inner_result_sha256": digest(directory / "result.json")}
        if path.exists():
            existing = read(path)
            require(existing.get("complete") is True and all(existing.get(k) == v for k, v in expected.items()),
                    "previous outer observation binding differs")
            return existing
        attempt = directory / "outer_attempt.json"
        require(not attempt.exists(), "outer already attempted; partial evidence preserved, no automatic repeat")
        indices, boundary = self.indices(fold["outer"])
        immutable(attempt, {**expected, "started_at_utc": utc(), "stage": "diagnostic_outer_attempt"})
        state = self.train.checkpoint_load(directory / "best_inner.pt", self.device)
        require(state["signature"] == inner_result["signature"], "outer inner-checkpoint signature differs")
        network = self.model_module.PlanGRU(self.dataset.groups, self.dataset.n_industries, "pv").to(self.device)
        network.load_state_dict(state["model"], strict=True)
        summary, records = self.train.validate(network, self.dataset, indices, self.config, self.device)
        self.assert_guards()
        result = {**expected, "stage": "diagnostic_outer", "complete": True, "records": cleaned(records),
                  "summary": cleaned(summary), "eligible_sections_expected": len(indices), "outer_boundary": boundary,
                  "outer_attempt_sha256": digest(attempt),
                  "finished_at_utc": utc(), "outer_is_virgin_OOS": False, "used_for_training_or_checkpoint": False}
        immutable(path, result)
        return result

    def collect_diagnostic_inputs(self):
        outer, inner, source_hashes = [], [], {}
        for p in plans():
            directory = self.output / "diagnostic" / p["fold"] / p["arm"] / ("seed_%s" % p["seed"])
            for name, destination in (("result.json", inner), ("outer_result.json", outer)):
                path = directory / name
                if path.exists():
                    doc = read(path)
                    require(all(doc.get(k) == v for k, v in self.binding.items())
                            and all(doc.get(k) == v for k, v in p.items()), "selector input identity differs")
                    if name == "result.json":
                        fold = next(f for f in FOLDS if f["id"] == p["fold"])
                        signature = self.signature("diagnostic", p["arm"], p["seed"], fold=fold["id"],
                            fit_period=list(fold["fit"]), inner_period=list(fold["inner"]))
                        self.checked_result(directory, signature, "best_inner.pt", "best_inner_checkpoint_sha256")
                    else:
                        result_path = directory / "result.json"
                        require(result_path.exists() and doc["inner_result_sha256"] == digest(result_path)
                                and doc["checkpoint_sha256"] == read(result_path)["best_inner_checkpoint_sha256"],
                                "outer evidence not bound to actual inner selection")
                        attempt = directory / "outer_attempt.json"
                        require(attempt.exists() and doc["outer_attempt_sha256"] == digest(attempt),
                                "actual once-only outer attempt missing/changed")
                        attempt_doc = read(attempt)
                        require(all(attempt_doc.get(k) == v for k, v in self.binding.items())
                                and all(attempt_doc.get(k) == v for k, v in p.items())
                                and attempt_doc.get("checkpoint_sha256") == doc["checkpoint_sha256"],
                                "outer attempt identity differs")
                    destination.append(doc)
                    source_hashes[str(path.relative_to(self.output))] = digest(path)
        return inner, outer, source_hashes

    def select(self):
        inner, outer, hashes = self.collect_diagnostic_inputs()
        require_completed_diagnostics(inner, outer)
        selected = {**self.binding, **select_lr(outer), "diagnostic_evidence_sha256": hashes}
        immutable(self.output / "selection.json", selected)
        return selected, inner

    def final_prerequisites(self):
        inner, outer, _ = self.collect_diagnostic_inputs()
        return require_completed_diagnostics(inner, outer)

    def fit_final(self, seed, selected, budget):
        self.final_prerequisites()
        arm = selected["selected_arm"]
        directory = self.output / "final" / ("seed_%s" % seed)
        signature = self.signature("final", arm, seed, fit_period=["2019-01-01", "2024-01-01"],
            fixed_epoch_budget=budget["epochs"], checkpoint_role="fixed_last_epoch",
            selection_sha256=digest(self.output / "selection.json"), budget_sha256=digest(self.output / "budget.json"))
        if (directory / "result.json").exists():
            return self.checked_result(directory, signature, "last.pt", "last_checkpoint_sha256")
        indices, boundary = self.indices(("2019-01-01", "2024-01-01"))
        network, optimizer, _, state, rows = self.setup(signature, directory, False)
        for epoch in range(len(rows), budget["epochs"]):
            started = time.monotonic()
            loss, sections = self.train_epoch(network, optimizer, indices, seed, epoch)
            record = {"phase": "r2_final", "seed": seed, "arm": arm, "epoch": epoch + 1,
                      "loss_label3": loss, "trained_sections": sections, "learning_rate": ARMS[arm],
                      "elapsed_seconds": time.monotonic() - started, "validation_read": False}
            require(optimizer.param_groups[0]["lr"] == ARMS[arm], "final constant LR changed")
            state = self.checkpoint_state(signature, network, optimizer, epoch, record)
            self.train.checkpoint_save(directory / "last.pt", state)
            self.train.event(directory / "epochs.jsonl", record)
            rows.append(record)
            self.assert_guards()
        require(len(rows) == budget["epochs"] and state is not None, "final did not finish its fixed budget")
        result = {**signature, "signature": signature, "complete": True, "epochs_completed": len(rows),
                  "last_checkpoint_sha256": digest(directory / "last.pt"),
                  "epoch_log_sha256": digest(directory / "epochs.jsonl"), "fit_boundary": boundary,
                  "2024_used_for_training_or_checkpoint_selection": False}
        immutable(directory / "result.json", result)
        return result

    def freeze(self):
        self.final_prerequisites()
        selected, inner = self.select()
        budget = {**self.binding, **final_budget(inner, selected["selected_arm"]),
                  "selection_sha256": digest(self.output / "selection.json")}
        immutable(self.output / "budget.json", budget)
        results = []
        for seed in SEEDS:
            path = self.output / "final" / ("seed_%s" % seed) / "result.json"
            require(path.exists(), "all five final fits must exist before new freeze")
            result = read(path)
            expected = self.signature("final", selected["selected_arm"], seed,
                fit_period=["2019-01-01", "2024-01-01"], fixed_epoch_budget=budget["epochs"],
                checkpoint_role="fixed_last_epoch", selection_sha256=digest(self.output / "selection.json"),
                budget_sha256=digest(self.output / "budget.json"))
            results.append(self.checked_result(path.parent, expected, "last.pt", "last_checkpoint_sha256"))
        hashes = {str((self.output / "final" / ("seed_%s" % s) / "last.pt").resolve()): r["last_checkpoint_sha256"]
                  for s, r in zip(SEEDS, results)}
        freeze = {**self.binding, "selection_sha256": digest(self.output / "selection.json"),
                  "budget_sha256": digest(self.output / "budget.json"), "checkpoint_hashes": hashes,
                  "checkpoint_role": "fixed_last_epoch", "seeds": list(SEEDS), "variant": "pv", "training_label": 3,
                  "historical_exposure_declared": True, "virgin_OOS_claim": False,
                  "future_ledger_status": "new_independent_freeze_required_no_old_predictions", "future_forecast_records": 0}
        freeze["freeze_id"] = sha(canonical(freeze))[:24]
        path = self.output / "freeze.json"
        if path.exists():
            existing = read(path)
            require(all(existing.get(k) == v for k, v in freeze.items())
                    and isinstance(existing.get("frozen_at_utc"), str), "new R2 freeze changed")
            return existing
        freeze["frozen_at_utc"] = utc()
        immutable(self.output / "freeze.json", freeze)
        return freeze

    def evaluate_once(self):
        frozen = self.freeze()
        checkpoints = list(frozen["checkpoint_hashes"])
        for name, interval in self.protocol["research_evaluation_after_new_freeze"]["periods"].items():
            directory = self.output / "research_evaluation" / name
            summary_path = directory / "summary.json"
            if summary_path.exists():
                result = read(summary_path)
                require(result.get("freeze_id") == frozen["freeze_id"] and result.get("complete") is True,
                        "prior research evaluation binding differs")
                for relative, value in result["outputs_sha256"].items():
                    require(digest(directory / relative) == value, "once-evaluated output changed")
                continue
            require(not (directory / "attempt.json").exists(), "research period already attempted; do not repeat partial evaluation")
            indices, boundary = self.indices(interval)
            directory.mkdir(parents=True, exist_ok=True)
            immutable(directory / "attempt.json", {"freeze_id": frozen["freeze_id"], "period": name,
                      "started_at_utc": utc(), "historical_exposure_declared": True})
            self.assert_guards()
            for p, h in frozen["checkpoint_hashes"].items():
                require(digest(p) == h, "new frozen final checkpoint changed")
            # Original predictor's ensemble standardization and eval mode are
            # reused. Metrics-only records avoid inheriting old CSV quality-flag
            # and low-count coverage export defects. No trading-return claim.
            seeds = {}
            records = self.train.prediction_records(self.dataset, indices, checkpoints, self.config,
                self.device, scores_csv=None, model_version=frozen["freeze_id"], seed_records_out=seeds)
            require(len(records) == len(indices), "research evaluation omitted eligible sections")
            immutable(directory / "sections.json", cleaned({"records": records, "seed_records": seeds}))
            result = {**self.binding, "freeze_id": frozen["freeze_id"], "period": name, "complete": True,
                      "summary": cleaned(self.evaluate.breakdown(records)),
                      "per_seed": {s: cleaned(self.evaluate.breakdown(v)) for s, v in seeds.items()},
                      "boundary": boundary, "historical_exposure_declared": True, "virgin_OOS_claim": False,
                      "used_for_selection_or_budget": False, "coverage_scope": "eligible mature sections only",
                      "all_four_slots_kept": True, "stock_score_csv_created": False,
                      "outputs_sha256": {"sections.json": digest(directory / "sections.json")}}
            self.assert_guards()
            immutable(summary_path, result)

    def run(self, stage):
        if stage in ("all", "diagnostics"):
            for fold in FOLDS:
                for arm in ARMS:
                    for seed in SEEDS:
                        self.diagnostic(fold, arm, seed)
        if stage in ("all", "select", "final", "freeze", "evaluate"):
            selected, inner = self.select()
            if stage in ("all", "final", "freeze", "evaluate"):
                self.final_prerequisites()
                budget = {**self.binding, **final_budget(inner, selected["selected_arm"]),
                          "selection_sha256": digest(self.output / "selection.json")}
                immutable(self.output / "budget.json", budget)
                if stage in ("all", "final"):
                    for seed in SEEDS:
                        self.fit_final(seed, selected, budget)
        if stage in ("all", "freeze", "evaluate"):
            self.freeze()
        if stage in ("all", "evaluate"):
            self.evaluate_once()
        self.assert_guards()
        print(json.dumps({"stage": stage, "status": "requested_stage_finished", "protocol_sha256": PROTOCOL_SHA256,
                          "fit_cap": 35, "IC07_guaranteed": False}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--stage", choices=("all", "diagnostics", "select", "final", "freeze", "evaluate"), default="all")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--expected-source-manifest-sha256")
    parser.add_argument("--causal-evidence-directory", type=Path)
    parser.add_argument("--expected-causal-receipt-sha256")
    parser.add_argument("--expected-causal-evidence-manifest-sha256")
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps(prepare_contract(), ensure_ascii=False))
        return
    required = ("root", "dataset", "output", "source_manifest", "expected_source_manifest_sha256",
                "causal_evidence_directory", "expected_causal_receipt_sha256", "expected_causal_evidence_manifest_sha256")
    require(all(getattr(args, name) is not None for name in required), "actual R2 execution requires all explicit material paths and reviewed pins")
    Runtime(args).run(args.stage)


if __name__ == "__main__":
    main()
