"""Staged R3:15 new PV-label4 fits, root finite15 admission, one decision, at most5 refits.

Without --execute only a literal prepared contract is printed. No old fit is
resumed. A failed stage preserves its exclusive attempt and cannot be retried.
Root must separately admit each stage; research periods follow a new freeze.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time
from types import MethodType

from .protocol import (BASELINE_SHA256, FOLDS, LR, PROTOCOL_SHA256, R2_BINDING,
                       REGISTRATION_SHA256, SEEDS, canonical, decide, final_budget,
                       load_protocol, plans, require, sha, verify_pair)
from .evidence import (SmallReader, baseline_roster, execution_gate, read_baseline_once,
                       source_guard, stage_admission, stat)


def utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def immutable(path, value):
    path = Path(path)
    blob = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode() + b"\n"
    if path.exists():
        require(path.read_bytes() == blob, "existing immutable R3 document differs")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(blob)


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    h = __import__("hashlib").sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(65536), b""):
            h.update(part)
    return h.hexdigest()


def cleaned(value):
    if isinstance(value, dict):
        return {str(k): cleaned(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [cleaned(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def prepare_contract():
    return {"status": "PREPARED_ONLY_NO_FILE_OR_MODEL_READ", "execution_allowed": False,
            "protocol_sha256": PROTOCOL_SHA256, "candidate_training_label": 4, "loss_index": 3,
            "evaluation_label": 4, "variant": "pv", "learning_rate": LR,
            "stages": ["diagnostics", "select", "final", "freeze", "evaluate"],
            "diagnostic_fits": 15, "baseline_new_fits_or_forward_calls": 0, "new_final_fits_maximum": 5,
            "new_fit_cap": 20, "actual15_completion_and_root_finite15_required_before_select": True,
            "no_automatic_resume_or_retry": True, "IC07_guaranteed": False, "virgin_OOS": False}


class Runtime:
    def __init__(self, args):
        self.args, self.root, self.output = args, args.root.resolve(), args.output.resolve()
        self.reader = SmallReader()
        require(Path(__file__).resolve() == self.root / "gru_research/r3_research/runner.py", "unsealed runner root")
        self.protocol = load_protocol(self.reader.read(args.protocol, PROTOCOL_SHA256))
        self.new_source = source_guard(self.reader, self.root, args.source_manifest,
                                       args.expected_source_manifest_sha256, self.protocol)
        self.meta, self.permission = execution_gate(self.reader, args, self.protocol, self.new_source)
        self.baseline = baseline_roster(self.reader, args.baseline_lineage, self.root)
        self.config = self.reader.json(self.root / "gru_research/config.json",
            self.protocol["same_configuration"]["original_config_sha256"])
        require(self.config == self.protocol["same_configuration"]["original_config"]
                and self.config["learning_rate"] == LR, "unchanged control configuration required")
        input_contract = {"meta_sha256": self.protocol["dataset_and_source_inheritance"]["meta_sha256"],
            "files_sha256": self.meta["files_sha256"], "sequence_length": 80, "all_four_label_eligibility": True,
            "period_indices_source_sha256": self.protocol["original27_source_sha256"]["gru_research/r2_research/folds.py"],
            "min_stocks": self.config["min_stocks"], "variance_floor": self.config["label_variance_floor"],
            "embargo_complete_days": 2, "all_actual_label_ends_before_cutoff": True}
        self.binding = {"protocol_sha256": PROTOCOL_SHA256,
            "new_source_manifest_sha256": self.new_source["manifest_sha256"],
            "root_label4_permission_sha256": args.expected_permission_sha256,
            "data_reuse_evidence_sha256": args.expected_data_reuse_evidence_sha256,
            "data_reuse_evidence_manifest_sha256": args.expected_data_reuse_evidence_manifest_sha256,
            "baseline_lineage_sha256": BASELINE_SHA256,
            "dataset_fingerprint": self.protocol["dataset_and_source_inheritance"]["canonical_metadata_fingerprint"],
            "input_and_common_four_label_cohort_contract_sha256": sha(canonical(input_contract)),
            "training_family": "pv_label4", "training_label": 4, "loss_index": 3}
        expected_output = self.root / self.protocol["namespaces"]["future_run"]
        require(self.output == expected_output.resolve() and self.output.parent == self.root / "outputs"
                and not self.output.is_symlink(), "exact separate new R3 run required")
        self.stage_gate = stage_admission(self.reader, args, self.binding)
        if args.stage == "diagnostics":
            self.output.mkdir(parents=True, exist_ok=False)
            immutable(self.output / "run_identity.json", self.binding)
            immutable(self.output / "input_contract.json", input_contract)
        else:
            identity = self.reader.json(self.output / "run_identity.json", sha(
                json.dumps(self.binding, ensure_ascii=False, indent=2, allow_nan=False).encode() + b"\n"))
            require(identity == self.binding, "original new-run identity changed")
        self.device, self._runtime_ready = args.device, False
        # Imports happen only after exact original27/new-source and permission checks.
        from gru_research.r2_research.runner import Runtime as OriginalRuntime
        require(Path(__import__(OriginalRuntime.__module__, fromlist=["__file__"]).__file__).resolve()
                == self.root / "gru_research/r2_research/runner.py", "imported original runner differs")
        for name in ("indices", "setup", "checkpoint_state", "checked_result", "outer_once", "evaluate_once"):
            setattr(self, name, MethodType(getattr(OriginalRuntime, name), self))
        self.attempt_path = self.output / "stage_attempts" / (args.stage + ".json")
        require(not self.attempt_path.exists(), "stage already attempted; preserve partial evidence, no automatic retry")
        immutable(self.attempt_path, {**self.binding, "stage": args.stage, "started_at_utc": utc(),
            "pid": os.getpid(), "root_stage_admission_sha256": args.expected_stage_admission_sha256,
            "automatic_retry": False})

    def assert_guards(self):
        # Baseline material bodies are cached once; later checks are explicitly STAT checks.
        self.reader.unchanged_stats()
        require(all(stat(p["path"]) == p for p in self.permission["remote_consumed_input_stat_pins"].values()),
                "remote realized input STAT changed during the stage")

    def ensure_runtime(self):
        if self._runtime_ready:
            return
        require(self.device == "cuda" and os.environ.get("SLURM_JOB_ID", "").isascii()
                and os.environ.get("SLURM_JOB_ID", "").isdigit()
                and int(os.environ["SLURM_JOB_ID"]) > 0
                and os.environ.get("SLURMD_NODENAME") == "scrp-node-8"
                and bool(os.environ.get("CUDA_VISIBLE_DEVICES")), "actual owned original-node GPU allocation required")
        require(all(os.environ.get(k) == v for k, v in {"OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4",
            "OPENBLAS_NUM_THREADS": "4", "CUBLAS_WORKSPACE_CONFIG": ":4096:8"}.items()), "original backend environment differs")
        import numpy as np
        import torch
        from gru_research import train, model, evaluate
        from gru_research.r2_research.folds import period_indices
        require(torch.cuda.is_available(), "no automatic CPU backend fallback")
        for module in (train, model, evaluate):
            require(Path(module.__file__).resolve() == self.root / "gru_research" / (module.__name__.split(".")[-1] + ".py"), "unsealed original Torch module")
        self.np, self.torch, self.train, self.model_module, self.evaluate = np, torch, train, model, evaluate
        self.period_indices = period_indices
        self.dataset = train.PanelDataset(self.args.dataset)
        require(self.dataset.fingerprint == self.binding["dataset_fingerprint"], "runtime dense panel identity differs")
        self._runtime_ready = True

    def signature(self, stage, arm, seed, **fields):
        require(arm == "control" and seed in SEEDS, "no alternative learning rate or seed")
        return {**self.binding, "config": copy.deepcopy(self.config), "seed": seed, "label": 4,
                "variant": "pv", "stage": stage, "arm": "control", **fields}

    def fresh_setup(self, signature, directory, diagnostic):
        require(not directory.exists(), "existing R3 fit is never implicitly resumed")
        directory.mkdir(parents=True, exist_ok=False)
        immutable(directory / "fit_attempt.json", {"signature": signature, "started_at_utc": utc(),
            "new_fit": True, "automatic_resume": False})
        network, optimizer, scheduler, state, rows = self.setup(signature, directory, diagnostic)
        require(state is None and rows == [], "new fit unexpectedly read a resumable state")
        return network, optimizer, scheduler

    def train_epoch(self, network, optimizer, indices, seed, epoch):
        network.train()
        losses, sections = [], 0
        for times in self.dataset.batches(indices, self.config["batch_cross_sections"],
                                          self.np.random.default_rng(seed + 10_000 * epoch)):
            x, industry, slots, labels, counts = self.dataset.batch(times, self.device, self.config)
            require(labels.ndim == 2 and labels.shape[1] == 4 and labels.dtype == self.torch.float32,
                    "label4 training requires shared4-label FP32 targets")
            optimizer.zero_grad(set_to_none=True)
            scores = network(x, industry, slots)
            require(bool(self.torch.isfinite(scores).all()), "nonfinite training scores")
            candidate, ignored = self.model_module.ic_losses(scores, labels, counts,
                self.config["min_stocks"], self.config["label_variance_floor"])
            require(ignored == 0 and candidate.shape == (4,), "shared4-label section/loss shape differs")
            loss = candidate[3]  # The sole predictive change from R2 control: training label4.
            require(bool(self.torch.isfinite(loss)), "nonfinite training loss")
            loss.backward()
            norm = self.torch.nn.utils.clip_grad_norm_(network.parameters(), self.config["gradient_clip"])
            require(bool(self.torch.isfinite(norm)), "nonfinite gradient")
            optimizer.step()
            require(all(bool(self.torch.isfinite(p).all()) for p in network.parameters()), "nonfinite optimized model")
            losses.append((float(loss.detach()), len(times)))
            sections += len(times)
        require(sections == len(indices) and losses, "epoch did not visit every eligible section once")
        return sum(value * count for value, count in losses) / sections, sections

    def diagnostic(self, fold, seed):
        self.ensure_runtime()
        directory = self.output / "diagnostic" / fold["id"] / "pv_label4" / ("seed_%s" % seed)
        fit, fit_report = self.indices(fold["fit"])
        inner, inner_report = self.indices(fold["inner"])
        signature = self.signature("diagnostic", "control", seed, fold=fold["id"],
            fit_period=list(fold["fit"]), inner_period=list(fold["inner"]))
        network, optimizer, scheduler = self.fresh_setup(signature, directory, True)
        rows, best_ic, best_epoch, bad = [], -math.inf, -1, 0
        best_model = best_records = best_summary = state = None
        for epoch in range(self.config["epochs"]):
            if bad >= self.config["patience"]:
                break
            started = time.monotonic()
            loss, sections = self.train_epoch(network, optimizer, fit, seed, epoch)
            summary, records = self.train.validate(network, self.dataset, inner, self.config, self.device)
            ic = summary["overall"]["ic"]
            require(type(ic) in (int, float) and math.isfinite(ic), "inner y4 IC invalid")
            if ic > best_ic:
                best_ic, best_epoch, bad = ic, epoch, 0
                best_model = {k: v.detach().cpu().clone() for k, v in network.state_dict().items()}
                best_records, best_summary = records, summary
            else:
                bad += 1
            scheduler.step(ic)
            record = {"phase": "r3_diagnostic", "fold": fold["id"], "arm": "control", "seed": seed,
                "epoch": epoch + 1, "loss_label4": loss, "trained_sections": sections,
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
        require(state is not None and best_model is not None, "no actual diagnostic checkpoint")
        self.train.checkpoint_save(directory / "best_inner.pt", {"signature": signature, "model": best_model,
            "epoch": best_epoch, "best_ic": best_ic, "groups": self.dataset.groups, "n_industries": self.dataset.n_industries})
        immutable(directory / "best_inner_sections.json", cleaned({"records": best_records}))
        immutable(directory / "best_inner_summary.json", cleaned(best_summary))
        result = {**signature, "signature": signature, "complete": True, "epochs_completed": len(rows),
            "best_inner_epoch": best_epoch + 1, "best_inner_IC": best_ic,
            "best_inner_checkpoint_sha256": digest(directory / "best_inner.pt"),
            "inner_records_sha256": digest(directory / "best_inner_sections.json"),
            "inner_summary_sha256": digest(directory / "best_inner_summary.json"),
            "epoch_log_sha256": digest(directory / "epochs.jsonl"), "fit_boundary": fit_report, "inner_boundary": inner_report}
        immutable(directory / "result.json", result)
        self.outer_once(directory, fold, "control", seed, result)

    def candidate_documents(self):
        wanted = {str(Path("diagnostic") / f["id"] / "pv_label4" / ("seed_%s" % s) / "result.json")
                  for f in FOLDS for s in SEEDS}
        expected_inner = self.stage_gate.get("diagnostic_result_hashes")
        expected_outer = self.stage_gate.get("outer_result_hashes")
        require(isinstance(expected_inner, dict) and set(expected_inner) == wanted
                and isinstance(expected_outer, dict)
                and set(expected_outer) == {p.replace("/result.json", "/outer_result.json") for p in wanted},
                "root actual candidate15 result/outer map missing")
        cps = self.stage_gate.get("candidate_best_checkpoint_hashes")
        require(isinstance(cps, dict) and set(cps) == {p.replace("/result.json", "/best_inner.pt") for p in wanted}, "actual finite15 CP map missing")
        output = {}
        for plan in plans():
            rel = str(Path("diagnostic") / plan["fold"] / "pv_label4" / ("seed_%s" % plan["seed"]))
            inner = self.reader.json(self.output / rel / "result.json", expected_inner[rel + "/result.json"])
            outer = self.reader.json(self.output / rel / "outer_result.json", expected_outer[rel + "/outer_result.json"])
            identity_sha = sha(json.dumps(inner["signature"], ensure_ascii=False, indent=2, allow_nan=False).encode() + b"\n")
            hashes = {"result.json": expected_inner[rel + "/result.json"],
                "outer_result.json": expected_outer[rel + "/outer_result.json"], "identity.json": identity_sha,
                "epochs.jsonl": inner["epoch_log_sha256"], "best_inner_sections.json": inner["inner_records_sha256"],
                "best_inner_summary.json": inner["inner_summary_sha256"], "outer_attempt.json": outer["outer_attempt_sha256"]}
            docs = {"result.json": inner, "outer_result.json": outer, "_hashes": hashes}
            for name in ("identity.json", "epochs.jsonl", "best_inner_sections.json", "best_inner_summary.json", "outer_attempt.json"):
                body = self.reader.read(self.output / rel / name, hashes[name])
                docs[name] = [json.loads(x) for x in body.splitlines()] if name == "epochs.jsonl" else json.loads(body)
            require(inner["best_inner_checkpoint_sha256"] == cps[rel + "/best_inner.pt"], "candidate CPU finite proof CP hash differs")
            output[plan["fold"], plan["seed"]] = docs
        return output

    def select_once(self):
        candidate = self.candidate_documents()
        baseline = read_baseline_once(self.reader, self.baseline)
        for fold in FOLDS:
            for seed in SEEDS:
                key = fold["id"], seed
                c = verify_pair(candidate[key], self.binding, fold, seed, 4, self.config)
                b = verify_pair(baseline[key], R2_BINDING, fold, seed, 3, self.config)
                require(c == b, "matched original and candidate actual population differs")
                for field in ("fit_boundary", "inner_boundary"):
                    require(candidate[key]["result.json"][field] == baseline[key]["result.json"][field], "actual fit/inner purge boundary differs")
                require(candidate[key]["outer_result.json"]["outer_boundary"] == baseline[key]["outer_result.json"]["outer_boundary"], "actual outer purge boundary differs")
        selection = {**self.binding, **decide({k: d["outer_result.json"] for k, d in candidate.items()},
                                             {k: d["outer_result.json"] for k, d in baseline.items()}),
            "candidate_evidence_sha256": {str(Path("diagnostic") / f / "pv_label4" / ("seed_%s" % s) / n): h
                for (f, s), doc in candidate.items() for n, h in doc["_hashes"].items()},
            "baseline_control15_descriptor_sha256": BASELINE_SHA256,
            "actual_complete_candidate_pairs": 15, "actual_reused_baseline_pairs": 15,
            "baseline_forward_or_fit_calls": 0, "root_candidate15_stage_admission_sha256": self.args.expected_stage_admission_sha256,
            "historical_exposure_declared": True, "full_PLAN_complete": False}
        immutable(self.output / "selection.json", selection)
        if selection["candidate_selected"]:
            budget = {**self.binding, **final_budget({k: d["result.json"] for k, d in candidate.items()}),
                      "selection_sha256": digest(self.output / "selection.json")}
            immutable(self.output / "budget.json", budget)
        else:
            immutable(self.output / "retained_R2_freeze.json", {**self.binding,
                "selection_sha256": digest(self.output / "selection.json"), "new_final_fits": 0,
                "new_R3_freeze_created": False, "R2_retained": self.baseline["retained_R2_frozen_five"],
                "research_2024_2025_2026_evaluations": 0})

    def winning_choice(self):
        selected = self.reader.json(self.output / "selection.json", self.stage_gate["selection_sha256"])
        budget = self.reader.json(self.output / "budget.json", self.stage_gate["budget_sha256"])
        require(selected.get("candidate_selected") is True and selected.get("statistical_comparison_valid") is True
                and all(selected.get(k) == v for k, v in self.binding.items())
                and all(budget.get(k) == v for k, v in self.binding.items())
                and budget.get("selection_sha256") == self.stage_gate["selection_sha256"], "actual winning decision/budget binding differs")
        require(selected.get("actual_complete_candidate_pairs") == 15
                and selected.get("actual_reused_baseline_pairs") == 15 and selected.get("new_final_fits") == 5
                and selected.get("conditions") == {"overall_gain_at_least_001": True,
                    "three_fold_gains_strictly_positive": True, "paired_99pct_CI_lower_strictly_positive": True}, "conditional final criterion gate not passed")
        require(type(selected.get("overall_gain")) in (int, float) and selected["overall_gain"] >= .001
                and len(selected.get("fold_gains", [])) == 3 and all(x > 0 for x in selected["fold_gains"])
                and selected.get("paired_difference_ci99", [None])[0] > 0,
                "actual registered gain/fold/99pct interval criteria differ")
        epochs = budget.get("inner_best_epochs")
        require(isinstance(epochs, list) and len(epochs) == 15
                and all(type(e) is int and 1 <= e <= self.config["epochs"] for e in epochs)
                and budget.get("epochs") == sorted(epochs)[7] and budget.get("added_one_to_result_epoch") is False,
                "candidate15 median fixed budget differs")
        return selected, budget

    def fit_final(self, seed, budget):
        self.ensure_runtime()
        directory = self.output / "final" / ("seed_%s" % seed)
        signature = self.signature("final", "control", seed, fit_period=["2019-01-01", "2024-01-01"],
            fixed_epoch_budget=budget["epochs"], checkpoint_role="fixed_last_epoch",
            selection_sha256=self.stage_gate["selection_sha256"], budget_sha256=self.stage_gate["budget_sha256"])
        indices, boundary = self.indices(("2019-01-01", "2024-01-01"))
        network, optimizer, _ = self.fresh_setup(signature, directory, False)
        rows, state = [], None
        for epoch in range(budget["epochs"]):
            started = time.monotonic()
            loss, sections = self.train_epoch(network, optimizer, indices, seed, epoch)
            record = {"phase": "r3_final", "seed": seed, "arm": "control", "epoch": epoch + 1,
                "loss_label4": loss, "trained_sections": sections, "learning_rate": LR,
                "elapsed_seconds": time.monotonic() - started, "validation_read": False}
            require(optimizer.param_groups[0]["lr"] == LR, "constant final learning rate changed")
            state = self.checkpoint_state(signature, network, optimizer, epoch, record)
            self.train.checkpoint_save(directory / "last.pt", state)
            self.train.event(directory / "epochs.jsonl", record)
            rows.append(record)
            self.assert_guards()
        require(len(rows) == budget["epochs"] and state is not None, "final fixed budget incomplete")
        immutable(directory / "result.json", {**signature, "signature": signature, "complete": True,
            "epochs_completed": len(rows), "last_checkpoint_sha256": digest(directory / "last.pt"),
            "epoch_log_sha256": digest(directory / "epochs.jsonl"), "fit_boundary": boundary,
            "2024_used_for_training_or_checkpoint_selection": False})

    def freeze(self):
        selected, budget = self.winning_choice()
        require(self.args.stage in ("freeze", "evaluate"), "freeze requires separate root finite5 stage admission")
        hashes = {}
        expected_cps = self.stage_gate.get("final_checkpoint_hashes")
        require(isinstance(expected_cps, dict) and set(expected_cps) == {"final/seed_%s/last.pt" % s for s in SEEDS}, "root actual final5 CP roster missing")
        for seed in SEEDS:
            directory = self.output / "final" / ("seed_%s" % seed)
            signature = self.signature("final", "control", seed, fit_period=["2019-01-01", "2024-01-01"],
                fixed_epoch_budget=budget["epochs"], checkpoint_role="fixed_last_epoch",
                selection_sha256=self.stage_gate["selection_sha256"], budget_sha256=self.stage_gate["budget_sha256"])
            result = self.checked_result(directory, signature, "last.pt", "last_checkpoint_sha256")
            require(result["epochs_completed"] == budget["epochs"]
                    and result["last_checkpoint_sha256"] == expected_cps["final/seed_%s/last.pt" % seed], "final actual budget/finite CP identity differs")
            hashes[str((directory / "last.pt").resolve())] = result["last_checkpoint_sha256"]
        frozen = {**self.binding, "selection_sha256": self.stage_gate["selection_sha256"],
            "budget_sha256": self.stage_gate["budget_sha256"], "checkpoint_hashes": hashes,
            "checkpoint_role": "fixed_last_epoch", "seeds": list(SEEDS), "variant": "pv", "training_label": 4,
            "historical_exposure_declared": True, "virgin_OOS_claim": False,
            "future_ledger_status": "new_independent_freeze_required_no_old_predictions", "future_forecast_records": 0,
            "full_PLAN_complete": False, "economic_acceptance": False}
        frozen["freeze_id"] = sha(canonical(frozen))[:24]
        path = self.output / "freeze.json"
        if path.exists():
            existing = read(path)
            require(all(existing.get(k) == v for k, v in frozen.items())
                    and isinstance(existing.get("frozen_at_utc"), str), "existing R3 freeze differs")
            return existing
        require(self.args.stage == "freeze", "evaluate cannot create a missing freeze")
        frozen["frozen_at_utc"] = utc()
        immutable(path, frozen)
        return frozen

    def run(self):
        stage = self.args.stage
        if stage == "diagnostics":
            for fold in FOLDS:
                for seed in SEEDS:
                    self.diagnostic(fold, seed)
        elif stage == "select":
            self.select_once()
        elif stage == "final":
            _, budget = self.winning_choice()
            for seed in SEEDS:
                self.fit_final(seed, budget)
        elif stage == "freeze":
            self.freeze()
        elif stage == "evaluate":
            frozen = self.freeze()
            require(digest(self.output / "freeze.json") == self.stage_gate.get("freeze_sha256"), "root admitted actual freeze SHA required")
            self.ensure_runtime()
            # The reused method expects this original key; periods are unchanged.
            self.protocol = {**self.protocol,
                "research_evaluation_after_new_freeze": self.protocol["after_new_freeze_research_evaluation"]}
            self.evaluate_once()
        self.assert_guards()
        source_guard(self.reader, self.root, self.args.source_manifest,
            self.args.expected_source_manifest_sha256, load_protocol(self.reader.read(self.args.protocol, PROTOCOL_SHA256)), final=True)
        immutable(self.output / "stage_results" / (stage + ".json"), {**self.binding, "stage": stage,
            "status": "requested_stage_finished", "finished_at_utc": utc(),
            "attempt_sha256": digest(self.attempt_path), "small_reads": self.reader.observations,
            "small_physical_bytes_read": self.reader.bytes, "baseline_CP_loads_or_forwards_or_fits": 0,
            "automatic_retry": False, "full_PLAN_complete": False, "virgin_OOS": False})
        print(json.dumps({"stage": stage, "status": "requested_stage_finished", "fit_cap": 20,
            "IC07_guaranteed": False, "protocol_sha256": PROTOCOL_SHA256}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--stage", choices=("diagnostics", "select", "final", "freeze", "evaluate"), default="diagnostics")
    for name in ("root", "dataset", "output", "protocol", "source-manifest", "registration", "baseline-lineage",
                 "permission", "data-reuse-evidence-directory", "stage-admission"):
        parser.add_argument("--" + name, type=Path)
    for name in ("expected-source-manifest-sha256", "expected-permission-sha256",
                 "expected-data-reuse-evidence-sha256", "expected-data-reuse-evidence-manifest-sha256",
                 "expected-stage-admission-sha256"):
        parser.add_argument("--" + name)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps(prepare_contract(), ensure_ascii=False))
        return
    required = ("root", "dataset", "output", "protocol", "source_manifest", "registration", "baseline_lineage",
        "permission", "data_reuse_evidence_directory", "expected_source_manifest_sha256", "expected_permission_sha256",
        "expected_data_reuse_evidence_sha256", "expected_data_reuse_evidence_manifest_sha256")
    require(all(getattr(args, n) is not None for n in required), "all explicit reviewed R3 materials required")
    if args.stage != "diagnostics":
        require(args.stage_admission is not None and args.expected_stage_admission_sha256 is not None,
                "actual root completed-stage admission required")
    runtime = None
    try:
        runtime = Runtime(args)
        runtime.run()
    except BaseException as exc:
        if runtime is not None and runtime.attempt_path.exists():
            path = runtime.output / "stage_failures" / (args.stage + ".json")
            if not path.exists():
                immutable(path, {"stage": args.stage, "status": "FAILED_PRESERVED_NO_RETRY", "finished_at_utc": utc(),
                    "error_type": type(exc).__name__, "error": str(exc), "small_reads": runtime.reader.observations,
                    "small_physical_bytes_read": runtime.reader.bytes, "automatic_retry": False,
                    "actual_stage_completion": False, "full_PLAN_complete": False})
        raise


if __name__ == "__main__":
    main()
