"""Synthetic contract fixtures only; never actual trading/qualification results."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gru_research import v4_actual_evidence as v


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def reference(path):
    return {"path": str(path.resolve()), "sha256": sha(path.read_bytes())}


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, allow_nan=False) + "\n")
    return reference(path)


def journal(days):
    events = []
    def add(kind, d, payload):
        body = {"kind": kind, "day": d, "version": "INITIAL", "payload": payload,
                "previous_hash": events[-1]["hash"] if events else "GENESIS"}
        body["hash"] = sha(json.dumps(body, sort_keys=True, default=str).encode())
        events.append(body)
    add("INITIALIZE", None, {"capital": "1000000", "source": "EXPLICIT_SYNTHETIC_FIXTURE"})
    for d in days:
        add("SESSION_OPEN", d, {})
        add("SESSION_CLOSE", d, {})
    marks = [{"date": d, "nav": "1000000", "cash": "1000000", "listed_stock_value": "0",
        "unlisted_share_claim": "0", "cash_receivable": "0", "accounting_residual": "0",
        "violations": [], "net_nav_lower_bound": "1000000", "net_nav_upper_bound": "1000000",
        "cash_tax_net_bounds_complete": True, "unresolved_tax_events": [],
        "net_NAV_contract_compatible": False, "economic_target_accepted": False} for d in days]
    return {"declared_valuation_days": days, "initial_scenario_capital": "1000000",
        "account_event_ledger": events, "account_event_ledger_hash": events[-1]["hash"],
        "completed_marks": marks, "closing_quantity": {}, "outcomes": [],
        "provider_execution_events": [], "sealed_minute_source_accesses": [],
        "failure": None, "status": "COMPLETE_DIAGNOSTIC_ONLY", "economic_target_accepted": False}


def summary(replay, basis, days):
    windows = {}
    bounds = {k: "0" for k in ("same_trading_path_net_return_lower", "same_trading_path_net_return_upper",
        "arithmetic_net_excess_percentage_points_lower", "arithmetic_net_excess_percentage_points_upper",
        "wealth_relative_net_excess_return_lower", "wealth_relative_net_excess_return_upper")}
    bounds["scope"] = v.BOUND_SCOPE
    for name, (a, z) in v.WINDOWS.items():
        count = sum(a <= d <= z for d in days)
        windows[name] = {
            "anchor": a, "end": z, "reset_account": False, "interval_not_annualized": True,
            "expected_mark_count": count, "observed_mark_count": count, "full_mark_and_execution_coverage": True,
            "scenario_values": {k: "0" for k in ("account_scenario_return", "csi500_price_return",
                "arithmetic_excess_percentage_points", "wealth_relative_excess_return")},
            "same_path_net_tax_bounds": deepcopy(bounds) if basis == "tax_upper_reserve" else None,
            "point_net_excess_return": None, "point_net_target_accepted": False,
            "scenario_max_drawdown": "0", "csi500_price_max_drawdown": "0",
            "filled_buy_notional_cny": "0", "filled_sell_notional_cny": "0",
            "cumulative_gross_turnover_previous_close_nav": "0", "cumulative_one_way_half_gross_turnover": "0",
            "turnover_unknown_orders": [], "trading_fees_cny": {k: "0" for k in ("commission", "transfer", "stamp", "total")},
            "slippage_unknown_orders": [], "slippage_vs_raw_twap_cny": "0", "slippage_already_in_fill_NAV_not_added_again": True,
            "eod_risk_violations": [], "postfill_risk_violations": [], "frozen_risk_policy_satisfied": True,
            "missing_execution_windows": [], "prior_execution_evidence_affect_anchor": [],
            "exposed_unresolved_ex_references": [], "prior_ex_reference_exposures_affect_anchor": [],
            "instrument_qualification_exposures": [], "prior_instrument_qualification_exposures_affect_anchor": [],
            "instrument_tax_fee_scope_complete": True, "full_mark_execution_and_instrument_scope_complete": True,
            "economic_comparison_eligible": basis == "tax_upper_reserve", "all_PLAN_acceptance_complete": False,
        }
    return {"account_basis": basis, "replay_status": replay["status"], "continuous_account": True,
        "windows": windows, "source_and_minute_hashes_verified_before_and_after_run": True,
        "instrument_gate_is_independent_of_reference_formula_gate": True, "instrument_qualification_exposures": [],
        "ex_reference_exposures": [], "all_PLAN_acceptance_complete": False, "point_net_target_accepted": False}


def packet_fixture(root):
    """Build 36 sparse synthetic accounts; public real inspection rejects this kind."""
    root = root.resolve()
    days = ["2025-01-02", "2025-01-03", "2025-12-31", "2026-09-23"]
    artifacts = {}
    artifacts["calendar"] = save(root / "calendar.json", {"dates": days})
    tail = int(datetime(2026, 9, 24, 1, 31, tzinfo=timezone.utc).timestamp() * 1e9)
    artifacts["tail_calendar"] = save(root / "tail.json", {"next_known_execution_time": tail})
    benchmark = root / "benchmark.csv"
    benchmark.write_text("date,close,index_code,benchmark_variant\n" + "".join(
        d + ",100,000905,PRICE_ONLY_SYNTHETIC_FIXTURE\n" for d in ["2024-12-31"] + days))
    artifacts["benchmark"] = reference(benchmark)
    checkpoint_hashes, models = {}, {}
    for model in v.MODELS:
        if model == "selected_full": continue
        models[model] = [str(root / "declared_only_weights" / model / ("seed_" + str(s)) / "best.pt") for s in (11, 22, 33, 44, 55)]
        for path in models[model]: checkpoint_hashes[path] = sha(path.encode())
    models["selected_full"] = models["fixed_label_4"]
    selection = {"validation_year": 2024, "minimum_gain": .001, "selected_method": "fixed_4", "selected_label": 4, "blo_label": 4,
                 "alternatives": {k: {"mean_ic": 0} for k in ("fixed_1", "fixed_2", "fixed_3", "fixed_4", "blo")}}
    identity_body = {"data": "1" * 64, "config": {"seeds": [11, 22, 33, 44, 55]}, "selection": selection, "hashes": checkpoint_hashes}
    freeze_id = sha(json.dumps(identity_body, sort_keys=True).encode())[:20]
    freeze = {"dataset_fingerprint": identity_body["data"], "config": identity_body["config"], "selection": selection,
              "checkpoint_hashes": checkpoint_hashes, "evaluation_models": models, "freeze_id": freeze_id,
              "frozen_at_utc": "2026-10-01T00:00:00Z", "no_test_driven_tuning": True}
    artifacts["freeze"] = save(root / "freeze.json", freeze)
    files = {**checkpoint_hashes, artifacts["freeze"]["path"]: artifacts["freeze"]["sha256"]}
    score_receipts, score_csvs, reports, exports = {}, {}, {}, {m: [] for m in v.MODELS}
    for split in v.SPLITS:
        for model in v.MODELS:
            key = split + "/" + model
            raw = root / "scores" / split / (model + "_raw.csv")
            corrected = root / "scores" / split / (model + "_corrected.csv")
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text("symbol,signal_time,earliest_execution_time,score,percentile,model_version,data_quality_flag\n")
            corrected.write_bytes(raw.read_bytes())
            score_csvs[key] = {"raw": reference(raw), "corrected": reference(corrected)}
            report = {"status": "complete_export", "research_evidence": False, "full_PLAN_complete": False,
                "raw_csv": str(raw), "raw_sha256": sha(raw.read_bytes()), "raw_preserved": True,
                "corrected_csv": str(corrected), "corrected_sha256": sha(corrected.read_bytes()),
                "model_version": freeze_id + ":" + model, "dataset_fingerprint": freeze["dataset_fingerprint"],
                "scores_percentiles_versions_changed": False, "inference_performed": False, "labels_or_IC_metrics_read": False,
                "unresolved_original_score_sections": [], "sections": []}
            score_receipts[key] = save(corrected.with_suffix(".receipt.json"), report)
            reports[key] = {k: value for k, value in report.items() if k != "sections"}
            exports[model].append({"split": split, "raw_path": str(raw), "raw_sha256": report["raw_sha256"],
                "csv_path": str(corrected), "csv_sha256": report["corrected_sha256"], "unresolved_original_score_sections": []})
            files[str(raw)] = report["raw_sha256"]; files[str(corrected)] = report["corrected_sha256"]
    export = {"freeze_id": freeze_id, "freeze_sha256": artifacts["freeze"]["sha256"], "export_code_sha256": v.EXPORT_SOURCE_SHA,
        "verified_input_hashes": {k: "0" * 64 for k in ("X.npy", "hour_complete.npy", "members.npy", "industry.npy", "times.npy", "earliest_execution_times.npy", "symbols.json")},
        "reports": reports, "status": "complete_export"}
    artifacts["score_export_manifest"] = save(root / "score_export_manifest.json", export)
    files[artifacts["score_export_manifest"]["path"]] = artifacts["score_export_manifest"]["sha256"]
    immutable = {artifacts[k]["path"]: artifacts[k]["sha256"] for k in ("calendar", "tail_calendar", "benchmark")}
    seal = {"immutable_files": immutable, "minute_raw_day_files": {str(root / "declared_only_source.parquet"): "0" * 64}}
    artifacts["source_seal"] = save(root / "seal.json", seal)
    pairs = {"pair_" + str(i): {"model_a": v.MODELS[i], "model_b": "fixed_label_4"} for i in range(9)}
    protocol = {"models": list(v.MODELS), "windows": {k: list(value) for k, value in v.WINDOWS.items()},
        "official_benchmark_csv_sha256": artifacts["benchmark"]["sha256"], "contrasts": pairs,
        "cdr_historical_universe_scope": {"historical_cdr_symbols": ["sh689009"]}}
    artifacts["protocol"] = save(root / "protocol.json", protocol)
    driver = {"comparison_protocol_sha256": artifacts["protocol"]["sha256"], "source_seal_sha256": artifacts["source_seal"]["sha256"],
              "driver_source_hashes": {str(root / "declared_only_driver.py"): "2" * 64}}
    artifacts["driver_receipt"] = save(root / "driver.json", driver)
    manifest = {"freeze_id": freeze_id, "freeze_sha256": artifacts["freeze"]["sha256"], "models": list(v.MODELS),
        "model_versions": {m: freeze_id + ":" + m for m in v.MODELS}, "test_winner_selection": False,
        "frozen_files": files, "score_exports": exports, "comparison_protocol_sha256": artifacts["protocol"]["sha256"],
        "source_seal_sha256": artifacts["source_seal"]["sha256"], "driver_receipt_sha256": artifacts["driver_receipt"]["sha256"]}
    artifacts["input_manifest"] = save(root / "input_manifest.json", manifest)
    results, records, runs, statuses = {}, {}, [], {}
    coverage = {}
    for model in v.MODELS:
        coverage[model] = save(root / (model + "_score_coverage.json"),
                              {"continuous_account_trading_score_coverage_by_window": {k: True for k in v.WINDOWS}})
        results[model] = {}; records[model] = {}
        for scenario in v.SCENARIOS:
            results[model][scenario] = {}; records[model][scenario] = {}
            for basis in v.BASES:
                replay = journal(days)
                replay.update(run_id=freeze_id + ":" + ":".join((model, scenario, basis)), model=freeze_id + ":" + model, cost_scenario=scenario)
                sm = summary(replay, basis, days)
                dest = root / "runs" / model / scenario / basis
                rr = save(dest / "replay.json", replay); sr = save(dest / "summary.json", sm)
                runs.append({"model": model, "scenario": scenario, "basis": basis, "replay": rr, "summary": sr})
                results[model][scenario][basis] = sm
                records[model][scenario][basis] = {"replay_path": rr["path"], "replay_sha256": rr["sha256"], "summary_sha256": sr["sha256"], "status": replay["status"]}
                statuses[(model, scenario, basis)] = replay["status"]
    contrasts = {name: {"model_a": spec["model_a"], "model_b": spec["model_b"], "resolver_used_2024_only": True,
        "rows": [{"cost_scenario": s, "account_basis": b, "window": w, "point_net_improvement_accepted": False,
                  "partial_prefix_or_subset_winner": False, "both_full_fixed_window": True,
                  "risk_and_net_bounds_eligible": b == "tax_upper_reserve",
                  "scenario_difference": {k: "0" for k in ("account_scenario_return", "arithmetic_excess_percentage_points", "wealth_relative_excess_return")},
                  "same_path_net_difference_bounds": {k: "0" for k in ("arithmetic_net_excess_difference_percentage_points_lower", "arithmetic_net_excess_difference_percentage_points_upper")}
                    if b == "tax_upper_reserve" else None} for s in v.SCENARIOS for b in v.BASES for w in v.WINDOWS]}
        for name, spec in pairs.items()}
    comparison = {"status": "ALL_NINE_FIXED_MODELS_REPLAYED_RESEARCH_ONLY", "models": list(v.MODELS), "freeze_id": freeze_id,
        "model_versions": manifest["model_versions"], "fixed_2024_selected_model": "selected_full", "test_winner_selected": False,
        "comparison_protocol_sha256": artifacts["protocol"]["sha256"], "source_seal_sha256": artifacts["source_seal"]["sha256"],
        "driver_receipt_sha256": artifacts["driver_receipt"]["sha256"], "input_manifest_sha256": artifacts["input_manifest"]["sha256"],
        "results": results, "artifacts": records, "contrasts": contrasts, "economic_target_accepted": False,
        "all_PLAN_acceptance_complete": False, "prospective60session_acceptance_complete": False}
    artifacts["comparison"] = save(root / "comparison.json", comparison)
    stdout = root / "stdout.log"
    stdout.write_text("".join("BEGIN " + freeze_id + ":" + ":".join(key) + "\nEND " + freeze_id + ":" + ":".join(key) + " " + statuses[key] + "\n" for key in v.EXPECTED_RUNS))
    artifacts["stdout"] = reference(stdout)
    guard = v.merge_maps(seal["immutable_files"], seal["minute_raw_day_files"], driver["driver_source_hashes"], files,
                        {artifacts[k]["path"]: artifacts[k]["sha256"] for k in ("protocol", "source_seal", "driver_receipt", "input_manifest")})
    wrapper = {"schema_version": 1, "evidence_kind": "SYNTHETIC_WRAPPER_FIXTURE", "wrapper_source_sha256": "3" * 64,
        "synthetic": True, "actual_hashes_measured": True, "stdout_captured_verbatim": True, "exit_code": 0,
        "before_hashes": guard, "after_hashes": guard, "stdout_sha256": artifacts["stdout"]["sha256"],
        "comparison_sha256": artifacts["comparison"]["sha256"], "before_captured_at_utc": "2026-10-01T00:00:00+00:00",
        "started_at_utc": "2026-10-01T00:00:01+00:00", "finished_at_utc": "2026-10-01T00:01:00+00:00",
        "after_captured_at_utc": "2026-10-01T00:01:01+00:00"}
    artifacts["wrapper_receipt"] = save(root / "wrapper.json", wrapper)
    packet = {"schema_version": 1, "evidence_kind": "SYNTHETIC_V4_FIXTURE", "artifacts": artifacts,
        "score_export_receipts": score_receipts, "score_csvs": score_csvs, "model_score_coverage": coverage, "runs": runs}
    packet_path = root / "packet.json"; save(packet_path, packet)
    return packet_path, packet


class V4EvidenceFixtures(unittest.TestCase):
    def test_default_is_contract_only(self):
        with patch.object(v, "hash_file", side_effect=AssertionError("must not read evidence")), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(v.main([]), 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result["status"], "prepared_only")
        self.assertIsNone(result["point_net_excess_return"])

    def test_original_float_gain_boundary_keeps_legal_fixed4_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, data = packet_fixture(Path(tmp))
            inspector = v._Inspector(path, "3" * 64, fixture=True)
            frozen = json.loads(Path(data["artifacts"]["freeze"]["path"]).read_text())
            manifest = json.loads(Path(data["artifacts"]["input_manifest"]["path"]).read_text())
            frozen["selection"]["alternatives"]["fixed_1"]["mean_ic"] = .051
            frozen["selection"]["alternatives"]["fixed_4"]["mean_ic"] = .05
            body = {"data": frozen["dataset_fingerprint"], "config": frozen["config"],
                    "selection": frozen["selection"], "hashes": frozen["checkpoint_hashes"]}
            frozen["freeze_id"] = sha(json.dumps(body, sort_keys=True).encode())[:20]
            manifest["freeze_id"] = frozen["freeze_id"]
            manifest["model_versions"] = {m: frozen["freeze_id"] + ":" + m for m in v.MODELS}
            self.assertLess(.051 - .05, .001)
            inspector.check_freeze(frozen, manifest)
            # Decimal(.051)-Decimal(.05) is exactly .001; that old v1 branch
            # rejected the legitimate frozen trainer's fixed4 selection.
            bad = deepcopy(frozen); bad["selection"]["selected_method"] = "fixed_1"
            bad["selection"]["selected_label"] = 1
            body["selection"] = bad["selection"]; bad["freeze_id"] = sha(json.dumps(body, sort_keys=True).encode())[:20]
            wrong_manifest = deepcopy(manifest); wrong_manifest["freeze_id"] = bad["freeze_id"]
            with self.assertRaisesRegex(v.EvidenceRejected, "selected identity"):
                inspector.check_freeze(bad, wrong_manifest)

    def test_numeric_IC_and_threshold_malformed_values_rejected(self):
        for value in (True, False, "0.05", None, float("nan"), float("inf"), -float("inf"), 10 ** 10000):
            with self.assertRaises(v.EvidenceRejected): v.finite_ic(value)
        with tempfile.TemporaryDirectory() as tmp:
            path, data = packet_fixture(Path(tmp)); inspector = v._Inspector(path, "3" * 64, fixture=True)
            frozen = json.loads(Path(data["artifacts"]["freeze"]["path"]).read_text())
            manifest = json.loads(Path(data["artifacts"]["input_manifest"]["path"]).read_text())
            for threshold in (True, False, "0.001", None, float("inf"), -.001, 0):
                bad = deepcopy(frozen); bad["selection"]["minimum_gain"] = threshold
                body = {"data": bad["dataset_fingerprint"], "config": bad["config"],
                        "selection": bad["selection"], "hashes": bad["checkpoint_hashes"]}
                bad["freeze_id"] = sha(json.dumps(body, sort_keys=True).encode())[:20]
                bad_manifest = deepcopy(manifest); bad_manifest["freeze_id"] = bad["freeze_id"]
                with self.assertRaises(v.EvidenceRejected): inspector.check_freeze(bad, bad_manifest)

    def test_missing_packet_is_pending_no_receipt_or_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); output = root / "missing" / "v4_actual_evidence_receipt_v1.json"
            with contextlib.redirect_stdout(io.StringIO()):
                status = v.main(["inspect", "--packet", str(root / "no_packet.json"),
                    "--approved-wrapper-source-sha256", "3" * 64, "--output", str(output)])
            self.assertEqual(status, 2); self.assertFalse(output.parent.exists())

    def test_full_fixture_never_admitted_as_actual_or_legacy_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = packet_fixture(Path(tmp))
            result = v._Inspector(path, "3" * 64, fixture=True).run()
            self.assertFalse(result["research_evidence"]); self.assertEqual(result["actual_run_count"], 0)
            self.assertEqual(result["fixture_run_count"], 36); self.assertIsNone(result["point_net_excess_return"])
            self.assertFalse(result["all_PLAN_acceptance_complete"])
            with self.assertRaises(v.EvidenceRejected): v.inspect_packet(path, "3" * 64)
            with self.assertRaises(v.EvidenceRejected): v.write_new_receipt(Path(tmp) / "manifest.json", result)

    def test_missing_wrapper_is_pending_not_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, data = packet_fixture(Path(tmp))
            Path(data["artifacts"]["wrapper_receipt"]["path"]).unlink()
            with self.assertRaises(v.EvidencePending): v._Inspector(path, "3" * 64, fixture=True).run()
            self.assertFalse((Path(tmp) / "manifest.json").exists())

    def test_hash_changed_artifact_rejected_and_original_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, data = packet_fixture(Path(tmp))
            target = Path(data["runs"][0]["replay"]["path"])
            original = target.read_bytes(); bad = original + b" \n"; target.write_bytes(bad)
            with self.assertRaises(v.EvidenceRejected): v._Inspector(path, "3" * 64, fixture=True).run()
            self.assertEqual(target.read_bytes(), bad)

    def test_duplicate_run_and_truncated_stdout_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, data = packet_fixture(Path(tmp)); data["runs"][-1] = deepcopy(data["runs"][0]); save(path, data)
            with self.assertRaises(v.EvidenceRejected): v._Inspector(path, "3" * 64, fixture=True).run()
        with tempfile.TemporaryDirectory() as tmp:
            path, data = packet_fixture(Path(tmp)); log = Path(data["artifacts"]["stdout"]["path"])
            log.write_bytes(log.read_bytes().rsplit(b"END ", 1)[0])
            data["artifacts"]["stdout"] = reference(log)
            w = Path(data["artifacts"]["wrapper_receipt"]["path"]); wrapper = json.loads(w.read_text())
            wrapper["stdout_sha256"] = reference(log)["sha256"]; data["artifacts"]["wrapper_receipt"] = save(w, wrapper); save(path, data)
            with self.assertRaisesRegex(v.EvidenceRejected, "BEGIN/END"): v._Inspector(path, "3" * 64, fixture=True).run()

    def test_actual_outer_map_change_cannot_use_summary_true_as_substitute(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, data = packet_fixture(Path(tmp)); w = Path(data["artifacts"]["wrapper_receipt"]["path"])
            wrapper = json.loads(w.read_text()); wrapper["after_hashes"] = dict(wrapper["after_hashes"])
            wrapper["after_hashes"][next(iter(wrapper["after_hashes"]))] = "f" * 64
            data["artifacts"]["wrapper_receipt"] = save(w, wrapper); save(path, data)
            with self.assertRaisesRegex(v.EvidenceRejected, "full source maps"): v._Inspector(path, "3" * 64, fixture=True).run()

    def test_malformed_json_and_point_promotion_rejected(self):
        with self.assertRaises(v.EvidenceRejected): v.strict_json(b'{"a":1,"a":2}')
        with self.assertRaises(v.EvidenceRejected): v.strict_json(b'{"a":NaN}')
        for key in ("point_net_excess_return", "point_net_return"):
            with self.assertRaises(v.EvidenceRejected): v.false_claims({key: "0"})
        with self.assertRaises(v.EvidenceRejected): v.false_claims({"economic_target_accepted": True})

    def test_short_prefix_and_cash_reconciliation_rejected(self):
        days = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"]
        replay = journal(days); replay["completed_marks"].pop()
        with self.assertRaisesRegex(v.EvidenceRejected, "short-prefix"): v.check_journal(replay, days, days + ["2025-01-08"])
        replay = journal(days); replay["completed_marks"][1]["cash"] = "999999"
        with self.assertRaisesRegex(v.EvidenceRejected, "cash mismatch"): v.check_journal(replay, days, days + ["2025-01-08"])

    def test_anchor_cdr_unknown_cannot_be_dropped_or_get_bounds(self):
        days = ["2025-01-02", "2025-01-03", "2025-12-31", "2026-09-23"]
        replay = journal(days)
        replay["outcomes"] = [{"date": "2025-01-03", "symbol": "sh689009", "order_id": "fixture_no_fill", "filled": 0, "risk_after": None}]
        sm = summary(replay, "tax_upper_reserve", days)
        exposure = {"date": "2025-01-03", "symbol": "sh689009", "qualification": "UNKNOWN_FIXTURE"}
        sm["instrument_qualification_exposures"] = [exposure]
        for name, (a, z) in v.WINDOWS.items():
            w = sm["windows"][name]
            w["instrument_qualification_exposures"] = [exposure] if a < exposure["date"] <= z else []
            w["prior_instrument_qualification_exposures_affect_anchor"] = [exposure] if exposure["date"] <= a else []
            w["instrument_tax_fee_scope_complete"] = False; w["full_mark_execution_and_instrument_scope_complete"] = False
            w["economic_comparison_eligible"] = False; w["same_path_net_tax_bounds"] = None
        cov = {"continuous_account_trading_score_coverage_by_window": {k: True for k in v.WINDOWS}}
        result = v.check_summary(replay, sm, "tax_upper_reserve", days, {d: Decimal(100) for d in days}, cov)
        self.assertEqual(result["calendar_2026_through_sep23"]["qualification_status"], "pending_qualification")
        self.assertIn("prior_instrument_qualification_exposures_affect_anchor", result["calendar_2026_through_sep23"]["unknown_exposure_fields"])
        bad = deepcopy(sm); bad["windows"]["calendar_2026_through_sep23"]["prior_instrument_qualification_exposures_affect_anchor"] = []
        with self.assertRaises(v.EvidenceRejected): v.check_summary(replay, bad, "tax_upper_reserve", days, {d: Decimal(100) for d in days}, cov)
        bad = deepcopy(sm); bad["windows"]["whole_observed"]["same_path_net_tax_bounds"] = summary(journal(days), "tax_upper_reserve", days)["windows"]["whole_observed"]["same_path_net_tax_bounds"]
        with self.assertRaises(v.EvidenceRejected): v.check_summary(replay, bad, "tax_upper_reserve", days, {d: Decimal(100) for d in days}, cov)

    def test_same_day_sale_and_failed_fill_mutation_rejected(self):
        days = ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07"]
        replay = journal(days); events = []
        def add(kind, d, payload):
            body = {"kind": kind, "day": d, "version": "INITIAL", "payload": payload,
                    "previous_hash": events[-1]["hash"] if events else "GENESIS"}
            body["hash"] = sha(json.dumps(body, sort_keys=True).encode()); events.append(body)
        add("INITIALIZE", None, {"capital": "1000000"})
        for d in days:
            add("SESSION_OPEN", d, {})
            if d == days[1]:
                for ident, side in (("OPEN:buy", "BUY"), ("OPEN:same_day_sell", "SELL")):
                    add("FILL", d, {"id": ident, "symbol": "sh600004", "side": side, "quantity": 100,
                        "price": "10", "fee": "0", "next_session": days[2], "day": d})
            add("SESSION_CLOSE", d, {})
        replay["account_event_ledger"] = events; replay["account_event_ledger_hash"] = events[-1]["hash"]
        with self.assertRaisesRegex(v.EvidenceRejected, "T\\+1/unlisted"):
            v.check_journal(replay, days, days + ["2025-01-08"])
        replay = journal(days)
        replay["outcomes"] = [{"order_id": "unfilled", "filled": 0, "requested": 100, "expired": 100,
                               "date": days[1], "symbol": "sh600004", "side": "BUY", "price": None, "fees": None}]
        result = v.check_journal(replay, days, days + ["2025-01-08"])
        self.assertTrue(result["failed_fills_preserve_state"])
        replay["outcomes"][0]["price"] = "10"
        with self.assertRaisesRegex(v.EvidenceRejected, "failed fill"):
            v.check_journal(replay, days, days + ["2025-01-08"])


if __name__ == "__main__":
    unittest.main()
