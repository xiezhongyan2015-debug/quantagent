"""Portable exact factor definitions with separate implementation/evidence/use states."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from quanta_agents.meta_v6.factors import FactorEngine, LANGUAGE_VERSION
from quanta_agents.research_kernel.store import digest
from .study import factor_spec, once, read, sha


def value_hash(frame):
    result = hashlib.sha256()
    result.update(str(list(frame.columns)).encode("utf-8"))
    result.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return result.hexdigest()


def load_bundle(path):
    bundle = read(path)
    body = {k: v for k, v in bundle.items() if k != "bundle_sha256"}
    if bundle.get("bundle_sha256") != digest(body):
        raise ValueError("factor bundle content integrity failure")
    spec = factor_spec(bundle)
    if spec.factor_id != bundle["factor_id"] or bundle["calculator_language"] != LANGUAGE_VERSION:
        raise ValueError("factor formula/calculator identity mismatch")
    relevant = ("meta_v6/factors.py", "meta/factor_algebra.py", "factor_research/adapters.py", "research_kernel/universe.py")
    for source, expected in bundle["calculator_source_hashes"].items():
        if any(Path(source).as_posix().endswith(suffix) for suffix in relevant) and sha(source) != expected:
            raise ValueError("installed factor calculator differs from bundle source: " + source)
    return spec, bundle


def export_bundles(study, evaluator, all_rows, selected):
    destination = study.root / "bundles"
    destination.mkdir(parents=True, exist_ok=True)
    reimport_engine = FactorEngine(evaluator.panel, max_cache_bytes=128 * 1024**2)
    exported, rejected = [], []
    for selection in selected:
        identity = selection["factor_id"]
        row = all_rows[identity]
        spec = factor_spec(row)
        development = read(study.root / "development" / (identity + ".json"))
        confirmation = read(study.root / "confirmation" / (identity + ".json"))
        if confirmation.get("status") == "implementation_failed":
            path = destination / (identity + "_rejected.json")
            once(path, {"factor_id": identity, "spec": spec.to_dict(), "status": "confirmation_implementation_failed",
                        "failure": confirmation, "historical_target_met": False,
                        "independent_stability_proven": False, "profitability_proven": False})
            rejected.append({"factor_id": identity, "path": str(path), "sha256": sha(path)})
            continue
        scores = evaluator.compute(spec)
        bundle = {
            "version": "v9a_factor_bundle_1", "factor_id": identity, "spec": spec.to_dict(),
            "family": {k: row[k] for k in ("family_id", "arm", "parameters", "control_ids", "main_effect_ids", "proposal_id", "trial_id")},
            "calculator_language": LANGUAGE_VERSION, "calculator_source_hashes": read(study.root / "source_pins.json"),
            "data": {"manifest": str(study.root / "confirmation_data_manifest.json"),
                     "manifest_sha256": sha(study.root / "confirmation_data_manifest.json"),
                     "panel_fingerprint": evaluator.engine.data_fingerprint,
                     "fields": evaluator.panel.provenance["factor_research"]["allowed_signal_fields"],
                     "decision_time": "after completed daily bar", "historical_available_at_verified": False,
                     "universe_id": evaluator.universe_id, "no_2025_values": True},
            "direction": selection["direction"], "direction_fit": development["train"]["direction_fit"],
            "snapshot_score_direction_applied": False,
            "preprocessing": "none_for_single_factor; fixed training direction multiplies raw formula",
            "fit_transform_rule": "unsupervised causal DSL; direction fit once 2016-2018; no annual refit",
            "label": study.config["label"], "baseline": study.config["baseline"],
            "implementation_status": "executable",
            "research_evidence_status": "exposed_history_target_met" if confirmation["historical_target_met"] else "rejected_annual_0.10_target",
            "trading_use_status": "gross_group_diagnostics_only_no_account_validation",
            "historical_target_met": confirmation["historical_target_met"],
            "independent_stability_proven": False, "profitability_proven": False,
            "evidence": {"train_annual": development["train"]["annual"], "development_annual": development["development"]["annual"],
                         "confirmation_annual": confirmation["report"]["annual"],
                         "all_attempts": str(study.expansion_path), "all_attempts_sha256": sha(study.expansion_path),
                         "development_incremental": str(study.root / "development_incremental" / (identity + ".json")),
                         "confirmation_incremental": str(study.root / "confirmation_incremental" / (identity + ".json"))},
            "scores_hash": value_hash(scores), "limitations": study.config["limitations"],
        }
        bundle["bundle_sha256"] = digest(bundle)
        path = destination / (identity + ".json")
        once(path, bundle)
        imported, _ = load_bundle(path)
        reproduced = reimport_engine.compute(imported)
        same = (scores.index.equals(reproduced.index) and scores.columns.equals(reproduced.columns)
                and np.allclose(scores.to_numpy(), reproduced.to_numpy(), rtol=1e-12, atol=1e-12, equal_nan=True))
        if not same:
            raise ValueError("factor bundle reimport does not reproduce frozen values")
        snapshot = destination / (identity + "_audit.npz")
        arrays = {"dates": scores.index.to_numpy(dtype="datetime64[ns]"), "symbols": np.asarray(scores.columns, dtype=str),
                  "open": evaluator.panel.fields["open"].to_numpy(), "scores": scores.to_numpy(),
                  "pool": evaluator.pool.to_numpy(dtype=bool)}
        if "open_observed" in evaluator.panel.fields:
            arrays["open_observed"] = evaluator.panel.fields["open_observed"].to_numpy()
        if not snapshot.exists():
            np.savez_compressed(snapshot, **arrays)
        with np.load(snapshot, allow_pickle=False) as saved:
            if set(saved.files) != set(arrays):
                raise ValueError("existing audit snapshot schema changed")
            for key, expected in arrays.items():
                actual = saved[key]
                same_values = np.array_equal(actual, expected, equal_nan=True) if expected.dtype.kind in "fc" else np.array_equal(actual, expected)
                if actual.dtype != expected.dtype or not same_values:
                    raise ValueError("existing audit snapshot does not reproduce frozen " + key)
        exported.append({"factor_id": identity, "bundle": str(path), "bundle_sha256": sha(path),
                         "audit_snapshot": str(snapshot), "audit_sha256": sha(snapshot),
                         "reimport_equal": same, "scores_hash": value_hash(reproduced),
                         "historical_target_met": confirmation["historical_target_met"]})
    once(destination / "manifest.json", {"bundles": exported, "rejected": rejected,
                                        "all_reimport_equal": all(r["reimport_equal"] for r in exported),
                                        "independent_stability_proven": False, "profitability_proven": False})
    return exported
