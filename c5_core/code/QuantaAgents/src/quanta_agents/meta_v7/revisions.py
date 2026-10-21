"""Read-only derivation of review-bound strategy revisions.

The controller owns review persistence and submission/idempotency. These helpers
never execute accounts, mutate parents, or certify the model's economic claims.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math

from quanta_agents.research_kernel.compiler import ALLOCATION_DEFAULTS, validate_strategy
from quanta_agents.research_kernel.store import digest, serial


VERSION = "v8_bound_revision_v1"
ALLOWED_PATHS = frozenset({"/score", "/gate", "/risk_score"} |
                          {"/allocation/" + key for key in ALLOCATION_DEFAULTS})
MAX_CHANGES = len(ALLOWED_PATHS)
MAX_PATCH_BYTES = 16000
PLAN_FIELDS = {"parent_run_id", "allowed_paths", "hypothesis", "falsifier"}


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value, field, maximum=2000):
    _need(type(value) is str and 0 < len(value.strip()) <= maximum,
          field + " must be bounded nonempty text")
    return value


def _bounded_json(value, maximum, field):
    try:
        size = len(serial(value).encode("utf-8"))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError(field + " must be finite bounded JSON") from exc
    _need(size <= maximum, field + " byte limit exceeded")


def _parent(kernel, batch_id, run_id):
    _text(batch_id, "batch_id", 200)
    _text(run_id, "parent_run_id", 200)
    rows = kernel.store.rows(
        "SELECT r.spec,r.status,r.evidence_id FROM runs r WHERE r.id=? AND EXISTS "
        "(SELECT 1 FROM attempts a WHERE a.batch_id=? AND a.run_id=r.id AND a.status='completed')",
        (run_id, batch_id))
    _need(len(rows) == 1 and rows[0]["status"] == "completed",
          "Revision parent must be a completed run in the reviewed batch")
    frozen = json.loads(rows[0]["spec"])
    _need(type(frozen) is dict and "strategy" in frozen, "Parent frozen strategy is missing")
    strategy = validate_strategy(frozen["strategy"])
    return frozen, strategy, rows[0]["evidence_id"]


def freeze_revision_plan(kernel, batch_id, plan):
    """Normalize a model plan and bind it to one completed proposal or control.

    Return data for the controller's immutable review record; no writes occur.
    Allowed expression roots authorize their whole trees, not a guessed intent.
    """
    _need(type(plan) is dict and set(plan) == PLAN_FIELDS, "Revision plan requires exact fields")
    _bounded_json(plan, MAX_PATCH_BYTES, "Revision plan")
    paths = plan["allowed_paths"]
    _need(type(paths) is list and 1 <= len(paths) <= MAX_CHANGES and
          all(type(path) is str and path in ALLOWED_PATHS for path in paths),
          "Revision allowed_paths must be explicit supported JSON pointers")
    _need(len(set(paths)) == len(paths), "Duplicate revision allowed path")
    frozen, strategy, _ = _parent(kernel, batch_id, plan["parent_run_id"])
    return {"parent_run_id": plan["parent_run_id"], "allowed_paths": sorted(paths),
            "hypothesis": _text(plan["hypothesis"], "hypothesis"),
            "falsifier": _text(plan["falsifier"], "falsifier"),
            "parent_strategy_sha256": digest(strategy), "parent_frozen_sha256": digest(frozen)}


def _differences(before, after, path=""):
    if type(before) is dict and type(after) is dict:
        rows = []
        for key in sorted(set(before) | set(after)):
            child = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in before or key not in after:
                rows.append({"path": child, "before": deepcopy(before.get(key)),
                             "after": deepcopy(after.get(key)),
                             "before_present": key in before, "after_present": key in after})
            else:
                rows.extend(_differences(before[key], after[key], child))
        return rows
    if type(before) is list and type(after) is list and len(before) == len(after):
        return [row for index, (left, right) in enumerate(zip(before, after))
                for row in _differences(left, right, path + "/" + str(index))]
    if before == after:
        return []
    return [{"path": path, "before": deepcopy(before), "after": deepcopy(after)}]


def _domains(paths):
    domains = set()
    for path in paths:
        if path == "/score" or path.startswith("/score/"):
            domains.add("prediction")
        elif path == "/risk_score" or path.startswith("/risk_score/"):
            domains.add("risk")
        elif path == "/gate" or path.startswith("/gate/"):
            domains.update(("selection_scope", "exposure"))
        elif path in {"/allocation/top_n", "/allocation/membership_buffer"}:
            domains.add("selection_scope")
        elif path in {"/allocation/gross_exposure", "/allocation/max_stock_weight"}:
            domains.add("exposure")
        elif path == "/allocation/weighting":
            domains.update(("risk", "exposure"))
        elif path in {"/allocation/rebalance_sessions", "/allocation/rebalance_schedule"}:
            domains.add("rebalance")
    return sorted(domains)


def derive_revision(kernel, parent_run_id, review_batch_id, changes, name=None):
    """Apply only declared paths to a saved parent and return an auditable diff."""
    _need(not kernel.store.meta("stopped", False), "Closed research cannot derive a new revision")
    _need(not kernel.store.rows("SELECT id FROM validation_jobs"),
          "Candidate is frozen for validation; no further revision")
    latest = kernel.store.rows("SELECT batch_id,review FROM batch_reviews ORDER BY rowid DESC LIMIT 1")
    _need(len(latest) == 1 and latest[0]["batch_id"] == review_batch_id,
          "Revision must bind the latest batch review")
    review = json.loads(latest[0]["review"])
    _need(review.get("verdict") == "revise", "Latest review must explicitly request revise")
    plan = review.get("revision_plan")
    _need(type(plan) is dict and plan.get("parent_run_id") == parent_run_id,
          "Revision parent must match the frozen review plan")
    # Revalidate rather than trusting stored declarations or silently upgrading
    # legacy reviews that did not contain a bound plan.
    expected = freeze_revision_plan(kernel, review_batch_id, {key: plan.get(key) for key in PLAN_FIELDS})
    _need(plan == expected, "Parent strategy or frozen identity differs from the review plan")
    frozen, before, parent_evidence = _parent(kernel, review_batch_id, parent_run_id)
    _need(type(changes) is list and 1 <= len(changes) <= MAX_CHANGES,
          "Revision changes must be a nonempty bounded list")
    _bounded_json(changes, MAX_PATCH_BYTES, "Revision changes")
    declared = []
    after = deepcopy(before)
    for change in changes:
        _need(type(change) is dict and set(change) == {"path", "value"},
              "Each revision change requires exact path/value fields")
        path = change["path"]
        _need(type(path) is str and path in plan["allowed_paths"],
              "Revision change is outside the frozen allowed paths")
        _need(path not in declared, "Duplicate revision change path")
        declared.append(path)
        parts = path[1:].split("/")
        if len(parts) == 1:
            after[parts[0]] = deepcopy(change["value"])
        else:
            after[parts[0]][parts[1]] = deepcopy(change["value"])
    if name is not None:
        after["name"] = _text(name, "name", 128)
    after = validate_strategy(after)
    economic = _differences({key: value for key, value in before.items() if key not in {"name", "metadata"}},
                            {key: value for key, value in after.items() if key not in {"name", "metadata"}})
    _need(economic, "Revision has no effective economic change")
    effective = [path for path in declared if any(row["path"] == path or row["path"].startswith(path + "/")
                                                 for row in economic)]
    _need(len(effective) == len(declared), "Revision contains an ineffective declared change")
    report = {"version": VERSION, "parent_run_id": parent_run_id, "review_batch_id": review_batch_id,
              "parent_evidence_id": parent_evidence, "review_evidence_id": review.get("evidence_id"),
              "review_sha256": digest(review), "parent_strategy_sha256": digest(before),
              "parent_frozen_sha256": digest(frozen), "derived_strategy_sha256": digest(after),
              "allowed_paths": list(plan["allowed_paths"]), "declared_paths": sorted(declared),
              "actual_changes": economic, "domains": _domains(row["path"] for row in economic),
              "name_change": None if before["name"] == after["name"] else
                             {"before": before["name"], "after": after["name"]},
              "hypothesis": plan["hypothesis"], "falsifier": plan["falsifier"],
              "causal_claim": False,
              "interpretation": "Declared parameter changes, not proof of isolated economic causes; "
                                "selection, exposure, risk, turnover and costs can remain coupled."}
    return {"spec": after, "report": report}


def control_identifiability(spec, summary=None, control_spec=None, control_summary=None):
    """Explain known exposure confounding without fabricating a matched account."""
    strategy = validate_strategy(spec)
    allocation = strategy["allocation"]
    cap_boundary = allocation["top_n"] * allocation["max_stock_weight"]
    inverse = allocation["weighting"] == "inverse_volatility"
    structural = (allocation["gross_exposure"] > cap_boundary + 1e-12 or
                  (inverse and allocation["gross_exposure"] > allocation["max_stock_weight"] + 1e-12))
    nonuniform_boundary = (inverse and allocation["top_n"] > 1 and
                           allocation["gross_exposure"] > 0 and
                           cap_boundary <= allocation["gross_exposure"] + 1e-12)
    changed = []
    if control_spec is not None:
        control = validate_strategy(control_spec)
        changed = _differences({key: value for key, value in strategy.items() if key not in {"name", "metadata"}},
                               {key: value for key, value in control.items() if key not in {"name", "metadata"}})
    def exposure(value):
        item = (value or {}).get("mean_exposure")
        return item if type(item) in (int, float) and math.isfinite(item) else None
    left, right = exposure(summary), exposure(control_summary)
    difference = right - left if left is not None and right is not None else None
    return {"status": "not_identified", "risk_information_gain_identified": False,
            "cap_can_reduce_gross_exposure": structural,
            "nonuniform_weights_at_or_below_full_allocation_cap": nonuniform_boundary,
            "mean_exposure_difference_control_minus_proposal": difference,
            "observed_exposure_difference": abs(difference) > 1e-12 if difference is not None else None,
            "changed_domains": _domains(row["path"] for row in changed),
            "limitations": ["Weights are normalized then capped without residual redistribution.",
                            "A weighting or gate control does not by itself match exposure, realized holdings, turnover or costs.",
                            "Saved mean exposure is descriptive; it must not be used as a hindsight trading control."],
            "financial_success": False}
