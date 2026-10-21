"""Read-only, additive evidence inspection for the sealed V4 research replay.

No engine imports, inference, market reads, networking or legacy manifest writes.
An explicit packet and a separately reviewed wrapper source hash are required.
The wrapper must capture actual stdout/exit and whole-run before/after hashes.
Per-run guard execution is source-bound evidence, not an observed phase hash map.
Collection of 36 accounts never certifies point net returns or the full PLAN.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path
import re

MODELS = ("fixed_label_1", "fixed_label_2", "fixed_label_3", "fixed_label_4",
          "blo_selected", "selected_full", "pv", "pv_factors", "pv_fund")
SCENARIOS = ("primary", "stress")
BASES = ("pretax", "tax_upper_reserve")
SPLITS = ("test_2025", "recent_2026")
WINDOWS = {
    "whole_observed": ("2025-01-02", "2026-09-23"),
    "calendar_2025_observed": ("2025-01-02", "2025-12-31"),
    "calendar_2026_through_sep23": ("2025-12-31", "2026-09-23"),
}
PINNED = {
    "protocol": "b288c7c9d1b5db777ad2bd3519415309123b1acee9adce55639bcc7b4c6188c9",
    "source_seal": "34c39255a5cd055cf233d594d03935841d6b99ae1f943dbb2e2251eab41fa20c",
    "driver_receipt": "e5998c9dfa8eab4be0b0849f475f8e736581d53ac75f24925c3529c933039996",
}
EXPORT_SOURCE_SHA = "8ad1c98b87aa1f39e6a474b101483a633bea0d6d8aa4c9858565d7c0fe2bee18"
BOUND_SCOPE = "SAME_SIMULATED_RESERVE_TRADING_PATH_NOT_ALL_UNKNOWN_TAX_CASH_FEEDBACK_PATHS"
EXECUTION_SCOPE = "ASSUMED_FIVE_MINUTE_RAW_OPEN_TWAP_WITH_PRIOR_VOLUME_CAP_NOT_OBSERVED_EXECUTABLE_DEPTH"
HASH = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_RUNS = tuple((m, s, b) for m in MODELS for s in SCENARIOS for b in BASES)
MAX_JSON_BYTES = 128 * 1024 * 1024


class EvidencePending(Exception):
    """A required original artifact has not arrived; this is not a pass."""


class EvidenceRejected(ValueError):
    """Existing artifacts are malformed, contradictory or have changed bytes."""


def require(condition, message):
    if not condition:
        raise EvidenceRejected(message)


def digest(value):
    require(isinstance(value, str) and HASH.fullmatch(value) is not None, "invalid SHA256")
    return value


def money(value):
    require(value is not None and not isinstance(value, bool), "missing/non-money amount")
    try:
        answer = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise EvidenceRejected("invalid decimal amount") from error
    require(answer.is_finite(), "nonfinite amount")
    return answer


def finite_ic(value):
    """Match frozen train/driver float selection; bool/string are not numeric IC."""
    require(type(value) in (int, float), "finite numeric mean_ic required; bool/string rejected")
    try:
        answer = float(value)
    except (OverflowError, ValueError) as error:
        raise EvidenceRejected("nonfinite numeric mean_ic") from error
    require(math.isfinite(answer), "nonfinite numeric mean_ic")
    return answer


def exact_day(value):
    require(isinstance(value, str), "date string required")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise EvidenceRejected("invalid ISO session date") from error
    require(parsed.isoformat() == value, "canonical date required")
    return value


def _pairs(items):
    answer = {}
    for key, value in items:
        require(key not in answer, "duplicate JSON key: " + key)
        answer[key] = value
    return answer


def strict_json(raw):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda v: (_ for _ in ()).throw(EvidenceRejected("nonfinite JSON")))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise EvidenceRejected("invalid JSON bytes") from error


def hash_file(path):
    h = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                h.update(block)
    except FileNotFoundError as error:
        raise EvidencePending("missing artifact: " + str(path)) from error
    return h.hexdigest()


def mapping(value):
    require(isinstance(value, dict) and bool(value), "nonempty actual hash map required")
    for name, expected in value.items():
        require(isinstance(name, str) and bool(name), "empty hash-map path")
        digest(expected)
    return value


def merge_maps(*maps):
    answer = {}
    for current in maps:
        for name, expected in mapping(current).items():
            require(name not in answer or answer[name] == expected, "conflicting sealed path hash: " + name)
            answer[name] = expected
    return answer


def false_claims(value):
    """Reject promotion of a scenario/bound into any existing completion gate."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"economic_target_accepted", "all_PLAN_acceptance_complete",
                       "prospective60session_acceptance_complete", "point_net_target_accepted",
                       "net_excess_return_point_acceptance", "point_net_improvement_accepted",
                       "net_NAV_contract_compatible", "full_PLAN_complete"}:
                require(child is False, "forbidden completion claim: " + key)
            if key in {"point_net_excess_return", "point_net_return"}:
                require(child is None, "point net return is outside this contract")
            false_claims(child)
    elif isinstance(value, list):
        for child in value:
            false_claims(child)


def contract():
    return {
        "schema_version": 1, "status": "prepared_only",
        "accepted_packet_kind": "ACTUAL_V4_RUN_PACKET",
        "required_artifacts": ["freeze", "protocol", "source_seal", "driver_receipt", "input_manifest",
                               "comparison", "calendar", "tail_calendar", "benchmark", "score_export_manifest",
                               "stdout", "wrapper_receipt"],
        "artifact_reference": {"path": "explicit actual file path", "sha256": "actual SHA256",
                               "identity_path": "optional original sealed path after byte-identical relocation"},
        "runs": [{"model": m, "scenario": s, "basis": b, "replay": "artifact reference",
                  "summary": "artifact reference"} for m, s, b in EXPECTED_RUNS],
        "score_export_receipts": [s + "/" + m for s in SPLITS for m in MODELS],
        "score_csvs": "18 keys each with raw and corrected artifact references; byte hashes only, no score parsing",
        "model_score_coverage": list(MODELS),
        "wrapper_contract": {
            "schema_version": 1, "evidence_kind": "ACTUAL_READONLY_V4_EXECUTION_CAPTURE",
            "wrapper_source_sha256": "separately reviewed, supplied out of band to inspection",
            "started_at_utc": "actual UTC time", "finished_at_utc": "actual UTC time",
            "before_captured_at_utc": "actual earlier UTC time", "after_captured_at_utc": "actual later UTC time",
            "before_hashes": "actual full merged seal/minute/driver/manifest map",
            "after_hashes": "same independently measured full map",
            "stdout_sha256": "original stdout bytes", "comparison_sha256": "actual output SHA256",
            "exit_code": 0, "stdout_captured_verbatim": True, "actual_hashes_measured": True,
            "synthetic": False,
        },
        "collection_state_is_not_economic_acceptance": True,
        "per_run_phase_map_directly_observed": False,
        "raw_TWAP_prices_independently_reconstructed": False,
        "point_net_excess_return": None, "all_PLAN_acceptance_complete": False,
        "legacy_manifest_generated": False,
    }


class _Inspector:
    def __init__(self, packet_path, approved_wrapper_source_sha256, *, fixture=False):
        self.base = Path(packet_path).resolve().parent
        self.packet_path = Path(packet_path).resolve()
        self.wrapper_sha = digest(approved_wrapper_source_sha256)
        self.fixture = fixture
        self.bound = {}
        if not self.packet_path.is_file():
            raise EvidencePending("missing explicit evidence packet: " + str(self.packet_path))
        require(self.packet_path.stat().st_size <= MAX_JSON_BYTES, "bounded JSON packet required")
        self.packet = strict_json(self.packet_path.read_bytes())
        require(self.packet.get("schema_version") == 1, "packet schema mismatch")
        require(self.packet.get("evidence_kind") == ("SYNTHETIC_V4_FIXTURE" if fixture else "ACTUAL_V4_RUN_PACKET"),
                "synthetic/unrecognized packet is not real evidence")

    def path(self, ref):
        require(isinstance(ref, dict) and isinstance(ref.get("path"), str) and ref["path"], "explicit artifact path required")
        path = Path(ref["path"])
        return (path if path.is_absolute() else self.base / path).resolve()

    def bind(self, ref, *, read=False):
        path = self.path(ref)
        expected = digest(ref.get("sha256"))
        actual = hash_file(path)
        require(actual == expected, "artifact hash mismatch: " + str(path))
        identity = ref.get("identity_path", str(path))
        require(isinstance(identity, str) and identity, "missing original artifact identity")
        if identity in self.bound:
            require(self.bound[identity]["sha256"] == actual and self.bound[identity]["path"] == str(path),
                    "ambiguous relocated artifact identity")
        self.bound[identity] = {"path": str(path), "sha256": actual, "bytes": path.stat().st_size}
        if read:
            require(path.stat().st_size <= MAX_JSON_BYTES, "JSON evidence exceeds bounded reader")
            raw = path.read_bytes()
            require(hashlib.sha256(raw).hexdigest() == actual and hash_file(path) == actual,
                    "evidence changed while read")
            return strict_json(raw)
        return actual

    def artifact(self, name):
        ref = self.packet.get("artifacts", {}).get(name)
        if ref is None:
            raise EvidencePending("missing artifact reference: " + name)
        return ref

    def original(self, ref):
        return ref.get("identity_path", str(self.path(ref)))

    def check_freeze(self, frozen, manifest):
        identity = hashlib.sha256(json.dumps({"data": frozen["dataset_fingerprint"], "config": frozen["config"],
            "selection": frozen["selection"], "hashes": frozen["checkpoint_hashes"]}, sort_keys=True).encode()).hexdigest()[:20]
        require(identity == frozen["freeze_id"] == manifest["freeze_id"], "freeze identity mismatch")
        require(frozen.get("no_test_driven_tuning") is True and frozen["config"]["seeds"] == [11, 22, 33, 44, 55],
                "original fixed-seed freeze required")
        require(set(frozen["evaluation_models"]) == set(MODELS), "nine models required")
        select = frozen["selection"]
        require(select["validation_year"] == 2024 and type(select["minimum_gain"]) in (int, float) and
                select["minimum_gain"] == .001 and math.isfinite(select["minimum_gain"]), "2024-only selection rule changed")
        alts = select["alternatives"]
        require(set(alts) == {"fixed_1", "fixed_2", "fixed_3", "fixed_4", "blo"}, "selection alternatives changed")
        mean_ics = {k: finite_ic(value["mean_ic"]) for k, value in alts.items()}
        best = max(alts, key=lambda k: mean_ics[k])
        # Exact Python float subtraction and strict <0.001 preserve frozen
        # train.py/driver behavior at representational threshold boundaries.
        method = "fixed_4" if mean_ics[best] - mean_ics["fixed_4"] < .001 else best
        label = select["blo_label"] if method == "blo" else int(method[-1])
        require(select["selected_method"] == method and select["selected_label"] == label and 1 <= int(select["blo_label"]) <= 4,
                "2024 selected identity changed")
        alias = "blo_selected" if method == "blo" else "fixed_label_" + str(label)
        require(frozen["evaluation_models"]["selected_full"] == frozen["evaluation_models"][alias], "selected_full alias changed")
        used = set()
        for model in MODELS:
            paths = frozen["evaluation_models"][model]
            require(len(paths) == len(set(paths)) == 5, "five unique seed weights required")
            for seed, name in zip(frozen["config"]["seeds"], paths):
                require("/seed_" + str(seed) + "/best.pt" in name, "seed-path mismatch")
                digest(frozen["checkpoint_hashes"][name]); used.add(name)
        require(used == set(frozen["checkpoint_hashes"]) and len(used) == 40, "40 distinct actual weights required")
        local_weights = {p: sha for p, sha in manifest["frozen_files"].items() if p.endswith("/best.pt")}
        require(len(local_weights) == 40 and Counter(local_weights.values()) == Counter(frozen["checkpoint_hashes"].values()),
                "40 weight declarations not bound to outer actual byte maps")
        require(manifest["models"] == list(MODELS) and manifest["model_versions"] ==
                {m: frozen["freeze_id"] + ":" + m for m in MODELS}, "manifest model/version mismatch")
        require(manifest.get("test_winner_selection") is False, "test winner forbidden")
        require(manifest["freeze_sha256"] == self.artifact("freeze")["sha256"], "manifest freeze hash mismatch")

    def check_wrapper(self, wrapper, required, comparison_sha):
        require(wrapper.get("schema_version") == 1 and wrapper.get("evidence_kind") ==
                ("SYNTHETIC_WRAPPER_FIXTURE" if self.fixture else "ACTUAL_READONLY_V4_EXECUTION_CAPTURE"), "wrapper identity mismatch")
        require(wrapper.get("wrapper_source_sha256") == self.wrapper_sha, "wrapper source not independently approved")
        require(wrapper.get("synthetic") is self.fixture and wrapper.get("actual_hashes_measured") is True and
                wrapper.get("stdout_captured_verbatim") is True and type(wrapper.get("exit_code")) is int and wrapper["exit_code"] == 0,
                "successful original wrapper capture required")
        require(wrapper.get("before_hashes") == required == wrapper.get("after_hashes"), "actual full source maps missing/changed")
        require(wrapper.get("stdout_sha256") == self.artifact("stdout")["sha256"] and
                wrapper.get("comparison_sha256") == comparison_sha, "wrapper output identity mismatch")
        stamps = []
        for name in ("before_captured_at_utc", "started_at_utc", "finished_at_utc", "after_captured_at_utc"):
            try:
                stamp = datetime.fromisoformat(wrapper[name])
            except (KeyError, ValueError, TypeError) as error:
                raise EvidenceRejected("missing actual wrapper timestamp") from error
            require(stamp.tzinfo is not None and stamp.utcoffset().total_seconds() == 0, "UTC wrapper timestamp required")
            stamps.append(stamp)
        require(stamps == sorted(stamps) and stamps[0] < stamps[2] and stamps[-1] <= datetime.now(timezone.utc),
                "wrapper timestamps contradict real execution order")

    def check_stdout(self, freeze_id, statuses):
        ref = self.artifact("stdout"); self.bind(ref)
        raw = self.path(ref).read_bytes()
        require(len(raw) <= MAX_JSON_BYTES and hashlib.sha256(raw).hexdigest() == ref["sha256"], "stdout changed/too large")
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeError as error:
            raise EvidenceRejected("unreadable original stdout") from error
        actual = [line for line in lines if line.startswith(("BEGIN ", "END "))]
        expected = []
        for key in EXPECTED_RUNS:
            ident = freeze_id + ":" + ":".join(key)
            expected += ["BEGIN " + ident, "END " + ident + " " + statuses[key]]
        require(actual == expected, "36 exact ordered BEGIN/END events required; missing/duplicate/truncated log")

    def check_scores(self, frozen, manifest, export):
        keys = {s + "/" + m for s in SPLITS for m in MODELS}
        require(export["freeze_id"] == frozen["freeze_id"] and export["freeze_sha256"] == self.artifact("freeze")["sha256"],
                "export freeze binding mismatch")
        require(self.fixture or export["export_code_sha256"] == EXPORT_SOURCE_SHA, "unreviewed score-export code")
        require(set(export["reports"]) == keys and set(self.packet.get("score_export_receipts", {})) == keys and
                set(self.packet.get("score_csvs", {})) == keys, "18 original/derived CSV proofs required")
        mapping(export["verified_input_hashes"])
        require(set(export["verified_input_hashes"]) == {"X.npy", "hour_complete.npy", "members.npy", "industry.npy",
                "times.npy", "earliest_execution_times.npy", "symbols.json"}, "seven source-bound panel input proofs required")
        incomplete = []
        for model in MODELS:
            rows = manifest["score_exports"][model]
            require(len(rows) == 2 and {r["split"] for r in rows} == set(SPLITS), "two score splits per model required")
            for lineage in rows:
                key = lineage["split"] + "/" + model
                report = self.bind(self.packet["score_export_receipts"][key], read=True)
                require({k: v for k, v in report.items() if k != "sections"} == export["reports"][key], "score receipt differs from export manifest")
                require(report["model_version"] == frozen["freeze_id"] + ":" + model and
                        report["dataset_fingerprint"] == frozen["dataset_fingerprint"], "score model/data mismatch")
                require(report["research_evidence"] is (not self.fixture) and report["raw_preserved"] is True and
                        report["scores_percentiles_versions_changed"] is False and report["inference_performed"] is False and
                        report["labels_or_IC_metrics_read"] is False, "original score preservation proof rejected")
                false_claims(report)
                for kind, pathkey, hashkey in (("raw", "raw_path", "raw_sha256"), ("corrected", "csv_path", "csv_sha256")):
                    ref = self.packet["score_csvs"][key][kind]; actual = self.bind(ref)
                    require(actual == lineage[hashkey] == report["raw_sha256" if kind == "raw" else "corrected_sha256"], "score bytes do not match preservation receipt")
                    require(self.original(ref) == lineage[pathkey] and manifest["frozen_files"].get(lineage[pathkey]) == actual,
                            "score file not bound in frozen input map")
                require(lineage["unresolved_original_score_sections"] == report["unresolved_original_score_sections"], "unresolved score sections dropped")
                if report["status"] != "complete_export": incomplete.append(key)
        return incomplete

    def run(self):
        artifacts = {name: self.bind(self.artifact(name), read=True) for name in
                     ("freeze", "protocol", "source_seal", "driver_receipt", "input_manifest", "comparison",
                      "calendar", "tail_calendar", "score_export_manifest", "wrapper_receipt")}
        for name, expected in PINNED.items():
            require(self.fixture or self.artifact(name)["sha256"] == expected, "unreviewed V4 " + name)
        frozen, protocol, seal, driver, manifest, comparison = (artifacts[k] for k in
            ("freeze", "protocol", "source_seal", "driver_receipt", "input_manifest", "comparison"))
        require(protocol["models"] == list(MODELS) and protocol["windows"] == {k: list(v) for k, v in WINDOWS.items()} and
                protocol["cdr_historical_universe_scope"]["historical_cdr_symbols"] == ["sh689009"], "registered V4 model/window/instrument scope changed")
        self.check_freeze(frozen, manifest)
        false_claims(comparison)
        require(comparison["status"] == "ALL_NINE_FIXED_MODELS_REPLAYED_RESEARCH_ONLY" and comparison["models"] == list(MODELS),
                "36-run actual V4 comparison required")
        for flag in ("economic_target_accepted", "all_PLAN_acceptance_complete", "prospective60session_acceptance_complete"):
            require(comparison.get(flag) is False, "V4 non-acceptance flag missing/promoted")
        require(comparison["freeze_id"] == frozen["freeze_id"] and comparison["model_versions"] == manifest["model_versions"] and
                comparison["fixed_2024_selected_model"] == "selected_full" and comparison["test_winner_selected"] is False,
                "comparison fixed model identity changed")
        for field, name in (("comparison_protocol_sha256", "protocol"), ("source_seal_sha256", "source_seal"),
                            ("driver_receipt_sha256", "driver_receipt")):
            require(comparison[field] == manifest[field] == self.artifact(name)["sha256"], "comparison/source binding mismatch")
        require(comparison["input_manifest_sha256"] == self.artifact("input_manifest")["sha256"], "actual input manifest hash mismatch")
        require(driver["comparison_protocol_sha256"] == self.artifact("protocol")["sha256"] and
                driver["source_seal_sha256"] == self.artifact("source_seal")["sha256"], "driver receipt/source binding mismatch")
        required = merge_maps(seal["immutable_files"], seal["minute_raw_day_files"],
                              driver["driver_source_hashes"], manifest["frozen_files"],
                              {self.original(self.artifact(name)): self.artifact(name)["sha256"] for name in
                               ("protocol", "source_seal", "driver_receipt", "input_manifest")})
        for name in ("freeze", "score_export_manifest"):
            require(required.get(self.original(self.artifact(name))) == self.artifact(name)["sha256"], "frozen input binding missing")
        self.check_wrapper(artifacts["wrapper_receipt"], required, self.artifact("comparison")["sha256"])
        try:
            frozen_at = datetime.fromisoformat(frozen["frozen_at_utc"].replace("Z", "+00:00"))
            started = datetime.fromisoformat(artifacts["wrapper_receipt"]["started_at_utc"])
        except (KeyError, ValueError, TypeError) as error:
            raise EvidenceRejected("actual freeze time missing") from error
        require(frozen_at.tzinfo is not None and frozen_at <= started, "replay precedes actual model freeze")
        self.check_scores(frozen, manifest, artifacts["score_export_manifest"])
        calendar = artifacts["calendar"]["dates"]
        require(calendar == sorted(set(calendar)) and all(exact_day(d) for d in calendar), "verified unique calendar required")
        tail = str(datetime.fromtimestamp(artifacts["tail_calendar"]["next_known_execution_time"] / 1e9, timezone.utc).astimezone(
            timezone(timedelta(hours=8))).date())
        require(tail > calendar[-1], "known execution tail must follow last signal day")
        sessions = calendar + [tail]
        days = [d for d in calendar if "2025-01-02" <= d <= "2026-09-23"]
        require(self.fixture or len(days) == 420, "full 420-session account period required")
        for name in ("calendar", "tail_calendar"):
            require(required.get(self.original(self.artifact(name))) == self.artifact(name)["sha256"], "calendar not sealed in observed source maps")
        benchmark_ref = self.artifact("benchmark"); self.bind(benchmark_ref)
        require(self.fixture or benchmark_ref["sha256"] == protocol["official_benchmark_csv_sha256"], "official benchmark hash mismatch")
        require(required.get(self.original(benchmark_ref)) == benchmark_ref["sha256"], "benchmark absent from actual outer sealed map")
        benchmark = benchmark_prices(self.path(benchmark_ref))
        require([d for d in benchmark if days[0] <= d <= days[-1]] == days, "benchmark/calendar full-period mismatch")
        require(set(self.packet.get("model_score_coverage", {})) == set(MODELS), "nine original coverage receipts required")
        coverage = {m: self.bind(self.packet["model_score_coverage"][m], read=True) for m in MODELS}
        runs = self.packet.get("runs", [])
        require(isinstance(runs, list) and len(runs) == 36, "36 unique run references required")
        keyed = {(r["model"], r["scenario"], r["basis"]): r for r in runs}
        require(len(keyed) == 36 and set(keyed) == set(EXPECTED_RUNS), "missing/duplicate/substituted run")
        paths, reports, statuses = set(), {}, {}
        require(set(comparison["results"]) == set(comparison["artifacts"]) == set(MODELS), "comparison model subset rejected")
        for model in MODELS:
            require(set(comparison["results"][model]) == set(comparison["artifacts"][model]) == set(SCENARIOS), "cost scenario subset rejected")
            for scenario in SCENARIOS:
                require(set(comparison["results"][model][scenario]) == set(comparison["artifacts"][model][scenario]) == set(BASES), "account basis subset rejected")
        for key in EXPECTED_RUNS:
            model, scenario, basis = key; row = keyed[key]
            replay_ref, summary_ref = row["replay"], row["summary"]
            for ref in (replay_ref, summary_ref):
                path = str(self.path(ref)); require(path not in paths, "reused original run artifact")
                paths.add(path)
            replay, summary = self.bind(replay_ref, read=True), self.bind(summary_ref, read=True)
            false_claims(replay); false_claims(summary)
            record = comparison["artifacts"][model][scenario][basis]
            require(record["replay_sha256"] == replay_ref["sha256"] and record["summary_sha256"] == summary_ref["sha256"] and
                    record["replay_path"] == self.original(replay_ref), "comparison journal binding mismatch")
            require(comparison["results"][model][scenario][basis] == summary, "comparison summary differs from original")
            ident = frozen["freeze_id"] + ":" + ":".join(key)
            require(replay["run_id"] == ident and replay["model"] == manifest["model_versions"][model] and
                    replay["cost_scenario"] == scenario, "actual run identity changed")
            require(record["status"] == replay["status"], "run status mismatch")
            statuses[key] = replay["status"]
            journal = check_journal(replay, days, sessions)
            access = check_accesses(replay, seal["minute_raw_day_files"], sessions)
            windows = check_summary(replay, summary, basis, days, benchmark, coverage[model])
            reports[ident] = {"replay": self.bound[self.original(replay_ref)], "summary": self.bound[self.original(summary_ref)],
                "status": replay["status"], "journal": journal, "minute_source_accesses": access, "windows": windows}
        self.check_stdout(frozen["freeze_id"], statuses)
        check_contrasts(comparison, protocol, frozen)
        complete = all(r["status"] == "COMPLETE_DIAGNOSTIC_ONLY" and r["journal"]["full_continuous_account"] for r in reports.values())
        qualified = [w for key, r in reports.items() if key.endswith(":tax_upper_reserve")
                     for w in r["windows"].values() if w["qualification_status"] == "qualified_same_path_interval_only"]
        output = {"schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "evidence_kind": "SYNTHETIC_CONTRACT_FIXTURE" if self.fixture else "READONLY_ACTUAL_V4_EVIDENCE",
            "research_evidence": not self.fixture,
            "replay_collection_status": (("fixture_complete_36_replays" if complete else "fixture_incomplete_36_replays")
                if self.fixture else ("complete_actual_36_replays" if complete else "incomplete_actual_36_replays")),
            "actual_run_count": 0 if self.fixture else 36, "fixture_run_count": 36 if self.fixture else 0,
            "economic_comparison_status": "qualified_same_path_interval_only" if complete and len(qualified) == 54 else "pending_qualification",
            "qualified_same_path_interval_window_count": len(qualified), "required_reserve_windows": 54,
            "freeze_id": frozen["freeze_id"], "runs": reports,
            "observed_outer_source_maps": True, "observed_full_map_entries": len(required),
            "per_run_phase_map_directly_observed": False,
            "per_run_guard_evidence": "SEALED_DRIVER_SUCCESS_PATH_AND_ORIGINAL_SUMMARY_TRUE; NOT_INDEPENDENT_PHASE_HASH_DUMPS",
            "raw_TWAP_prices_independently_reconstructed": False,
            "raw_TWAP_evidence": "ACTUAL_SEALED_ACCESS_HASHES_WINDOWS_COMPLETENESS_FILL_WINDOW_AND_SOURCE_GUARDED_SUMMARY",
            "score_CSV_content_independently_recomputed": False,
            "score_evidence": "ACTUAL_RAW_DERIVED_BYTE_HASHES_AND_SOURCE_BOUND_PRESERVATION_RECEIPTS; NO_SCORE_PARSE_OR_INFERENCE",
            "fee_schedule_is_broker_certified": False, "tax_FIFO_and_rounding_are_broker_certified": False,
            "point_net_excess_return": None, "economic_target_accepted": False,
            "gain_status": "not_assessed_by_this_collection_adapter", "test_winner_selected": False,
            "all_PLAN_acceptance_complete": False, "prospective60session_acceptance_complete": False,
            "legacy_gate_status": "pending_point_net_contract", "legacy_manifest_generated": False,
            "artifact_bindings": self.bound, "packet_sha256": hash_file(self.packet_path)}
        return output


def benchmark_prices(path):
    answer = {}
    with Path(path).open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            d = exact_day(row["date"]); price = money(row["close"])
            require(d not in answer and price > 0 and row["index_code"] == "000905" and
                    "PRICE" in row["benchmark_variant"], "price-only official benchmark schema rejected")
            answer[d] = price
    require(list(answer) == sorted(answer), "benchmark must be chronological")
    return answer


def check_journal(replay, days, sessions):
    """Reconcile recorded cash/quantities and dated sellability; no new tax engine."""
    events, marks = replay["account_event_ledger"], replay["completed_marks"]
    require(isinstance(events, list) and events and isinstance(marks, list), "actual ledger/marks required")
    require(events[0]["kind"] == "INITIALIZE" and events[0]["day"] is None, "one original initial account required")
    require(replay["declared_valuation_days"] == days, "account period changed/truncated")
    mark_days = [exact_day(m["date"]) for m in marks]
    require(mark_days == days[:len(mark_days)] and len(mark_days) <= len(days), "marks skip/reorder/duplicate official sessions")
    require(money(replay["initial_scenario_capital"]) == money(events[0]["payload"]["capital"]) == money(1000000), "registered capital changed")
    prev, cash, lots, actions, fills = "GENESIS", money(1000000), {}, {}, {}
    entitlements, cash_rights, ex_processed, paid = {}, {}, set(), set()
    opens, closes, current, closed = [], [], None, True
    cash_at_close = {}
    for index, event in enumerate(events):
        body = {k: v for k, v in event.items() if k != "hash"}
        require(event["previous_hash"] == prev and hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest() == event["hash"],
                "journal hash chain changed")
        prev = event["hash"]; kind, payload, d = event["kind"], event["payload"], event["day"]
        if kind == "INITIALIZE": require(index == 0, "cash/holdings reset rejected")
        elif kind == "SESSION_OPEN":
            require(closed and len(opens) < len(days) and d == days[len(opens)], "skipped/reopened official account session")
            current, closed = d, False; opens.append(d)
        else:
            # begin() sets the new day and records ex/listing/open-payment events
            # before SESSION_OPEN; close() records the reserve after SESSION_CLOSE.
            pre_open = closed and len(opens) < len(days) and d == days[len(opens)] and kind in {
                "EX_DISTRIBUTION", "PRETAX_EX_ENTITLEMENT_SCENARIO", "NEW_SHARES_LISTED",
                "NEW_SHARES_LISTED_SCENARIO", "CASH_DISTRIBUTION_PAID"}
            require(d is None or d == current or pre_open, "journal date outside active session")
        if kind == "VERSION_CHANGED": require(payload["capital_reset"] is False, "version reset rejected")
        if kind in ("ACTION_ANNOUNCED", "PRETAX_ACTION_SCENARIO_ANNOUNCED"):
            action_id = payload["action_id"]
            require(action_id not in actions, "duplicate corporate action")
            require(payload["announced_on"] <= d <= payload["record_on"] < payload["ex_on"], "late corporate entitlement cannot be backfilled")
            actions[action_id] = payload
        if kind == "ENTITLEMENT_CAPTURED":
            action = actions[payload["action"]]
            require(d == action["record_on"] and payload["action"] not in entitlements and payload["basis"] == "LISTED_ONLY",
                    "record-day capture repeated/wrong date/basis")
            entitled = sum(l[0] for l in lots.get(action["symbol"], []) if l[2] <= d)
            require(type(payload["quantity"]) is int and payload["quantity"] == entitled, "record-date holdings differ from actual dated lots")
            entitlements[payload["action"]] = entitled
        if kind in ("EX_DISTRIBUTION", "PRETAX_EX_ENTITLEMENT_SCENARIO"):
            action = actions[payload["action"]]
            require(d == action["ex_on"] and payload["action"] in entitlements and payload["action"] not in ex_processed,
                    "corporate entitlement wrong session/missing actual record capture/repeated")
            require(payload["record_quantity"] == entitlements[payload["action"]], "ex right uses invented record holdings")
            entitlement = money(payload["record_quantity"])
            ratio = money(action.get("new_share_ratio", action.get("bonus_ratio")))
            require(entitlement * ratio == money(payload["new_shares"]), "noncash quantity rounded/replaced")
            if kind == "PRETAX_EX_ENTITLEMENT_SCENARIO":
                expected_cash = (entitlement * money(action["cash_gross_per_share"])).quantize(money(".01"), rounding=ROUND_HALF_UP)
                require(expected_cash == money(payload["gross_cash_receivable"]), "declared research cash entitlement does not reconcile")
            else:
                expected_cash = entitlement * money(action["cash_net_per_share"])
            cash_rights[payload["action"]] = expected_cash
            ex_processed.add(payload["action"])
            q = int(payload["new_shares"])
            require(q >= 0, "negative entitlement quantity")
            if q: lots.setdefault(action["symbol"], []).append([q, action["shares_sellable_on"], action["shares_list_on"]])
        if kind == "FILL":
            require(not closed and payload["id"] not in fills, "fill outside open session/duplicate fill")
            q, symbol = payload["quantity"], payload["symbol"]
            require(type(q) is int and q > 0 and payload["side"] in ("BUY", "SELL"), "invalid actual fill")
            price, fee = money(payload["price"]), money(payload["fee"])
            require(price > 0 and fee >= 0, "invalid actual fill money")
            require(d in sessions and sessions.index(d) + 1 < len(sessions) and
                    payload["next_session"] == sessions[sessions.index(d) + 1], "T+1 calendar mismatch")
            if payload["side"] == "BUY":
                cash -= price * q + fee
                lots.setdefault(symbol, []).append([q, payload["next_session"], d])
            else:
                available = sum(l[0] for l in lots.get(symbol, []) if l[1] <= d and l[2] <= d)
                require(q <= available, "T+1/unlisted inventory breach")
                left = q
                for lot in lots.get(symbol, []):
                    if lot[1] <= d and lot[2] <= d:
                        take = min(left, lot[0]); lot[0] -= take; left -= take
                cash += price * q - fee
            fills[payload["id"]] = payload
        if kind in ("CASH_DISTRIBUTION_PAID", "PRETAX_CASH_DISTRIBUTION_PAID", "GROSS_DIVIDEND_PAID_SCENARIO"):
            action = actions[payload["action"]]
            require(d == action["pay_on"] and payload["action"] in cash_rights and payload["action"] not in paid,
                    "cash distribution wrong pay date/missing entitlement/repeated")
            require(money(payload["amount"]) == cash_rights[payload["action"]], "actual cash payment differs from recorded right")
            paid.add(payload["action"])
            cash += money(payload["amount"])
        if kind == "RESEARCH_CASH_DIVIDEND_TAX_UPPER_LIABILITY_RESERVED":
            require(payload["reserve_is_simulated_liability_not_actual_broker_debit"] is True, "reserve presented as broker debit")
            cash -= money(payload["reserve_delta"])
            require(cash == money(payload["spendable_cash"]), "reserve cash reconciliation failed")
            if closed: cash_at_close[d] = cash  # reserve is actually recorded after SESSION_CLOSE
        if kind == "SESSION_CLOSE":
            require(not closed, "duplicate session close")
            closed = True; closes.append(d); cash_at_close[d] = cash
    require(prev == replay["account_event_ledger_hash"], "terminal journal hash mismatch")
    require(closes == opens[:len(closes)] and closes[:len(mark_days)] == mark_days, "marks/closed sessions mismatch")
    for mark in marks:
        require(money(mark["accounting_residual"]) == 0, "nonzero original accounting residual")
        require(money(mark["cash"]) == cash_at_close[mark["date"]], "actual journal/mark cash mismatch")
        require(money(mark["nav"]) == sum(money(mark[k]) for k in
                ("cash", "listed_stock_value", "unlisted_share_claim", "cash_receivable")), "NAV cash/claim identity failed")
    closing = {s: sum(l[0] for l in ls) for s, ls in lots.items() if sum(l[0] for l in ls)}
    require(closing == replay["closing_quantity"], "terminal actual holdings differ from journal")
    orders = replay["outcomes"]; ids = set(); matched = set()
    require([o["date"] for o in orders] == sorted(o["date"] for o in orders), "terminal outcomes not chronological")
    for outcome in orders:
        ident = outcome["order_id"]; require(ident not in ids, "duplicate terminal order outcome"); ids.add(ident)
        require(exact_day(outcome["date"]) in opens and outcome["side"] in ("BUY", "SELL") and
                isinstance(outcome["symbol"], str) and outcome["symbol"], "malformed dated terminal outcome")
        q = outcome["filled"]
        require(type(q) is int and type(outcome["requested"]) is int and outcome["requested"] > 0 and
                type(outcome["expired"]) is int and 0 <= q <= outcome["requested"] and outcome["expired"] == outcome["requested"] - q, "order terminal quantity mismatch")
        fid = "OPEN:" + ident
        if q:
            require(fid in fills, "filled outcome missing actual cash journal event")
            f = fills[fid]
            require((f["symbol"], f["side"], f["quantity"], f["day"]) ==
                    (outcome["symbol"], outcome["side"], q, outcome["date"]), "outcome/fill identity mismatch")
            require(money(f["price"]) == money(outcome["price"]) and money(f["fee"]) == money(outcome["fees"]["total"]), "outcome price/fee differs from journal")
            require(money(outcome["fees"]["total"]) == sum(money(outcome["fees"][k]) for k in ("commission", "transfer", "stamp")), "fee components do not reconcile")
            require(outcome["execution_evidence"] == EXECUTION_SCOPE, "TWAP evidence scope changed")
            for field, clock in (("execution_window_start", "09:31:00"), ("execution_window_end", "09:36:00"), ("recorded_fill_time", "09:36:00")):
                require(outcome[field] == outcome["date"] + " " + clock + "+08:00", "fill timing is not original raw 0931--0936 window")
            require(outcome["five_minute_price_available_at"] == outcome["recorded_fill_time"], "fill before five raw bars available")
            matched.add(fid)
        else:
            require(fid not in fills and outcome["price"] is None and outcome["fees"] is None, "failed fill altered cash/inventory")
    require(matched == set(fills), "journal fill missing terminal engine outcome")
    full = mark_days == days and opens == closes == days and closed and replay["failure"] is None
    require(replay["status"] != "COMPLETE_DIAGNOSTIC_ONLY" or full, "short-prefix account claimed complete")
    return {"hash_chain_verified": True, "events": len(events), "official_mark_sessions": len(marks),
            "full_continuous_account": full, "cash_quantity_reconciled": True,
            "record_ex_payment_components_reconciled": True,
            "recorded_T1_sellability_checked": True, "failed_fills_preserve_state": True,
            "tax_FIFO_recomputed": False, "official_tax_rounding_verified": False}


def check_accesses(replay, minute_map, sessions):
    accesses = replay.get("sealed_minute_source_accesses")
    require(isinstance(accesses, list), "actual sealed minute access receipt missing")
    for row in accesses:
        d = exact_day(row["execution_day"])
        require(d in sessions and row["source_path"] in minute_map and minute_map[row["source_path"]] == row["source_sha256"], "unsealed minute access")
        start = int(row["window_start_utc_ns"]); end = int(row["window_end_utc_ns"])
        local = datetime.fromtimestamp(start / 1e9, timezone.utc).astimezone(timezone(timedelta(hours=8)))
        require(local.isoformat() == d + "T09:31:00+08:00" and end - start == 300_000_000_000, "raw minute window alignment changed")
        require(sessions.index(d) > 0, "raw window lacks previous official signal session")
        signal = datetime.fromtimestamp(int(row["signal_time_utc_ns"]) / 1e9, timezone.utc).astimezone(timezone(timedelta(hours=8)))
        require(signal.isoformat() == sessions[sessions.index(d)-1] + "T15:00:00+08:00", "execution skips prior-close signal alignment")
        for quote in row["requested_quotes"].values():
            require(type(quote["complete"]) is bool, "unknown quote-completeness schema")
    for outcome in replay["outcomes"]:
        if outcome["filled"]:
            require(any(a["execution_day"] == outcome["date"] and a["requested_quotes"].get(outcome["symbol"], {}).get("complete") is True
                        for a in accesses), "actual fill has no complete sealed minute access")
    return {"actual_access_receipts": len(accesses), "sealed_hash_window_identity_verified": True,
            "source_guarded_fill_prices_and_slippage_only": True, "raw_prices_independently_reconstructed": False}


def _same(actual, expected, name):
    if expected is None:
        require(actual is None, "unknown promoted to finite: " + name)
    else:
        require(money(actual) == expected, "arithmetic differs from actual marks: " + name)


def _drawdown(points):
    peak, worst = None, money(0)
    for point in points:
        point = money(point); require(point > 0, "nonpositive drawdown mark")
        peak = point if peak is None else max(peak, point)
        worst = min(worst, point/peak-1)
    return -worst if peak is not None else None


def _instrument_dates(replay):
    """Independently keep the frozen sh689009 gate, including no-ex-date exposure."""
    quantities, actions, exposed = {}, {}, set()
    def held(d):
        if quantities.get("sh689009", 0) > 0:
            exposed.add((d, "sh689009"))
    for event in replay["account_event_ledger"]:
        if event["day"] is None: continue
        d, p, k = event["day"], event["payload"], event["kind"]
        held(d)
        if k in ("ACTION_ANNOUNCED", "PRETAX_ACTION_SCENARIO_ANNOUNCED"):
            actions[p["action_id"]] = p["symbol"]
        elif k == "FILL":
            if p["symbol"] == "sh689009": exposed.add((d, p["symbol"]))
            quantities[p["symbol"]] = quantities.get(p["symbol"], 0) + (1 if p["side"] == "BUY" else -1)*p["quantity"]
        elif k in ("EX_DISTRIBUTION", "PRETAX_EX_ENTITLEMENT_SCENARIO"):
            symbol = actions[p["action"]]
            quantities[symbol] = quantities.get(symbol, 0) + p["new_shares"]
        held(d)
    for o in replay["outcomes"]:
        if o["symbol"] == "sh689009": exposed.add((o["date"], o["symbol"]))
    for e in replay["provider_execution_events"]:
        if e["kind"] == "NEXT_SESSION_RAW_TWAP_CONTEXT":
            for row in e["no_fill_windows"]:
                if row["symbol"] == "sh689009": exposed.add((e["day"], row["symbol"]))
    # Match the sealed V4 propagation at marks after all already-observed events.
    for mark in replay["completed_marks"]:
        if quantities and all(mark["date"] >= d for d, _ in exposed): held(mark["date"])
    return exposed


def _exposure_keys(rows):
    keys = [(exact_day(r["date"]), r["symbol"]) for r in rows]
    require(len(keys) == len(set(keys)), "duplicate instrument exposure")
    return set(keys)


def check_summary(replay, summary, basis, days, benchmark, coverage):
    require(summary["account_basis"] == basis and summary["replay_status"] == replay["status"] and
            summary["continuous_account"] is True and summary["source_and_minute_hashes_verified_before_and_after_run"] is True,
            "source-guarded continuous summary required")
    require(summary["instrument_gate_is_independent_of_reference_formula_gate"] is True and set(summary["windows"]) == set(WINDOWS), "independent instrument/three-window gate required")
    marks = {m["date"]: m for m in replay["completed_marks"]}
    instrument_dates = _instrument_dates(replay)
    require(_exposure_keys(summary["instrument_qualification_exposures"]) == instrument_dates,
            "actual held/requested/no-fill CDR exposure dropped")
    out = {}
    for name, (anchor, end) in WINDOWS.items():
        window = summary["windows"][name]
        dates = [d for d in days if anchor <= d <= end]
        require(window["anchor"] == anchor and window["end"] == end and window["reset_account"] is False and
                window["interval_not_annualized"] is True, "fixed annual anchor/window changed")
        require(window["expected_mark_count"] == len(dates) and window["observed_mark_count"] == sum(d in marks for d in dates), "window mark count mismatch")
        unknown = [key for key in ("missing_execution_windows", "prior_execution_evidence_affect_anchor",
                   "exposed_unresolved_ex_references", "prior_ex_reference_exposures_affect_anchor",
                   "instrument_qualification_exposures", "prior_instrument_qualification_exposures_affect_anchor") if window[key]]
        require(_exposure_keys(window["instrument_qualification_exposures"]) == {p for p in instrument_dates if anchor < p[0] <= end} and
                _exposure_keys(window["prior_instrument_qualification_exposures_affect_anchor"]) == {p for p in instrument_dates if p[0] <= anchor},
                "CDR exposure at/before anchor dropped")
        actual_unknown = [(e["day"], r) for e in replay["provider_execution_events"] if e["kind"] == "NEXT_SESSION_RAW_TWAP_CONTEXT"
                          for r in e["no_fill_windows"] if r.get("execution_evidence_missing")]
        require(window["missing_execution_windows"] == [dict(date=d, **r) for d, r in actual_unknown if anchor < d <= end] and
                window["prior_execution_evidence_affect_anchor"] == [dict(date=d, **r) for d, r in actual_unknown if d <= anchor],
                "required execution unknown dropped from window/anchor")
        references = summary["ex_reference_exposures"]
        ref_date = lambda r: r.get("ex_date", r.get("date", r.get("ex_on")))
        require(window["exposed_unresolved_ex_references"] == [r for r in references if anchor < ref_date(r) <= end] and
                window["prior_ex_reference_exposures_affect_anchor"] == [r for r in references if ref_date(r) <= anchor],
                "source-bound unresolved reference dropped from window/anchor")
        fullmarks = all(d in marks for d in dates)
        score_complete = coverage["continuous_account_trading_score_coverage_by_window"].get(name) is True
        failure = replay["failure"]
        blocked = bool(failure and failure.get("book_day") and failure["book_day"] <= end and
                       failure.get("error_type") not in ("IncompleteCashTaxEvidence", "IncompleteRawTWAPEvidence"))
        full = fullmarks and score_complete and not blocked and not window["missing_execution_windows"] and not window["prior_execution_evidence_affect_anchor"]
        require(window["full_mark_and_execution_coverage"] is full, "mark/execution/score coverage inconsistent")
        affected = bool(window["instrument_qualification_exposures"] or window["prior_instrument_qualification_exposures_affect_anchor"])
        require(window["instrument_tax_fee_scope_complete"] is (not affected) and window["full_mark_execution_and_instrument_scope_complete"] is (full and not affected), "CDR/instrument qualifier inconsistent")
        risk = bool(window["eod_risk_violations"] or window["postfill_risk_violations"])
        require(window["eod_risk_violations"] == [dict(date=d, violations=marks[d]["violations"]) for d in dates
                if d in marks and marks[d]["violations"]], "actual closing risk flags changed/dropped")
        require(window["postfill_risk_violations"] == [dict(date=o["date"], order_id=o["order_id"], violations=o["risk_after"]["violations"])
                for o in replay["outcomes"] if anchor < o["date"] <= end and (o.get("risk_after") or {}).get("violations")], "actual postfill risk flags changed/dropped")
        require(window["frozen_risk_policy_satisfied"] is (full and not risk), "risk qualification inconsistent")
        tax_unknown = [m["date"] for m in replay["completed_marks"] if m["date"] <= end and
                       (m.get("unresolved_tax_events") or m.get("cash_tax_net_bounds_complete") is False)]
        if full:
            nav0, nav1 = money(marks[anchor]["nav"]), money(marks[end]["nav"])
            idx0, idx1 = benchmark[anchor], benchmark[end]
            require(min(nav0, nav1, idx0, idx1) > 0, "positive NAV/index endpoints required")
            expected = {"account_scenario_return": nav1/nav0-1, "csi500_price_return": idx1/idx0-1,
                        "arithmetic_excess_percentage_points": 100 * ((nav1/nav0-1) - (idx1/idx0-1)),
                        "wealth_relative_excess_return": (nav1/nav0)/(idx1/idx0)-1}
            require(isinstance(window["scenario_values"], dict), "missing full-window diagnostic values")
            for key, value in expected.items(): _same(window["scenario_values"][key], value, key)
        else:
            require(window["scenario_values"] is None, "short prefix promoted to full-window values")
        _same(window["scenario_max_drawdown"], _drawdown(marks[d]["nav"] for d in dates if d in marks) if full else None, "scenario drawdown")
        _same(window["csi500_price_max_drawdown"], _drawdown(benchmark[d] for d in dates), "benchmark drawdown")
        buy, sell, turnover = money(0), money(0), money(0)
        fees = {k: money(0) for k in ("commission", "transfer", "stamp", "total")}
        turnover_unknown = []
        for o in replay["outcomes"]:
            if not anchor < o["date"] <= end or not o["filled"]: continue
            notional = money(o["price"]) * o["filled"]
            if o["side"] == "BUY": buy += notional
            else: sell += notional
            prev = days[days.index(o["date"])-1] if days.index(o["date"]) else None
            if prev not in marks: turnover_unknown.append(o["order_id"])
            else: turnover += notional/money(marks[prev]["nav"])
            for k in fees: fees[k] += money(o["fees"][k])
        _same(window["filled_buy_notional_cny"], buy, "buy notional")
        _same(window["filled_sell_notional_cny"], sell, "sell notional")
        _same(window["cumulative_gross_turnover_previous_close_nav"], turnover if not turnover_unknown else None, "turnover")
        _same(window["cumulative_one_way_half_gross_turnover"], turnover/2 if not turnover_unknown else None, "half turnover")
        require(window["turnover_unknown_orders"] == turnover_unknown, "turnover unknown order dropped")
        for k, value in fees.items(): _same(window["trading_fees_cny"][k], value, "actual filled fee: " + k)
        require(window["slippage_already_in_fill_NAV_not_added_again"] is True, "slippage double-counting contract changed")
        if not window["slippage_unknown_orders"]:
            require(money(window["slippage_vs_raw_twap_cny"]) >= 0, "invalid source-bound adverse slippage")
        else: require(window["slippage_vs_raw_twap_cny"] is None, "missing raw slippage promoted to finite")
        bounds = window["same_path_net_tax_bounds"]
        if bounds is not None:
            require(basis == "tax_upper_reserve" and full and not unknown and not tax_unknown and bounds["scope"] == BOUND_SCOPE,
                    "bounds despite prior/within-window unknown qualification")
            a, z = marks[anchor], marks[end]
            low0, high0, low1, high1 = (money(m[k]) for m, k in ((a, "net_nav_lower_bound"), (a, "net_nav_upper_bound"),
                (z, "net_nav_lower_bound"), (z, "net_nav_upper_bound")))
            require(0 < low0 <= high0 and 0 < low1 <= high1, "invalid same-path NAV envelope")
            lower, upper, bench = low1/high0-1, high1/low0-1, benchmark[end]/benchmark[anchor]-1
            expect = {"same_trading_path_net_return_lower": lower, "same_trading_path_net_return_upper": upper,
                      "arithmetic_net_excess_percentage_points_lower": 100*(lower-bench),
                      "arithmetic_net_excess_percentage_points_upper": 100*(upper-bench),
                      "wealth_relative_net_excess_return_lower": (1+lower)/(1+bench)-1,
                      "wealth_relative_net_excess_return_upper": (1+upper)/(1+bench)-1}
            for key, value in expect.items(): _same(bounds[key], value, key)
        eligible = full and not unknown and not tax_unknown and not risk and bounds is not None
        require(window["economic_comparison_eligible"] is eligible, "unknown qualification promoted to eligible")
        out[name] = {"anchor": anchor, "end": end, "reset_account": False, "point_net_excess_return": None,
            "qualification_status": "qualified_same_path_interval_only" if eligible else "pending_qualification",
            "unknown_exposure_fields": unknown, "unresolved_tax_mark_dates_including_before_anchor": tax_unknown,
            "scenario_values_diagnostic": window["scenario_values"], "same_path_net_tax_bounds": bounds,
            "original_qualification_and_costs": window, "official_tax_rounding_verified": False,
            "broker_point_net_certified": False}
    return out


def check_contrasts(comparison, protocol, frozen):
    reports, specs = comparison["contrasts"], protocol["contrasts"]
    require(len(specs) == 9 and set(reports) == set(specs), "nine preregistered comparisons required; no subset winner")
    best = max(range(1, 5), key=lambda k: money(frozen["selection"]["alternatives"]["fixed_" + str(k)]["mean_ic"]))
    expected_keys = {(s, b, w) for s in SCENARIOS for b in BASES for w in WINDOWS}
    for name, spec in specs.items():
        report = reports[name]
        require(report["model_a"] == spec["model_a"] and report["model_b"] == spec.get("model_b", "fixed_label_" + str(best)) and
                report["resolver_used_2024_only"] is True, "contrast identity/resolver changed")
        rows = report["rows"]
        keys = [(r["cost_scenario"], r["account_basis"], r["window"]) for r in rows]
        require(len(keys) == len(set(keys)) == 12 and set(keys) == expected_keys, "contrast missing duplicate/substituted fixed window")
        for row in rows:
            require(row["point_net_improvement_accepted"] is False and row["partial_prefix_or_subset_winner"] is False, "contrast promoted to winner")
            a = comparison["results"][report["model_a"]][row["cost_scenario"]][row["account_basis"]]["windows"][row["window"]]
            b = comparison["results"][report["model_b"]][row["cost_scenario"]][row["account_basis"]]["windows"][row["window"]]
            full = a["full_mark_and_execution_coverage"] and b["full_mark_and_execution_coverage"]
            eligible = a["economic_comparison_eligible"] and b["economic_comparison_eligible"]
            require(row["both_full_fixed_window"] is full and row["risk_and_net_bounds_eligible"] is eligible,
                    "contrast qualifications differ from bound windows")
            if full:
                require(isinstance(row["scenario_difference"], dict), "missing full-window diagnostic difference")
                for key in ("account_scenario_return", "arithmetic_excess_percentage_points", "wealth_relative_excess_return"):
                    _same(row["scenario_difference"][key], money(a["scenario_values"][key])-money(b["scenario_values"][key]), "contrast scenario difference")
            else:
                require(row["scenario_difference"] is None, "partial comparison promoted to finite")
            if eligible:
                x, y = a["same_path_net_tax_bounds"], b["same_path_net_tax_bounds"]
                expect = {"arithmetic_net_excess_difference_percentage_points_lower":
                            money(x["arithmetic_net_excess_percentage_points_lower"])-money(y["arithmetic_net_excess_percentage_points_upper"]),
                          "arithmetic_net_excess_difference_percentage_points_upper":
                            money(x["arithmetic_net_excess_percentage_points_upper"])-money(y["arithmetic_net_excess_percentage_points_lower"])}
                for key, value in expect.items(): _same(row["same_path_net_difference_bounds"][key], value, "contrast interval difference")
            else:
                require(row["same_path_net_difference_bounds"] is None, "unknown comparison promoted to interval")


def inspect_packet(packet_path, approved_wrapper_source_sha256):
    """Explicit future actual-evidence read. Missing input returns pending, never writes."""
    try:
        return _Inspector(packet_path, approved_wrapper_source_sha256).run()
    except EvidencePending as error:
        return {"schema_version": 1, "replay_collection_status": "pending_actual_evidence", "reason": str(error),
                "research_evidence": False, "actual_run_count": 0, "economic_comparison_status": "pending_qualification",
                "point_net_excess_return": None, "all_PLAN_acceptance_complete": False, "legacy_manifest_generated": False}
    except (KeyError, TypeError, IndexError, AttributeError, OSError) as error:
        raise EvidenceRejected("malformed existing evidence: " + type(error).__name__) from error


def write_new_receipt(path, payload):
    """Only new additive receipts; pending/fixture/legacy output is never published."""
    require(payload.get("research_evidence") is True and payload.get("replay_collection_status") == "complete_actual_36_replays",
            "no completed receipt for pending/incomplete/fixture material")
    path = Path(path)
    require(path.name.startswith("v4_actual_evidence_receipt_") and path.suffix == ".json", "additive receipt filename required; no legacy manifest")
    require(str(path.resolve()) not in {r["path"] for r in payload["artifact_bindings"].values()}, "cannot overwrite any original evidence")
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    # No creation of a missing real result directory or replace operation.
    with path.open("xb") as stream:
        stream.write(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command")
    subs.add_parser("contract")
    inspect = subs.add_parser("inspect")
    inspect.add_argument("--packet", type=Path, required=True)
    inspect.add_argument("--approved-wrapper-source-sha256", required=True)
    inspect.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.command in (None, "contract"):
        print(json.dumps(contract(), ensure_ascii=False, indent=2)); return 0
    try:
        result = inspect_packet(args.packet, args.approved_wrapper_source_sha256)
        if args.output and result["replay_collection_status"] == "complete_actual_36_replays":
            write_new_receipt(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if result["replay_collection_status"] == "complete_actual_36_replays" else 2
    except (EvidenceRejected, FileExistsError) as error:
        print(json.dumps({"replay_collection_status": "rejected_existing_evidence", "reason": str(error),
                          "point_net_excess_return": None, "all_PLAN_acceptance_complete": False,
                          "original_bytes_retained": True, "legacy_manifest_generated": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
