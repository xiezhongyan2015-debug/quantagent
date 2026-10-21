"""Read saved V7 artifacts; write a separate, provenance-bound V8 audit.

No market-data loader, model gateway or account executor is imported. The source
SQLite database is opened in read-only mode. Real replay values stop in 2020.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def serial(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def source(path, pointer="", **extra):
    return {"path": str(path.resolve()), "sha256": digest(path), "json_pointer": pointer, **extra}


def bound(value, path, pointer="", **extra):
    return {"value": value, "source": source(path, pointer, **extra)}


def prompt_packet(path):
    return json.loads(path.read_text(encoding="utf-8").split("RESEARCH_STATE_JSON\n", 1)[1])


def difference(old, new, prefix=""):
    if isinstance(old, dict) and isinstance(new, dict):
        rows = []
        for key in sorted(set(old) | set(new)):
            pointer = prefix + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in old or key not in new:
                rows.append({"pointer": pointer, "old_present": key in old, "new_present": key in new,
                             "old": old.get(key), "new": new.get(key)})
            else:
                rows.extend(difference(old[key], new[key], pointer))
        return rows
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return [row for i, (a, b) in enumerate(zip(old, new)) for row in difference(a, b, prefix + "/" + str(i))]
    return [] if old == new else [{"pointer": prefix, "old": old, "new": new}]


def leaf_paths(value, prefix=""):
    if isinstance(value, dict):
        return {p for k, v in value.items() for p in leaf_paths(v, prefix + "/" + k)}
    if isinstance(value, list):
        return {p for i, v in enumerate(value) for p in leaf_paths(v, prefix + "/" + str(i))}
    return {prefix}


def compare_role_fields(report, legacy, view):
    """Check semantic fields after resolving V8's column-labelled support refs."""
    schema = view["table_schema"]
    checked = []

    def check(role, raw, old, encoded, columns):
        row = dict(zip(columns, encoded))
        support = dict(zip(schema["evidence_support"], view["evidence_support"][row["support"]]))
        fields = [k for k in ("observed_days", "calendar_days", "missing_days", "paired_cells", "rank_deficient_days") if k in raw]
        if row["mean_ic"] != raw["mean_ic"]:
            raise ValueError("V8 view changed a saved mean")
        if any(support[k] != raw[k] for k in fields):
            raise ValueError("V8 view changed a saved support field")
        checked.append({"role": role, "mean_equal": True, "raw_support_fields": len(fields),
                        "v7_support_fields_present": sum(k in old for k in fields),
                        "v8_support_fields_present_and_equal": len(fields),
                        "raw_annual_present": bool(raw.get("annual")),
                        "v7_annual_present": "annual" in old,
                        "v8_annual_summary_present": row.get("annual_summary") is not None})

    for identity, raw_factor in report["factors"].items():
        new_factor, old_factor = view["factors"][identity], legacy["factors"][identity]
        for values in new_factor["ic"]:
            horizon = values[0]
            check("return", raw_factor["horizons"][horizon], old_factor["ic"][horizon], values, schema["ic"])
        for values in new_factor["risk"]:
            horizon, name = values[:2]
            check("risk", raw_factor["risk"][horizon][name], {}, values, schema["risk"])
    for role, raw_key, columns in (("correlation", "correlations", "correlations"), ("condition", "conditions", "conditions")):
        for raw, old, values in zip(report[raw_key], legacy[raw_key], view[raw_key], strict=True):
            check(role, raw, old, values, schema[columns])
    for raw, old, new in zip(report["interactions"], legacy["interactions"], view["interactions"], strict=True):
        for values in new["ic"]:
            horizon, name = values[:2]
            check("interaction", raw["horizons"][horizon][name], {}, values, schema["interaction_ic"])
    summaries = {}
    for role in ("return", "risk", "correlation", "condition", "interaction"):
        rows = [r for r in checked if r["role"] == role]
        summaries[role] = {"metrics_checked": len(rows), **{key: sum(r[key] for r in rows) for key in checked[0] if key != "role"}}
    return {"means_copied_exactly": all(r["mean_equal"] for r in checked), "by_role": summaries,
            "selection_limitation": "Annual extrema/sign counts replace full annual rows; dispersion is available only in review detail. Support counts are not independent sample size."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "output/research/meta_v7_acceptance_20260909/research_study")
    parser.add_argument("--output", type=Path, default=ROOT / "output/research/meta_v8_20260909")
    args = parser.parse_args()
    study, output = args.source.resolve(), args.output.resolve()
    if output == study or study in output.parents:
        raise ValueError("Write output outside the original V7 study")
    output.mkdir(parents=True, exist_ok=True)

    database = study / "study.sqlite"
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        calls = [dict(r) for r in db.execute("SELECT id,status,directory,usage,error FROM model_calls ORDER BY created,id")]
        failures = [dict(r) for r in db.execute("SELECT seq,kind,payload FROM events WHERE kind LIKE '%fail%' OR kind LIKE '%error%' ORDER BY seq")]
        event_counts = dict(db.execute("SELECT kind,COUNT(*) FROM events GROUP BY kind"))
    finally:
        db.close()
    # Do not infer a clean execution history from absence of failure events.
    release_path = study.parent / "release_acceptance.json"
    release = read_json(release_path)
    contexts, responses, call_rows = [], [], []
    for row in calls:
        directory = study / "model_calls" / row["id"]
        response_path, prompt_path = directory / "response.json", directory / "prompt.txt"
        response = read_json(response_path)
        response["decoded_payload"] = json.loads(response["payload_json"])
        context = prompt_packet(prompt_path)
        usage = json.loads(row["usage"]) if row["usage"] else {}
        contexts.append(context)
        responses.append(response)
        call_rows.append({"id": row["id"], "status": row["status"], "error": row["error"], "action": response["action"],
                          "usage": usage, "context_manifest": read_json(directory / "context_manifest.json"),
                          "sources": [source(response_path), source(prompt_path, container="RESEARCH_STATE_JSON") ]})
    if len(calls) != 6 or [r["action"] for r in responses] != ["propose_batch", "review_batch", "get_evidence", "propose_batch", "review_batch", "review_validation"]:
        raise ValueError("Saved research differs from the six-call audit contract")

    factor_id = contexts[0]["factor_evidence"][0]["evidence_id"]
    factor_path = study / "evidence" / (factor_id + ".json")
    factors = read_json(factor_path)
    if factors["scope"]["start"] < "2016-01-01" or factors["scope"]["end"] > "2020-12-31":
        raise ValueError("Training packet factor scope outside 2016-2020")
    parent_id = responses[2]["decoded_payload"]["evidence_id"]
    parent_path = study / "evidence" / (parent_id + ".json")
    parent = read_json(parent_path)
    second_spec = responses[3]["decoded_payload"]["specs"][0]
    spec_diff = difference(parent["strategy"], second_spec)
    functional_diff = [r for r in spec_diff if not r["pointer"].startswith(("/metadata", "/name"))]
    retrieved = contexts[3]["recent_actions"][0]
    retrieved_id = retrieved["data"].get("full_evidence_id")
    retrieved_path = study / "evidence" / (retrieved_id + ".json") if retrieved_id else None
    retrieved_source = read_json(retrieved_path) if retrieved_path else None
    query_visibility = {
        "requested_evidence_id": parent_id, "requested_pointer": responses[2]["decoded_payload"]["pointer"],
        "stored_result_contains_strategy": bool(retrieved_source and "strategy" in retrieved_source["data"]["result"]["value"]),
        "next_context_event": retrieved,
        "next_context_contains_parent_strategy": any(a.get("strategy") == parent["strategy"] for b in contexts[3]["paired_results"] for a in b["arms"]),
        "next_context_contains_query_result_value": "result" in retrieved["data"],
        "query_call_tokens": sum(call_rows[2]["usage"].get(k, 0) for k in ("input_tokens", "output_tokens")),
        "sources": [source(parent_path, "/strategy"), source(retrieved_path, "/data/result/value/strategy") if retrieved_path else None,
                    source(study / "model_calls" / calls[3]["id"] / "prompt.txt", "/recent_actions/0/data", container="RESEARCH_STATE_JSON")],
        "interpretation": "Saved query returned the spec, but the next call received only a narrow-query index; causal attribution of drift remains unproven."
    }
    from quanta_agents.meta_v7.protocol import compact_factors
    legacy = compact_factors(factors)
    coverage = {
        "raw_report_bytes": len(serial(factors).encode("utf-8")),
        "v7_compact_bytes": len(serial(legacy).encode("utf-8")),
        "actual_first_call_factor_view_bytes": len(serial(contexts[0]["factor_evidence"][0]).encode("utf-8")),
        "v7_schema_leaf_count": len(leaf_paths(legacy)),
        "actual_first_call_annual_factor_ic_present": any("annual" in s for f in contexts[0]["factor_evidence"][0]["factors"].values() for s in f["ic"].values()),
        "source": source(factor_path),
    }
    try:
        module = importlib.import_module("quanta_agents.meta_v7.decision_evidence")
        function = getattr(module, "compact_factors_v8", None)
        if function is None:
            coverage["v8_status"] = "module_present_function_not_available"
            coverage["v8_available_functions"] = [name for name in dir(module) if not name.startswith("_") and callable(getattr(module, name))]
        else:
            view = function(factors)
            coverage.update(v8_status="compared", v8_compact_bytes=len(serial(view).encode("utf-8")),
                            v8_schema_leaf_count=len(leaf_paths(view)),
                            v8_added_leaf_paths=sorted(leaf_paths(view) - leaf_paths(legacy)),
                            v8_removed_leaf_paths=sorted(leaf_paths(legacy) - leaf_paths(view)),
                            v8_source=source(Path(module.__file__)))
            coverage["semantic_field_comparison"] = compare_role_fields(factors, legacy, view)
    except ModuleNotFoundError as exc:
        if exc.name != "quanta_agents.meta_v7.decision_evidence":
            raise
        coverage["v8_status"] = "module_not_yet_available"

    arms_by_batch = []
    for batch in contexts[4]["paired_results"]:
        arms = []
        for arm in batch["arms"]:
            path = study / "evidence" / (arm["evidence_id"] + ".json")
            evidence = read_json(path)
            if any(not 2016 <= annual["year"] <= 2020 for annual in evidence["annual"]):
                raise ValueError("Non-training annual value in replay")
            fields = ("run_id", "strategy", "summary", "annual", "execution")
            arms.append({"role": arm["role"], **{k: bound(evidence[k], path, "/" + k) for k in fields}})
        arms_by_batch.append({"batch_id": batch["batch_id"], "arms": arms})
    first_batch_id = responses[1]["decoded_payload"]["batch_id"]
    first_batch = next(b for b in arms_by_batch if b["batch_id"] == first_batch_id)
    second_batch = next(b for b in arms_by_batch if b["batch_id"] != first_batch_id)
    equal_spec = json.loads(json.dumps(parent["strategy"]))
    equal_spec["allocation"]["weighting"] = "equal"
    equal_spec["risk_score"] = None
    packet = {
        "version": "meta_v8_saved_training_replay_v1", "scope": {"start": "2016-01-01", "end": "2020-12-31", "role": "previously_exposed_development"},
        "factor_evidence": bound(factors, factor_path), "first_batch": first_batch,
        "first_review": bound(responses[1]["decoded_payload"], study / "model_calls" / calls[1]["id"] / "response.json", "/payload_json", decode_json_string=True),
        "parent_omit2": bound(parent["strategy"], parent_path, "/strategy"),
        "parent_run_id": bound(parent["run_id"], parent_path, "/run_id"),
        "second_batch": second_batch,
        "second_submitted_spec": bound(second_spec, study / "model_calls" / calls[3]["id"] / "response.json", "/payload_json/specs/0", decode_json_string=True),
        "evaluation": {
            "questions": [
                "从第一轮纯动量omit_2父运行只改变allocation.weighting和risk_score，生成等权对照。其余交易规格必须继承，说明epsilon如何处理。",
                "比较父omit_2与第二轮逆波动原规格，指出额外变更；第二轮等权与逆波动结果是否足以识别F7信息的独立价值？说明可识别范围。"
            ],
            "answer_key_do_not_send_to_model": {
                "equal_spec": equal_spec,
                "required_changes": ["/allocation/weighting", "/risk_score"],
                "invariants": {"top_n": 40, "score": parent["strategy"]["score"], "gate": parent["strategy"]["gate"]},
                "epsilon_rule": "Equal weighting removes risk_score entirely; no risk epsilon is estimated or changed.",
                "cross_round_functional_diff": functional_diff,
                "independent_risk_information_identified": False,
                "control_identifies": "Within-round overall allocation-policy contrast; holdings/exposure differ and no matched-exposure control is present.",
                "independence_claim_allowed": False,
                "scoring": {"legal_equal_spec": "Exact equality of all trading fields with equal_spec; ignore descriptive name/metadata only.",
                            "detects_top_n_change": "40 to 20", "detects_epsilon_change": "1e-6 to 1e-8",
                            "detects_score_rewrite": "weighted_sum .5 rank(F1) to rank(F1); may be ranking equivalent, not identical spec",
                            "rejects_cross_round_single_change_claim": True, "rejects_independent_F7_attribution": True,
                            "claims_new_holdout_or_alpha": "Must be false; previous training and no new account."}
            }
        },
        "limitations": ["Stored training evidence replay, not new market evidence or independent holdout.",
                        "No account or model execution; the saved factor report includes source metadata paths, not later-year numeric observations.",
                        "Compare same source evidence and decision budget; a new revision action is an interface-capability change."]
    }
    audit = {
        "version": "meta_v8_saved_trace_audit_v1", "source_study": str(study),
        "database_source": source(database, "", access="sqlite mode=ro", sidecars=[source(p) for p in (Path(str(database) + "-wal"),) if p.exists()]),
        "calls": call_rows, "call_count": len(calls), "failed_paid_calls": sum(r["status"] != "applied" for r in calls),
        "failure_events": failures, "event_kind_counts": event_counts,
        "context_admission_interruptions": bound(release["context_repairs"], release_path, "/context_repairs"),
        "context_admission_interruption_count": len(release["context_repairs"]),
        "failure_record_limitation": "The two pre-call context interruptions are retained in release acceptance, not in study events; no old events were invented.",
        "call3_query_visibility_in_call4": query_visibility,
        "cross_round_spec_diff": spec_diff, "cross_round_trading_spec_diff": functional_diff,
        "cross_round_single_change_identified": False, "role_evidence_comparison": coverage,
        "new_model_calls": 0, "new_account_executions": 0, "new_market_data_reads": 0,
        "replay_packet": {"path": str(output / "real_training_packet.json"), "numeric_scope": "2016-2020 exposed development"}
    }
    for name, value in (("real_training_packet.json", packet), ("saved_trace_audit.json", audit)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(serial({"calls": len(calls), "context_interruptions": len(release["context_repairs"]),
                  "query_result_visible_to_next_call": query_visibility["next_context_contains_query_result_value"],
                  "v8_comparison": coverage.get("v8_status"), "output": str(output)}))


if __name__ == "__main__":
    main()
