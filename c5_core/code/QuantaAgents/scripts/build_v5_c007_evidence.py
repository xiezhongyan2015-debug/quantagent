"""Import exposed archived c007 proxy accounts into V5; no market reads/replay.

Only creates a fresh output folder. Policy is written before importing accounts;
because prior c007 results are already exposed this is retrospective diagnostics,
not retrospective preregistration or an independent financial validation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
from statistics import mean, stdev
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quanta_agents.meta_v5.analytics import digest
from quanta_agents.meta_v5.registry import Catalog, file_hash, prepare_catalog, write_catalog

ARCHIVE = ROOT / "output/reviews/c007_cross_year_20260908/stock_run_02"
COMPILED = ROOT / "experiment_traces/v4s1/f1/control/arms/fixed/batches/frozen/candidates/c007/workbench/compiled.json"
DEFAULT_OUTPUT = ROOT / "output/reviews/meta_v5_c007_20260908"
UNITS = ["sh600004", "sh600006", "sh600017", "sh600022", "sh600026", "sh600037", "sh600039", "sh600053",
         "sh600056", "sh600058", "sh600059", "sh600060", "sh600062", "sh600064", "sh600073", "sh600079"]
YEARS = list(range(2020, 2026))
INITIAL = 1_000_000.0
POLICY = {"version": "v5_stability_policy_v1", "expected_years": YEARS,
    "annualization": 252, "risk_free_rate": .02, "min_sessions_per_year": 200,
    "min_paired_cell_fraction": 1., "min_unit_coverage_fraction": 1.,
    "min_positive_excess_year_fraction": 2 / 3, "min_positive_excess_unit_fraction": 2 / 3,
    "max_worst_year_excess_loss": .15, "max_drawdown": .35,
    "max_positive_pnl_concentration": .5, "max_stale_fraction": 0.,
    "require_known_fees": True, "require_exposure": True, "require_execution_certified": False}


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        f.write("\n")


def need(value, message):
    if not value:
        raise ValueError(message)


def close(left, right, message, tolerance=1e-10):
    need(math.isclose(left, right, rel_tol=tolerance, abs_tol=tolerance), message)


def read_account(unit, arm, manifest):
    path = ARCHIVE / f"{unit}_{arm}_10bp.json.gz"
    sha = file_hash(path)
    need(manifest["outputs"].get(path.name) == sha, "saved account differs from completed manifest: " + path.name)
    with gzip.open(path, "rt", encoding="utf-8") as f:
        account = json.load(f)
    rows, diagnostics = account["daily"], account["diagnostics"]
    need(diagnostics["version"] == "c007-cross-year-proxy-v1.2", "unexpected accounting version")
    need(diagnostics["initial_cash"] == INITIAL and not diagnostics["execution_valid"], "unexpected account basis")
    need(diagnostics["accounting_mode"] == "adjusted_price_units_approximation", "unexpected accounting mode")
    need(diagnostics["no_terminal_liquidation"] is True, "terminal treatment changed")
    calendar = [r["date"] for r in rows]
    need(calendar == sorted(set(calendar)) and len(calendar) == 1455 and
         calendar[0] == "2020-01-02" and calendar[-1] == "2025-12-31", "archive calendar changed")
    need(sorted({int(d[:4]) for d in calendar}) == YEARS, "expected year missing")
    previous = INITIAL
    for r in rows:
        need(all(type(r[k]) in (int, float) and math.isfinite(r[k]) for k in
                 ("nav", "cash", "equity", "return", "pnl", "fees", "exposure")), "unknown archive accounting row")
        need(r["nav"] > 0 and r["cash"] >= 0 and r["fees"] >= 0, "invalid cash account")
        close(r["cash"] + r["equity"], r["nav"], "cash-equity identity")
        close(r["nav"] / previous - 1, r["return"], "daily return cannot omit initial loss or include external flow")
        close(r["nav"] - previous, r["pnl"], "daily PnL identity", tolerance=1e-8)
        close(r["equity"] / r["nav"], r["exposure"], "full-NAV exposure identity")
        previous = r["nav"]
    close(sum(r["fees"] for r in rows), sum(t["fees"]["total"] for t in account["trades"]), "trade/daily fees mismatch", 1e-8)
    return path, sha, account


def imported_series(account):
    rows = account["daily"]
    return {"nav": [r["nav"] for r in rows], "fees": [r["fees"] for r in rows],
            "exposure": [r["exposure"] for r in rows], "stale": [r["held_stale_count"] for r in rows]}


def independent_metrics(rows, initial):
    returns = [r["return"] for r in rows]
    sd = stdev(returns)
    peak, dd = initial, 0.
    for r in rows:
        peak = max(peak, r["nav"])
        dd = max(dd, 1 - r["nav"] / peak)
    return {"return": rows[-1]["nav"] / initial - 1,
            "sharpe": (mean(returns) - 1.02 ** (1 / 252) + 1) / sd * math.sqrt(252) if sd > 0 else None,
            "max_drawdown": dd, "fees": sum(r["fees"] for r in rows),
            "average_exposure": mean(r["exposure"] for r in rows)}


def verify_profile(profile, accounts):
    checks = 0
    for full in profile["full_period"]:
        unit = full["unit_id"]
        for side, arm in (("candidate", "c007"), ("benchmark", "continuous_target")):
            account = accounts[(unit, arm)]
            previous = INITIAL
            for year in YEARS:
                rows = [r for r in account["daily"] if int(r["date"][:4]) == year]
                cell = next(c for c in profile["cells"] if c["unit_id"] == unit and c["year"] == year)
                expected = independent_metrics(rows, previous)
                for metric, value in expected.items():
                    actual = cell[side + "_metrics"][metric]
                    need(actual is not None, "unexpected unknown complete archived metric")
                    close(actual, value, "independent annual metric: " + metric); checks += 1
                previous = rows[-1]["nav"]
            expected = independent_metrics(account["daily"], INITIAL)
            for metric, value in expected.items():
                close(full[side + "_metrics"][metric], value, "independent full-period metric: " + metric); checks += 1
    need(profile["formal_target_success"] is False, "development report cannot imply formal acceptance")
    return checks


def build(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_new(output / "policy_frozen_before_import.json", {"policy": POLICY, "policy_hash": digest(POLICY),
        "created_utc": datetime.now(timezone.utc).isoformat(), "prior_c007_results_exposed": True,
        "interpretation": "Retrospective diagnostic policy; normative proposal for future preregistration, not a claim this preceded c007 results.",
        "parameters_scanned": 0, "capital_scanned": False, "result_targeted_policy_search": False})
    manifest_path = ARCHIVE / "manifest_completed.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    compiled = json.loads(COMPILED.read_text(encoding="utf-8"))
    need(compiled["program_hash"] == digest(compiled["spec"]), "compiled strategy identity mismatch")
    need(compiled["spec"]["factors"][0]["expression"] ==
         "(close > lag(close, 1)) & (volume < lag(rolling_mean(volume, 20), 1) * (1))", "frozen c007 event changed")
    need(compiled["spec"]["target_weight_expression"] == "where(rolling_max(contracting_rise, (10)) > 0, 1, 0)",
         "frozen c007 10-session rule changed")
    program_hash = compiled["program_hash"]
    archived_common = {}
    for name in ("proxy_engine.py", "run_cross_year.py", "load_panel.py", "PROTOCOL.md", "PRE_RESULT_RULE_CORRECTION.md", "FEE_REPAIR_002.md"):
        path = ARCHIVE / "source_snapshot" / name
        if not path.is_file():
            path = ARCHIVE.parent / name
        need(file_hash(path) == manifest["files"][name]["sha256"], "saved source archive changed: " + name)
        archived_common[str(path)] = file_hash(path)
    cost_policy = {"version": "archived_proxy_cost_10bp_v1", "adverse_slippage": .001,
        "raw_tick": .01, "tick_rounding": "buy_ceil_sell_floor",
        "commission_rate": .0003, "minimum_commission_CNY": 5.,
        "transfer_rate_before_2022_04_29": .00002, "transfer_rate_since_2022_04_29": .00001,
        "sell_stamp_before_2023_08_28": .001, "sell_stamp_since_2023_08_28": .0005,
        "component_money_rounding": "Decimal_all_products_half_up_to_CNY_cent",
        "slippage_embedded_in_fill_NAV_not_added_again": True,
        "archived_engine_sha256": manifest["files"]["proxy_engine.py"]["sha256"]}
    scopes = [("fixed16_singletons", UNITS, "stock"), ("fixed16_equal", ["fixed16_equal"], "portfolio"),
              ("csi500_historical_equal", ["csi500_historical_equal"], "portfolio")]
    bundles, accounts = [], {}
    common_calendar = None
    for scope, units, unit_kind in scopes:
        sources = {str(manifest_path): file_hash(manifest_path), str(COMPILED): file_hash(COMPILED), **archived_common}
        pairs = []
        for unit in units:
            sides = {}
            for arm in ("c007", "continuous_target"):
                path, pin, account = read_account(unit, arm, manifest)
                accounts[(unit, arm)] = account
                sources[str(path)] = pin; sides[arm] = account
                calendar = [r["date"] for r in account["daily"]]
                if common_calendar is None: common_calendar = calendar
                need(common_calendar == calendar, "candidate/benchmark/unit calendars differ")
            pairs.append({"unit_id": unit, "unit_kind": unit_kind, "calendar": common_calendar,
                "initial_nav": INITIAL, "candidate": imported_series(sides["c007"]),
                "benchmark": imported_series(sides["continuous_target"]),
                "benchmark_id": unit + "_continuous_target_10bp",
                "source_quality": "Archived adjusted-unit proxy; stale held marks retained. Not a corporate-action cash/tax ledger."})
        source_scope_identity = {"version": "c007_common_research_scope_v1", "scope": scope,
            "market_input_sha256": {name: manifest["files"][name]["sha256"] for name in
                ("panel.npz", "metadata.json", "panel_inventory.json", "load_panel.py")},
            "market_hash_validation": "Bindings from frozen completed manifest; importer does not reread underlying market arrays.",
            "calendar_hash": digest(common_calendar), "expected_units": units,
            "execution_engine_sha256": manifest["files"]["proxy_engine.py"]["sha256"],
            "cost_policy_hash": digest(cost_policy), "benchmark_definition": "daily_continuous_target_same_unit_scope",
            "terminal_policy": "no_forced_liquidation", "initial_nav": INITIAL}
        bundles.append({"version": "v5_stability_bundle_v1", "candidate_id": "c007_" + scope,
            "program_hash": program_hash, "scope_id": "c007_2020_2025_10bp_" + scope,
            "split": "development", "expected_units": units, "pairs": pairs,
            "provenance": {"source_class": "saved_development", "accounting_mode": "adjusted_price_units_approximation",
                "account_currency": "CNY", "fee_unit": "CNY", "external_cash_flows": "none_after_initial",
                "execution_certified": False, "exposed": True, "source_hashes": sources,
                "source_scope_identity": source_scope_identity, "cost_policy": cost_policy,
                "cost_policy_hash": digest(cost_policy), "program_identity_basis": "original_compiled_spec_canonical_sha256",
                "archived_compiled_sha256": file_hash(COMPILED), "program_scope_limit": "Signal identity preserved; execution uses separately identified proxy, not the original raw-share cash/tax account.",
                "cash_path_limit": "Saved proxy cash/equity/PnL rows reconciled; no external-flow mode supported. Not independently certified statutory accounting."}})
    prepared = prepare_catalog(bundles, POLICY)
    checks = sum(verify_profile(e["profile"], accounts) for e in prepared["entries"])
    identity = write_catalog(output, prepared)
    need(Catalog(output, identity).verify(), "catalog immutable verification failed")
    summary = {"version": "v5_c007_reassessment_v1", "program_hash": program_hash,
        "original_compiled_sha256": file_hash(COMPILED), "policy_hash": digest(POLICY),
        "archive_manifest_sha256": file_hash(manifest_path), "accounts_read": len(accounts),
        "independent_metric_checks": checks, "catalog_verified": True, "formal_target_success": False,
        "independent_market_samples_claimed": False, "strategy_or_market_replay_performed": False,
        "scope_summaries": {e["candidate_id"]: {"scope_id": e["bundle"]["scope_id"],
            "profile_hash": e["profile"]["profile_hash"], "summary": e["profile"]["summary"],
            "full_period": e["profile"]["full_period"], "development_eligible": e["profile"]["development_eligible"]}
            for e in prepared["entries"]}}
    write_new(output / "reassessment_summary.json", summary)
    write_new(output / "catalog_request.json", {"bundles": bundles, "policy": POLICY})
    write_new(output / "build_receipt.json", {"builder_sha256": file_hash(Path(__file__)),
        "analytics_sha256": file_hash(ROOT / "src/quanta_agents/meta_v5/analytics.py"),
        "registry_sha256": file_hash(ROOT / "src/quanta_agents/meta_v5/registry.py"),
        "output_sha256": {str(p.relative_to(output)): file_hash(p) for p in output.rglob("*.json")},
        "verified": True, "independent_financial_validation": False})
    return {"output": str(output), "accounts_read": len(accounts), "independent_metric_checks": checks,
        "policy_hash": digest(POLICY), "scopes": {k: {"development_eligible": v["development_eligible"],
        "statuses": v["summary"]["statuses"]} for k, v in summary["scope_summaries"].items()}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Fresh output folder; existing folders are never overwritten")
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False, indent=2))
