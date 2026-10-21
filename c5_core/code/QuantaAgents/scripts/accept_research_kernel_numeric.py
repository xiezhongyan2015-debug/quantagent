"""Explicit real-data engineering acceptance: two accounts, no alpha search.

An invocation owns a new output directory. Repeating it requires a different
--output; originals and failed attempts are never overwritten or resumed.
The default supervisor bounds one worker, retaining every native account table.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OLD_ROOT = ROOT / "output/research/meta_v6_factor_calendar_20260909"
OUTPUT = ROOT / "output/research/research_kernel_acceptance_20260909/numeric_parity"
VERSION = "research_kernel_real_numeric_parity_v3_readonly_cache"
SOURCES = [Path(__file__), *[ROOT / "src/quanta_agents" / value for value in (
    "research_kernel/compiler.py", "research_kernel/execution.py", "research_kernel/assets.py",
    "meta_v6/data.py", "meta_v6/factors.py", "meta/factor_algebra.py", "meta_v6/portfolio.py",
    "meta_v6/portfolio_study.py", "meta_v6/research.py", "meta_v6/jobs.py", "meta_v3/windows_job.py")]]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def proof(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha(path), "bytes": path.stat().st_size}


def write_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def verify(proofs):
    for item in proofs:
        if sha(item["path"]) != item["sha256"]:
            raise ValueError("bound input/source changed: " + item["path"])


def factor(name):
    return {"op": "factor", "id": name}


def node(op, *args, **kwargs):
    return {"op": op, "args": list(args), **kwargs}


def constant(value):
    return {"op": "constant", "value": value}


def declarations(old_root):
    from quanta_agents.meta_v6.factors import canonical_expression
    from quanta_agents.research_kernel.compiler import validate_strategy
    data_file = old_root / "preparation/data_selection.json"
    factor_file = old_root / "factor_index.json"
    combination_file = old_root / "cycles/04_information_application/combination_declaration.json"
    data, index, combination = read(data_file), read(factor_file), read(combination_file)
    if data["authorized_numeric_range"] != ["2015-01-01", "2024-12-31"]:
        raise ValueError("unexpected original numeric scope")
    inputs = [proof(data_file), proof(factor_file), proof(combination_file)]
    for key in ("calendar", "membership"):
        p = proof(data[key + "_path"])
        if p["sha256"] != data[key + "_sha256"]:
            raise ValueError("original date/universe metadata changed")
        inputs.append(p)
    definitions = []
    original_names = {}
    for key, expression, direction in (("F1", "pct_change(lag(close,20),100)", 1),
                                       ("F2", "pct_change(close,5)", -1)):
        rows = [row for row in index["factors"] if row.get("factor_key") == key]
        if len(rows) != 1:
            raise ValueError("original factor identity is not unique")
        row = rows[0]
        path = Path(row["folder"]) / "spec.json"
        original = read(path)
        p = proof(path)
        if not any(item["path"] == p["path"] and item["sha256"] == p["sha256"]
                   for item in row["artifacts"]):
            raise ValueError("original factor spec no longer matches index")
        if (canonical_expression(original["spec"]["expression"]) != canonical_expression(expression)
                or original["original"]["direction"] != direction or row["direction"] != direction):
            raise ValueError("original formula/direction differs from parity declaration")
        inputs.append(p)
        original_names[key] = row["name"]
        definitions.append({"id": key, "expression": expression, "roles": ["return"],
                            "source": p, "metadata": {"acceptance_only": True,
                            "original_direction_applied_by_compiler_once": direction}})
    i1 = [item for item in combination["specs"] if item["name"] == "I1"]
    if len(i1) != 1:
        raise ValueError("original I1 declaration is not unique")
    i1 = i1[0]
    wanted = {"top_n": 20, "gross_exposure": 1., "max_stock_weight": .05,
              "weighting": "equal", "rebalance_sessions": 5,
              "rebalance_schedule": "weekly_last_session", "membership_buffer": 40}
    if (any(i1[key] != value for key, value in wanted.items()) or i1["market_filter"] != "none"
            or i1["crowding_gate_factor"] or i1["factor_weights"] != {
                original_names["F1"]: .5, original_names["F2"]: -.5}):
        raise ValueError("I1 economic rules differ from fixed R2 parity case")
    score = node("weighted_sum", node("rank", factor("F1")),
                 node("rank", node("negate", factor("F2"))), weights=[.5, .5])
    baseline = validate_strategy({"version": 1, "name": "R2_I1_numeric_parity", "score": score,
                                 "allocation": wanted, "metadata": {"engineering_acceptance_only": True}})
    for key, expression, role in (("X3", "rolling_mean(volume,10)", "condition"),
                                   ("X4", "rolling_std(pct_change(close,1),20)", "risk"),
                                   ("X5", "(close-open)/open", "interaction")):
        definitions.append({"id": key, "expression": expression, "roles": [role],
                            "metadata": {"engineering_acceptance_only": True,
                                         "no_alpha_or_financial_claim": True}})
    forms = []
    for ids in (["F1", "F2", "X3"], ["F1", "F2", "X3", "X4", "X5"]):
        expressions = [node("rank", node("negate", factor(key)) if key == "F2" else factor(key)) for key in ids]
        forms.append(validate_strategy({"name": f"{len(ids)}_operand_targets_only", "allocation": wanted,
            "score": node("weighted_sum", *expressions, weights=[1 / len(ids)] * len(ids)),
            "gate": node("where", node("gt", node("rank", factor("X3")), constant(.8)),
                         constant(.5), constant(1)),
            "metadata": {"purpose": "language_and_target_coverage_only", "alpha_claim": False}}))
    return {"data": data, "input_proofs": inputs, "original_names": original_names,
            "original_portfolio": i1, "baseline_strategy": baseline,
            "asset_definitions": definitions, "target_only_strategies": forms,
            "expected_panel_fingerprint": index["scope"]["panel_fingerprint"],
            "expected_data_fingerprint": index["scope"]["data_fingerprint"]}


def frame_hash(frame):
    import pandas as pd
    # pandas hashes columns individually. Leaving the full selection-plan attrs
    # attached deep-copies that large metadata once per column. Hash numerical
    # content on one shallow export; attrs have a separately preserved proof.
    export = frame.copy(deep=False)
    export.attrs = {}
    h = hashlib.sha256()
    h.update(json.dumps({"shape": list(export.shape), "columns": list(export.columns),
                         "dtypes": [str(value) for value in export.dtypes],
                         "index_type": type(export.index).__name__, "index_name": export.index.name},
                        sort_keys=True, ensure_ascii=False).encode("utf-8"))
    h.update(pd.util.hash_pandas_object(export, index=True).to_numpy().tobytes())
    return h.hexdigest()


def check_hash_export():
    import numpy as np
    import pandas as pd
    values = pd.DataFrame([[1., np.nan], [3., 4.]], index=pd.date_range("2019-01-01", periods=2),
                          columns=["sh600000", "sh600001"])
    values.attrs = {"selection_plans": {"2019-01-01": {"order": list(range(2000)), "gate": [1., .5]}}}
    plans = json.loads(json.dumps(values.attrs))
    empty = values.copy(deep=False)
    empty.attrs = {}
    assert frame_hash(values) == frame_hash(empty), "content hash must ignore separately preserved attrs"
    assert values.attrs == plans, "hash export must not mutate original plans"
    return {"status": "passed", "content_hash_equal_with_empty_attrs": True,
            "original_attributes_preserved": True, "numeric_market_reads": 0, "account_executions": 0}


def read_saved_frames(source, definitions, expected_fingerprint, reference=None):
    """Read original verified cache payloads without opening/writing its SQLite."""
    import numpy as np
    import pandas as pd
    from quanta_agents.research_kernel import assets
    started = time.perf_counter()
    source = Path(source).resolve()
    original = read(source.parent / "asset_resolution.json")
    calculator = assets._calculator_identity()
    if (original.get("complete") is not True or original["calculator"] != calculator
            or original["panel_fingerprint"] != expected_fingerprint):
        raise ValueError("original factor cache calculator or panel identity differs")
    frames, entries = {}, {}
    for definition in definitions:
        name = definition["id"]
        identity = {"expression": assets.factors.canonical_expression(definition["expression"]),
                    "calculator": calculator, "panel_fingerprint": expected_fingerprint}
        key = assets._digest(identity)
        if original["assets"][name]["cache_key"] != key:
            raise ValueError("original asset cache expression identity differs")
        folder = source / "score_cache" / key
        manifest = assets._read_json(folder / "manifest.json")
        digest = manifest.get("payload_sha256", "")
        if (manifest.get("identity") != identity or manifest.get("key") != key
                or not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            raise ValueError("original cache manifest is not bound to the declared factor")
        payload = folder / f"scores.{digest}.parquet"
        if assets._sha(payload) != digest:
            raise ValueError("original score payload hash differs")
        frame = pd.read_parquet(assets._io(payload))
        if assets._sha(payload) != digest:
            raise ValueError("original score payload changed during read")
        if (list(frame.shape) != manifest["shape"]
                or [list(frame.index.names), list(frame.columns.names)] != manifest["axis_names"]
                or any(str(dtype) != "float64" for dtype in frame.dtypes)
                or np.isinf(frame.to_numpy()).any() or not frame.index.is_unique
                or not frame.index.is_monotonic_increasing or not frame.columns.is_unique
                or frame.index.min() < pd.Timestamp("2015-01-01") or frame.index.max() > pd.Timestamp("2024-12-31")):
            raise ValueError("saved factor axes, numeric dtype or date scope invalid")
        if reference is None:
            reference = frame
        if (not frame.index.equals(reference.index) or not frame.columns.equals(reference.columns)
                or frame.index.names != reference.index.names or frame.columns.names != reference.columns.names):
            raise ValueError("saved scores do not exactly align with the shared panel")
        frame.attrs = {}
        frames[name] = frame
        entries[name] = {"status": "resolved", "cache_hit": True, "cache_key": key,
                         "original_payload": {**assets._proof(payload), "bytes": assets._io(payload).stat().st_size}}
    return frames, {"complete": True, "computed": 0, "cache_hits": len(frames), "cache_misses": 0,
        "resolved": len(frames), "requested": len(definitions), "alias_reuses": 0, "unavailable": {},
        "panel_fingerprint": expected_fingerprint, "calculator": calculator, "assets": entries,
        "source_resolution": proof(source.parent / "asset_resolution.json"),
        "readonly_saved_frame_reuse": True, "direction_applied": False,
        "engine_cache": {"new_engine_created": False, "node_evaluations": 0, "label_evaluations": 0},
        "duration_seconds": time.perf_counter() - started}


def save_targets(folder, targets):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "targets.parquet"
    if path.exists():
        raise ValueError("refusing to overwrite targets")
    export = targets.copy(deep=False)
    export.attrs = {}
    export.to_parquet(path)
    write_once(folder / "target_attributes.json", targets.attrs)
    return {"data": proof(path), "attributes": proof(folder / "target_attributes.json"),
            "content_sha256": frame_hash(targets)}


def worker(args):
    import numpy as np
    import pandas as pd
    from pandas.testing import assert_frame_equal
    from quanta_agents.meta_v6.data import load_market_panel
    from quanta_agents.meta_v6.portfolio import AccountPolicy, DailyAccount, PortfolioSpec, target_weights
    from quanta_agents.meta_v6.portfolio_study import save_account
    from quanta_agents.research_kernel.assets import AssetRegistry
    from quanta_agents.research_kernel.compiler import strategy_id
    from quanta_agents.research_kernel.execution import execute_strategy, _targets
    out, old_root = args.output.resolve(), args.old_root.resolve()
    intent_path = out / "acceptance_intent.json"
    if sha(intent_path) != args.intent_sha256:
        raise ValueError("worker is not bound to the original acceptance intent")
    intent = read(intent_path)
    all_proofs = intent["source_proofs"] + intent["declaration"]["input_proofs"] + intent["cache_reuse_proofs"]
    verify(all_proofs)
    declared = intent["declaration"]
    started, timings = time.perf_counter(), {}
    state = {"accounts_started": 0, "accounts_completed": 0, "model_calls": 0}
    def checkpoint(stage):
        event = {"stage": stage, "elapsed_seconds": time.perf_counter() - started,
                 "created_at": datetime.now(timezone.utc).isoformat(), **state}
        with (out / "worker_progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush(); os.fsync(stream.fileno())
        print(json.dumps(event), flush=True)
    def cancelled():
        return time.perf_counter() - started >= args.timeout_seconds - 5
    try:
        checkpoint("loading_panel")
        data = declared["data"]
        before = time.perf_counter()
        panel = load_market_panel(data["data_root"], start="2015-01-01", end="2024-12-31",
            authorized_start="2015-01-01", authorized_end="2024-12-31",
            calendar_path=data["calendar_path"], membership_path=data["membership_path"],
            cache_dir=old_root / "preparation/market_panel_cache")
        timings["panel_load_seconds"] = time.perf_counter() - before
        if panel.dates.min().year < 2015 or panel.dates.max().year > 2024:
            raise ValueError("numeric panel escaped the authorized 2015-2024 scope")
        if panel.fingerprint() != declared["expected_panel_fingerprint"]:
            raise ValueError("loaded actual panel differs from original panel content identity")
        write_once(out / "panel_receipt.json", {"shape": list(panel.eligible.shape),
            "panel_fingerprint": panel.fingerprint(), "provenance": panel.provenance,
            "load_metrics": panel.load_metrics, "numeric_2025_read": False})
        checkpoint("resolving_five_assets_once")
        registry = AssetRegistry(out / "assets")
        definitions = [registry.register(item) for item in declared["asset_definitions"]]
        write_once(out / "registered_assets.json", definitions)
        before = time.perf_counter()
        if intent["reuse_asset_cache"] is not None:
            scores, metrics = read_saved_frames(intent["reuse_asset_cache"], definitions,
                                                declared["expected_data_fingerprint"], panel.eligible)
        else:
            scores, metrics = registry.resolve([item["id"] for item in definitions], panel)
        timings["asset_resolve_seconds"] = time.perf_counter() - before
        write_once(out / "asset_resolution.json", metrics)
        if not metrics["complete"] or set(scores) != {"F1", "F2", "X3", "X4", "X5"}:
            raise ValueError("not all declared parity/target-only assets resolved")
        if metrics["panel_fingerprint"] != declared["expected_data_fingerprint"]:
            raise ValueError("factor engine input content differs from original factor scope")
        score_content = {key: frame_hash(value) for key, value in scores.items()}
        policy = AccountPolicy()
        old_spec = PortfolioSpec(**declared["original_portfolio"])
        old_scores = {declared["original_names"][key]: scores[key] for key in ("F1", "F2")}
        checkpoint("old_account")
        before = time.perf_counter()
        old_targets = target_weights(panel, old_scores, old_spec, start="2016-01-01", end="2020-12-31")
        old_target_proof = save_targets(out / "old_account", old_targets)
        state["accounts_started"] += 1
        checkpoint("old_account_native_started")
        old = DailyAccount(panel, policy).run(old_targets, start="2016-01-01", end="2020-12-31", cancelled=cancelled)
        save_account(out / "old_account", old)
        state["accounts_completed"] += 1
        timings["old_targets_account_save_seconds"] = time.perf_counter() - before
        checkpoint("new_account")
        before = time.perf_counter()
        state["accounts_started"] += 1
        checkpoint("new_account_native_started")
        new = execute_strategy(panel, {key: scores[key] for key in ("F1", "F2")}, declared["baseline_strategy"],
                               start="2016-01-01", end="2020-12-31", policy=policy, cancelled=cancelled)
        new_target_proof = save_targets(out / "new_account", new["targets"])
        save_account(out / "new_account", new)
        write_once(out / "new_account/diagnostics.json", new["diagnostics"])
        write_once(out / "new_account/normalized_strategy.json", new["normalized_spec"])
        state["accounts_completed"] += 1
        timings["new_targets_account_save_seconds"] = time.perf_counter() - before
        checkpoint("exact_table_comparison")
        comparisons = []
        for key, left, right in [(key, old[key], new[key]) for key in ("daily", "trades", "annual")] + [
                ("targets", old_targets, new["targets"])]:
            message = None
            try:
                # The comparison helper also traverses columns. Numerical
                # exports prevent the same metadata-copy cost here; plans get
                # their independent exact equality test immediately below.
                left_export, right_export = left.copy(deep=False), right.copy(deep=False)
                left_export.attrs, right_export.attrs = {}, {}
                assert_frame_equal(left_export, right_export, check_exact=True, check_dtype=True)
            except AssertionError as exc:
                message = str(exc)
            comparisons.append({"table": key, "exact": message is None,
                "old_shape": list(left.shape), "new_shape": list(right.shape),
                "old_content_sha256": frame_hash(left), "new_content_sha256": frame_hash(right),
                "index_exact": left.index.equals(right.index), "columns_exact": left.columns.equals(right.columns),
                "dtypes_exact": left.dtypes.equals(right.dtypes), "difference": message})
        plans_exact = old_targets.attrs["selection_plans"] == new["targets"].attrs["selection_plans"]
        # Compatibility metadata intentionally differs; economic selection plans must not.
        summary_exact = all(old["summary"][key] == new["summary"][key]
                            for key in old["summary"] if key != "duration_seconds")
        write_once(out / "baseline_comparison.json", {"comparisons": comparisons,
            "selection_plans_exact": plans_exact, "summary_exact_except_elapsed": summary_exact,
            "policy_exact": old["policy"] == new["policy"],
            "old_targets": old_target_proof, "new_targets": new_target_proof})
        checkpoint("three_and_five_factor_targets_only")
        supported = []
        for form in declared["target_only_strategies"]:
            before = time.perf_counter()
            targets, coverage = _targets(panel, scores, form, start="2016-01-01", end="2020-12-31", cancelled=cancelled)
            values = targets.to_numpy()
            if not np.isfinite(values).all() or (values < 0).any() or (values.sum(axis=1) > 1 + 1e-10).any():
                raise ValueError("target-only form violated finite long-only capital bounds")
            target_proof = save_targets(out / form["name"], targets)
            supported.append({"name": form["name"], "strategy_id": strategy_id(form),
                "supported": True, "coverage": coverage, "target_proof": target_proof,
                "account_executions": 0, "IC_or_forward_return_computed": False,
                "alpha_claim": False, "duration_seconds": time.perf_counter() - before})
        verify(all_proofs)
        passed = (all(row["exact"] for row in comparisons) and plans_exact and summary_exact
                  and old["policy"] == new["policy"])
        report = {"version": VERSION, "status": "passed" if passed else "failed",
            "purpose": "real-data implementation parity, not strategy research or new alpha evidence",
            "intent_sha256": args.intent_sha256, "source_proofs": intent["source_proofs"],
            "numeric_scope": ["2015-01-01", "2024-12-31"], "account_scope": ["2016-01-01", "2020-12-31"],
            "panel_shape": list(panel.eligible.shape), "panel_load_metrics": panel.load_metrics,
            "score_content_sha256": score_content, "asset_resolution": metrics,
            "comparisons": comparisons, "selection_plans_exact": plans_exact,
            "summary_exact_except_elapsed": summary_exact, "policy": asdict(policy),
            "baseline_metrics": {key: new["summary"][key] for key in (
                "mean_full_year_sharpe", "worst_full_year_sharpe", "return", "sharpe", "max_drawdown",
                "fees", "slippage", "mean_exposure", "turnover")},
            "extra_target_forms": supported, "timings": timings,
            "worker_wall_seconds": time.perf_counter() - started, **state,
            "baseline_factor_assets": 2, "additional_target_only_factor_assets": 3,
            "actual_factor_computations_this_attempt": metrics["computed"],
            "cache_reuse_proofs": intent["cache_reuse_proofs"],
            "no_new_account_for_three_or_five_factor_forms": True,
            "financial_success": False, "independent_holdout": False,
            "numeric_2025_read": False, "source_hashes_verified_after_execution": True,
            "limitations": ["Both accounts use one shared adjusted-unit development approximation and the same input factors",
                "Exact agreement is implementation compatibility, not independent financial validation",
                "Three/five factor target generation carries no performance or discovery claim",
                "Logical date predicates and cached 2015-2024 arrays exclude 2025 values; Parquet page-level decoding isolation is not claimed"],
            "artifacts": [proof(path) for path in out.rglob("*") if path.is_file()
                          and "jobs" not in path.relative_to(out).parts and path.name != "worker_progress.jsonl"]}
        write_once(out / "numeric_parity_report.json", report)
        checkpoint(report["status"])
        return 0 if passed else 1
    except BaseException as exc:
        write_once(out / "numeric_parity_failure.json", {"status": "failed", "type": type(exc).__name__,
            "message": str(exc), "traceback": traceback.format_exc(), "intent_sha256": args.intent_sha256,
            "worker_wall_seconds": time.perf_counter() - started, **state,
            "financial_success": False, "partial_outputs_retained": True})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-root", type=Path, default=OLD_ROOT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--timeout-seconds", type=float, default=300.)
    parser.add_argument("--reuse-asset-cache", type=Path,
                        help="Read verified frames from a previous completed asset cache; never reuse an account")
    parser.add_argument("--self-check", action="store_true", help="Run only the synthetic hash/attrs regression check")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--intent-sha256", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(check_hash_export()))
        return 0
    if args.worker:
        return worker(args)
    from quanta_agents.meta_v6.jobs import run_job
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    helper_check = check_hash_export()
    declaration = declarations(args.old_root.resolve())
    reuse = args.reuse_asset_cache.resolve() if args.reuse_asset_cache is not None else None
    reuse_proofs = []
    if reuse is not None:
        if not reuse.is_dir() or not (reuse / "assets.sqlite").is_file():
            raise ValueError("expected completed asset registry directory for cache reuse")
        reuse_proofs = [proof(path) for path in reuse.rglob("*") if path.is_file()]
        resolution = reuse.parent / "asset_resolution.json"
        if not resolution.is_file() or read(resolution).get("complete") is not True:
            raise ValueError("prior asset resolution must have completed before reuse")
        reuse_proofs.append(proof(resolution))
        for name in ("registered_assets.json", "acceptance_intent.json", "supervisor_receipt.json", "acceptance_script_original.py"):
            prior = reuse.parent / name
            if prior.exists():
                reuse_proofs.append(proof(prior))
    intent = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "declaration": declaration, "source_proofs": [proof(path) for path in SOURCES],
        "reuse_asset_cache": str(reuse) if reuse is not None else None, "cache_reuse_proofs": reuse_proofs,
        "helper_self_check": helper_check,
        "model_calls": 0, "maximum_account_executions": 2, "implicit_retry": False,
        "timeout_seconds": args.timeout_seconds, "account_capital_each": 1_000_000.,
        "cost_policy": "original AccountPolicy default, cost_multiplier=1",
        "purpose": "explicitly authorized real-data implementation acceptance; not a new research cycle"}
    intent_path = out / "acceptance_intent.json"
    write_once(intent_path, intent)
    command = [sys.executable, "-u", str(Path(__file__).resolve()), "--worker", "--old-root",
               str(args.old_root.resolve()), "--output", str(out), "--timeout-seconds", str(args.timeout_seconds),
               "--intent-sha256", sha(intent_path)]
    job = run_job(command, cwd=ROOT, output_dir=out / "jobs/numeric_parity", timeout_seconds=args.timeout_seconds,
                  max_output_bytes=64 * 1024**2, env={"PYTHONIOENCODING": "utf-8"})
    good = job["status"] == "completed" and job.get("all_owned_processes_exited") is True
    report = read(out / "numeric_parity_report.json") if good and (out / "numeric_parity_report.json").exists() else None
    good = good and report is not None and report["status"] == "passed"
    summary = {"status": "passed" if good else "failed", "job_status": job["status"],
               "all_owned_processes_exited": job.get("all_owned_processes_exited"),
               "report_path": str(out / "numeric_parity_report.json") if report else None,
               "model_calls": 0, "financial_success": False}
    write_once(out / "supervisor_receipt.json", {**summary,
        "job_receipt": proof(out / "jobs/numeric_parity/job_result.json"),
        "report": proof(out / "numeric_parity_report.json") if report else None})
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
