"""Exercise current build_context on saved, training-only V7 stage snapshots.

All writes target a new replay directory. No research kernel is constructed on
the old study. The third stage's V8 plan is an explicit counterfactual injection.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quanta_agents.meta_v7.protocol import build_context
from quanta_agents.meta_v7.revisions import derive_revision, freeze_revision_plan


def serial(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_support_values(view, original):
    schema, pairs, checked = view["table_schema"], {}, []
    factor_ids = schema["factor_ids"]
    for kind in ("correlations", "conditions", "interactions"):
        for row in original[kind]:
            suffix = (row["horizon"], row["condition"]) if kind == "conditions" else ()
            pairs[(kind, row["left"], row["right"], *suffix)] = row

    def check(raw, data, columns, coverage=None):
        decoded = dict(zip(columns, data))
        support = dict(zip(schema["evidence_support"], view["evidence_support"][decoded["support"]]))
        fields = [key for key in ("observed_days", "calendar_days", "missing_days", "paired_cells", "rank_deficient_days") if key in raw]
        ok = decoded["mean_ic"] == raw["mean_ic"] and all(support[key] == raw[key] for key in fields)
        source_coverage = raw.get("coverage", coverage)
        if source_coverage is not None:
            ok = ok and support["coverage"] == [source_coverage.get(key) for key in schema["coverage"]]
        checked.append(ok)

    for identity, factor in view["factors"].items():
        for data in factor["ic"]:
            check(original["factors"][identity]["horizons"][data[0]], data, schema["ic"])
        for data in factor["risk"]:
            check(original["factors"][identity]["risk"][data[0]][data[1]], data, schema["risk"])
    for kind in ("correlations", "conditions"):
        for data in view[kind]:
            suffix = tuple(data[2:4]) if kind == "conditions" else ()
            check(pairs[(kind, factor_ids[data[0]], factor_ids[data[1]], *suffix)], data, schema[kind])
    for row in view["interactions"]:
        raw = pairs[("interactions", factor_ids[row["left"]], factor_ids[row["right"]])]
        for data in row["ic"]:
            horizon = raw["horizons"][data[0]]
            check(horizon[data[1]], data, schema["interaction_ic"], horizon.get("coverage"))
    return {"metric_means_and_saved_support_checked": len(checked), "all_copied_exactly": all(checked)}


class SnapshotStore:
    def __init__(self, study, destination, packet, sources):
        self.study, self.destination, self.packet = study, destination, packet
        self.sources = sources
        self.destination.mkdir(parents=True, exist_ok=True)
        self.evidence_reads, self.evidence_writes = [], []
        self.ledger = self.load(packet["full_ledger_evidence_id"])
        self.reviews = deepcopy(self.ledger["batch_reviews"])
        connection = sqlite3.connect((study / "study.sqlite").as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            self.events = [dict(r) for r in connection.execute(
                "SELECT seq,kind,payload FROM events WHERE seq<=? ORDER BY seq", (packet["state"]["revision"],))]
            self.frozen_runs = {}
            for run in self.ledger["runs"]:
                row = dict(connection.execute("SELECT id,spec,status,evidence_id FROM runs WHERE id=?", (run["id"],)).fetchone())
                # Only run ids already present at the training-only snapshot.
                self.frozen_runs[row["id"]] = row
        finally:
            connection.close()
        self.meta_values = {"batch_evidence:" + batch["batch_id"]: batch["evidence_id"] for batch in packet["paired_results"]}
        self.meta_values["stopped"] = False

    def load(self, identity):
        path = self.study / "evidence" / (identity + ".json")
        self.sources[str(path)] = sha(path)
        return read(path)

    def evidence(self, identity, **kwargs):
        self.evidence_reads.append(identity)
        return {"value": deepcopy(self.load(identity))}

    def put_evidence(self, kind, value):
        identity = "ev_" + hashlib.sha256(serial({"kind": kind, "value": value}).encode("utf-8")).hexdigest()
        path = self.destination / (identity + ".json")
        path.write_text(serial(value) + "\n", encoding="utf-8")
        self.evidence_writes.append({"kind": kind, "path": str(path), "sha256": sha(path)})
        return identity

    def meta(self, key, default=None):
        return self.meta_values.get(key, default)

    def rows(self, query, parameters=()):
        q = " ".join(query.split())
        if q == "SELECT DISTINCT batch_id FROM attempts ORDER BY rowid DESC LIMIT 2":
            values = list(dict.fromkeys(r["batch_id"] for r in reversed(self.ledger["attempts"])))[:2]
            return [{"batch_id": value} for value in values]
        if q.startswith("SELECT seq,kind,payload FROM events WHERE seq>?"):
            return [deepcopy(row) for row in reversed(self.events) if row["seq"] > parameters[0]
                    and (row["kind"].startswith("action_") or row["kind"] in {"batch_review", "validation_review"})][:3]
        if q == "SELECT payload FROM events WHERE kind='batch_registered' ORDER BY seq DESC":
            return [{"payload": row["payload"]} for row in reversed(self.events) if row["kind"] == "batch_registered"]
        if q == "SELECT review FROM batch_reviews WHERE batch_id=?":
            return [{"review": row["review"]} for row in self.reviews if row["batch_id"] == parameters[0]]
        if q == "SELECT batch_id,review FROM batch_reviews ORDER BY rowid DESC LIMIT 1":
            return deepcopy(self.reviews[-1:])
        if q.startswith("SELECT r.spec,r.status,r.evidence_id FROM runs r WHERE r.id=? AND EXISTS"):
            run_id, batch_id = parameters
            valid = any(r["run_id"] == run_id and r["batch_id"] == batch_id and r["status"] == "completed" for r in self.ledger["attempts"])
            return [deepcopy(self.frozen_runs[run_id])] if valid else []
        if q == "SELECT id FROM validation_jobs":
            return []
        tables = {"SELECT * FROM stage_evidence": "stage_evidence", "SELECT * FROM batch_reviews": "batch_reviews",
                  "SELECT id,status,evidence_id,error FROM runs": "runs", "SELECT id,status,usage,error FROM model_calls": "model_calls",
                  "SELECT * FROM attempts": "attempts"}
        if q in tables:
            return deepcopy(self.reviews if tables[q] == "batch_reviews" else self.ledger[tables[q]])
        raise AssertionError("Unimplemented snapshot query: " + q)

    def inject_review(self, review):
        self.reviews = [{"batch_id": row["batch_id"], "review": serial(review) if row["batch_id"] == review["batch_id"] else row["review"]}
                        for row in self.reviews]
        # Match the controller's review + action result duplication, not only
        # the easy minimal context; it materially affects the real byte budget.
        for event in self.events:
            if event["kind"] == "batch_review":
                old = json.loads(event["payload"])
                if old.get("batch_id") == review["batch_id"]:
                    event["payload"] = serial(review)
            elif event["kind"] == "action_review_batch":
                old = json.loads(event["payload"])
                if old.get("result", {}).get("batch_id") == review["batch_id"]:
                    old["result"] = deepcopy(review)
                    event["payload"] = serial(old)


def run(study, destination):
    sources = {}
    for path in (study / "study.sqlite", study / "study.sqlite-wal"):
        if path.exists():
            sources[str(path)] = sha(path)
    first_id = "model_e96113b40d75407a9d25044dae36c3ef"
    ids = [first_id, "model_5bcffdce6546423594f0640ed0bc6c81", "model_a6db9c14b461465abb9ca64eb550dc48"]
    packets = []
    for identity in ids:
        path = study / "model_calls" / identity / "prompt.txt"
        sources[str(path)] = sha(path)
        packets.append(json.loads(path.read_text(encoding="utf-8").split("RESEARCH_STATE_JSON\n", 1)[1]))
    catalogue = {row["id"]: row for row in packets[0]["catalogue"]}
    config_path = study / "config.json"
    config = read(config_path)
    sources[str(config_path)] = sha(config_path)
    results = []
    for index, (name, packet) in enumerate(zip(("first_selection", "first_review", "next_selection_with_bound_plan"), packets)):
        if packet["validation_results"] or packet["state"]["validation_jobs"]:
            raise AssertionError("Replay stages must precede validation")
        stage_dir = destination / name
        store = SnapshotStore(study, stage_dir / "evidence", packet, sources)
        state = deepcopy(packet["state"])
        state.update(version="meta_v8.0.0-dev", research_profile="v8")
        kernel = SimpleNamespace(research_profile="v8", status=lambda: deepcopy(state), config=deepcopy(config), store=store,
            assets=SimpleNamespace(get=lambda identity: deepcopy(catalogue[identity])),
            project=SimpleNamespace(summary=lambda: deepcopy(store.ledger["project"]),
                list_trials=lambda limit, offset: deepcopy(store.ledger["trials"][offset:offset + limit]),
                list_exposures=lambda limit, offset: deepcopy(store.ledger["exposures"][offset:offset + limit])))
        counterfactual, derivation = None, None
        if index == 2:
            review = json.loads(store.reviews[0]["review"])
            parent_id = next(row["run_id"] for row in store.ledger["attempts"] if row["role"] == "omit_2")
            plan = {"parent_run_id": parent_id, "allowed_paths": ["/allocation/weighting", "/risk_score"],
                    "hypothesis": "保持纯动量父规格，只比较等权与逆波动配置。",
                    "falsifier": "若改善仅来自仓位变化，不能认定F7风险信息有独立价值。"}
            review["revision_plan"] = freeze_revision_plan(kernel, review["batch_id"], plan)
            store.inject_review(review)
            before_reads = len(store.evidence_reads)
            derived = derive_revision(kernel, parent_id, review["batch_id"],
                                      [{"path": "/allocation/weighting", "value": "equal"}, {"path": "/risk_score", "value": None}])
            original = json.loads(store.frozen_runs[parent_id]["spec"])["strategy"]
            derivation = {"parent_run_id": parent_id, "top_n": derived["spec"]["allocation"]["top_n"],
                          "score_unchanged": derived["spec"]["score"] == original["score"],
                          "gate_unchanged": derived["spec"]["gate"] == original["gate"],
                          "risk_score": derived["spec"]["risk_score"], "weighting": derived["spec"]["allocation"]["weighting"],
                          "new_get_evidence_reads": len(store.evidence_reads) - before_reads,
                          "new_model_calls": 0, "new_account_executions": 0, "report": derived["report"]}
            counterfactual = {"injected_revision_plan": review["revision_plan"],
                              "note": "A new valid V8 plan was frozen against the saved parent; original V7 did not emit a plan."}
        record = {"stage": name, "old_context_bytes": len((study / "model_calls" / ids[index] / "prompt.txt").read_bytes()),
                  "counterfactual": counterfactual, "revision_derivation": derivation}
        try:
            generated = build_context(kernel)
            view = generated["packet"]
            (stage_dir / "prompt.txt").write_text(generated["prompt"], encoding="utf-8")
            rows = view["factor_evidence"]
            active_review = next((row["review"] for row in view["paired_results"] if "review" in row), {})
            field_checks = []
            for row in rows:
                supports = row.get("evidence_support", {})
                schema = row.get("table_schema", {})
                references = []
                for factor in row.get("factors", {}).values():
                    for kind in ("ic", "risk"):
                        for data in factor[kind]:
                            references.append(dict(zip(schema[kind], data))["support"])
                for kind in ("correlations", "conditions"):
                    references.extend(dict(zip(schema[kind], data))["support"] for data in row[kind])
                for interaction in row["interactions"]:
                    references.extend(dict(zip(schema["interaction_ic"], data))["support"] for data in interaction["ic"])
                field_checks.append({"factor_ids": list(row["factors"]), "factor_count": len(row["factors"]),
                    "pair_interaction_count": len(row["interactions"]), "condition_rows": len(row["conditions"]),
                    "correlation_rows": len(row["correlations"]), "referenced_support_count": len(references),
                    "all_supports_resolve": all(value in supports for value in references),
                    "support_columns": schema["evidence_support"], "selection": row["selection"],
                    "source_value_validation": check_support_values(row, store.load(row["evidence_id"]))})
            record.update(status="completed", bytes=generated["utf8_bytes"], within_32000=generated["utf8_bytes"] <= 32000,
                          detail_in_full_evidence=view.get("detail_in_full_evidence", []), factor_checks=field_checks,
                          bound_plan_attached="revision_plan" in active_review,
                          bound_plan_parent_attached=active_review.get("revision_plan", {}).get("parent_run_id"),
                          recent_actions=[row["kind"] for row in view["recent_actions"]],
                          prompt_path=str(stage_dir / "prompt.txt"), prompt_sha256=sha(stage_dir / "prompt.txt"))
        except Exception as exc:
            record.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)}, within_32000=False)
            trace = exc.__traceback__
            while trace is not None:
                if trace.tb_frame.f_code is build_context.__code__:
                    rejected = trace.tb_frame.f_locals.get("prompt")
                    rejected_packet = trace.tb_frame.f_locals.get("packet")
                    if rejected is not None:
                        target = stage_dir / "rejected_prompt.txt"
                        target.write_text(rejected, encoding="utf-8")
                        record.update(bytes=len(rejected.encode("utf-8")), rejected_prompt_path=str(target), rejected_prompt_sha256=sha(target))
                    if rejected_packet is not None:
                        record["packet_field_bytes"] = {key: len(serial(value).encode("utf-8")) for key, value in rejected_packet.items()}
                        record["factor_field_bytes"] = [{key: len(serial(value).encode("utf-8")) for key, value in row.items()} for row in rejected_packet["factor_evidence"]]
                        record["recent_actions_after_fallback"] = rejected_packet["recent_actions"]
                    break
                trace = trace.tb_next
        record["writes"] = store.evidence_writes
        results.append(record)
    all_unchanged = all(sha(Path(path)) == value for path, value in sources.items())
    report = {"version": "meta_v8_saved_context_replay_v1", "stages": results,
              "all_stage_contexts_within_budget": all(row["within_32000"] for row in results),
              "all_source_artifacts_unchanged": all_unchanged,
              "numeric_scope": "2016-2020 saved training results only; original exposure/date-range metadata retained",
              "new_model_calls": 0, "new_account_executions": 0, "new_market_data_reads": 0,
              "source_manifest": [{"path": path, "sha256": value} for path, value in sorted(sources.items())],
              "implementation": [{"path": str(path), "sha256": sha(path)} for path in
                                  [ROOT / "src/quanta_agents/meta_v7" / name for name in ("protocol.py", "decision_evidence.py", "revisions.py")]],
              "limitations": ["Mock storage routes actual build_context calls against saved stage state; not a new real model research run.",
                              "The third-stage bound plan is explicitly injected, not an original V7 model output.",
                              "Review context intentionally selects used factors and touching pairs; selection stages must restore all 12 factors.",
                              "A bounded patch need not transmit the whole stored spec to the model; inheritance is checked by the real derivation helper."]}
    report["conclusion"] = {
        "scope": "Three saved real training stages, with one explicitly counterfactual V8 revision plan",
        "stage_bytes": {row["stage"]: row.get("bytes") for row in results},
        "all_within_32000": report["all_stage_contexts_within_budget"],
        "minimum_remaining_bytes": min(32000 - row["bytes"] for row in results if "bytes" in row),
        "saved_metric_values_verified": all(check["source_value_validation"]["all_copied_exactly"]
                                             for row in results for check in row.get("factor_checks", [])),
        "bound_revision_preserves_top_n40_without_get_evidence": bool(results[-1].get("revision_derivation")
            and results[-1]["revision_derivation"]["top_n"] == 40
            and results[-1]["revision_derivation"]["new_get_evidence_reads"] == 0),
        "general_context_size_guarantee": False,
        "cost_or_discovery_improvement_claim": False}
    report["preserved_previous_failed_replays"] = []
    for name in ("context_replay_before_context_dedup", "context_replay_after_first_context_dedup", "context_replay_before_contract_merge"):
        previous = destination.parent / name / "context_replay.json"
        if previous.exists():
            old = read(previous)
            report["preserved_previous_failed_replays"].append({"path": str(previous), "sha256": sha(previous),
                "stage_bytes": {row["stage"]: row.get("bytes") for row in old["stages"]},
                "all_within_budget": old["all_stage_contexts_within_budget"]})
    target = destination.parent / "context_replay.json"
    target.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(serial({"stages": [{k: row.get(k) for k in ("stage", "status", "bytes", "error")} for row in results],
                  "source_unchanged": all_unchanged, "output": str(target)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "output/research/meta_v7_acceptance_20260909/research_study")
    parser.add_argument("--output", type=Path, default=ROOT / "output/research/meta_v8_20260909/context_replay")
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve() or args.source.resolve() in args.output.resolve().parents:
        raise ValueError("Replay writes must be outside the source study")
    run(args.source.resolve(), args.output.resolve())
