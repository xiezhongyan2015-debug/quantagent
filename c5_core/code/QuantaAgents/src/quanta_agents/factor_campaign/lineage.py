"""Durable provenance and separate definition, value and correlation redundancy."""
from __future__ import annotations

from contextlib import contextmanager
import ast
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path
import sqlite3
import time

import numpy as np
import pandas as pd

from quanta_agents.meta_v6.factors import FactorSpec, canonical_expression
from quanta_agents.factor_research.contracts import required_fields
from quanta_agents.research_kernel.store import digest, serial


def canonical_definition(expression):
    expression = canonical_expression(expression)
    return {"expression": expression, "definition_sha256": digest(expression)}


@lru_cache(maxsize=8192)
def _definition_tree(expression):
    # AST objects are private, read-only and contain no market arrays. Reusing
    # repeated 46-library syntax avoids revalidating parents for every child.
    node = ast.parse(canonical_expression(expression), mode="eval").body
    return node, ast.dump(node)


def classify_transformation(expression, parent_definitions=(), *, aggregate_members=(), supervised=False, registered_definitions=()):
    """Registered additive aggregation takes priority over causal modulation.

    Exact original library formulas remain references. Price/volume products or
    ratios are explicit fixed condition interactions, inferred from causal AST
    operands rather than a model label that could hide additive aggregation.
    """
    tree, tree_key = _definition_tree(expression)
    pinned = {_definition_tree(p["expression"])[1] for p in registered_definitions}
    known = {}
    for p in list(registered_definitions) + list(parent_definitions):
        node, key = _definition_tree(p["expression"])
        if not isinstance(node, (ast.Name, ast.Constant)):
            known[key] = p.get("factor_id", p.get("name", p["expression"]))
    def contained(node):
        return {key for child in ast.walk(node) if (key := ast.dump(child)) in known}
    members = set(aggregate_members)
    fixed_reference = tree_key in pinned
    condition = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "where" for node in ast.walk(tree))
    price_volume = False
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Div)):
            left, right = required_fields(ast.unparse(node.left)), required_fields(ast.unparse(node.right))
            prices, activity = {"open", "high", "low", "close"}, {"volume", "amount"}
            price_volume |= bool(left & prices and right & activity or right & prices and left & activity)
    condition |= price_volume
    if not fixed_reference:
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                a, b = contained(node.left), contained(node.right)
                additive = isinstance(node.op, (ast.Add, ast.Sub))
                if a and b and len(a | b) > 1 and (additive or not condition):
                    members.update(known[key] for key in a | b)
    members = sorted(members)
    if supervised:
        kind = "supervised_predictor"
    elif members:
        kind = "registered_factor_aggregation"
    elif condition and not fixed_reference:
        kind = "conditional_interaction"
    else:
        kind = "raw_formula"
    return {"transformation_kind": kind, "aggregate_members": members,
            "parent_definitions": list(parent_definitions), "supervised": bool(supervised),
            "classification_reason": "registered_additive_or_unconditional_aggregate" if members else "exact_original_reference" if fixed_reference else "causal_price_volume_modulation" if price_volume else "explicit_where_condition" if condition else "raw_causal_formula",
            "matched_registered_ids": sorted({known[key] for key in contained(tree)}),
            "acceptance_route": "combination" if supervised or members else "single_factor"}


def value_identity(values, *, sample_mask=None):
    """Actual ordered data + validity mask, not a declared dataset name."""
    if not isinstance(values, pd.DataFrame) or not values.index.is_unique or not values.columns.is_unique:
        raise ValueError("Unique ordered dates and columns required")
    array = values.to_numpy(dtype="<f8", copy=True)
    valid = np.isfinite(array)
    if sample_mask is not None:
        if not values.index.equals(sample_mask.index) or not values.columns.equals(sample_mask.columns):
            raise ValueError("Common sample must align exactly")
        valid &= sample_mask.to_numpy(dtype=bool)
    array[~valid] = 0.0
    array[array == 0] = 0.0  # Canonicalize negative zero.
    layout = {"dates": [str(v) for v in values.index], "columns": [str(v) for v in values.columns]}
    sample = hashlib.sha256(serial(layout).encode() + valid.tobytes()).hexdigest()
    return {"values_sha256": hashlib.sha256(sample.encode() + array.tobytes()).hexdigest(),
            "sample_sha256": sample, "finite_cells": int(valid.sum()), "shape": list(array.shape)}


def redundancy(left, right, *, threshold=.98, min_stocks=100, min_days=200):
    """Correlation uses paired cells and retains its common sample evidence."""
    if not 0 < threshold <= 1:
        raise ValueError("Correlation threshold must be in (0,1]")
    if not left.index.equals(right.index) or not left.columns.equals(right.columns):
        raise ValueError("Redundancy requires exactly aligned panels")
    mask = pd.DataFrame(np.isfinite(left.to_numpy()) & np.isfinite(right.to_numpy()), index=left.index, columns=left.columns)
    a_id, b_id = value_identity(left, sample_mask=mask), value_identity(right, sample_mask=mask)
    correlations = []
    for a, b, valid in zip(left.to_numpy(), right.to_numpy(), mask.to_numpy()):
        if valid.sum() < min_stocks:
            continue
        x, y = a[valid], b[valid]
        if np.std(x) <= 0 or np.std(y) <= 0:
            continue
        correlations.append(float(np.corrcoef(x, y)[0, 1]))
    enough = len(correlations) >= min_days
    rho = float(np.mean(correlations)) if correlations else None
    return {"numeric_duplicate_on_common_sample": a_id["values_sha256"] == b_id["values_sha256"],
            "highly_correlated": enough and abs(rho) >= threshold, "paired_daily_pearson_mean": rho,
            "common_sample_sha256": a_id["sample_sha256"], "common_cells": a_id["finite_cells"],
            "valid_days": len(correlations), "correlation_evaluable": enough, "threshold": threshold,
            "ic_acceptance_evidence": False}


def select_parents(records, *, limit=12):
    """Round-robin role-preserving selection using only explicitly supplied evidence.

    Prior historical metrics may select parents but never become current scores.
    Complement selection needs paired incremental evidence; condition mechanisms
    retain a route independent of marginal return IC.
    """
    if type(limit) is not int or limit < 0:
        raise ValueError("Nonnegative parent limit required")
    pools = {name: [] for name in ("quality", "complement", "condition")}
    for row in records:
        if row.get("status") in {"implementation_failed", "invalid", "control"} or row.get("definition_duplicate_of"):
            continue
        evidence = row.get("evidence", row)
        years = evidence.get("years", [])
        if any(int(year) > 2024 for year in years):
            raise ValueError("Unauthorized year in parent evidence")
        worst = evidence.get("worst_ic", evidence.get("six_year_min_ic"))
        if isinstance(worst, (int, float)) and math.isfinite(worst):
            pools["quality"].append((float(worst), row))
        increment = evidence.get("incremental", {})
        delta = increment.get("delta_ic", increment.get("paired_delta_pearson_ic"))
        if increment.get("common_sample_sha256") and increment.get("passed") is True and isinstance(delta, (int, float)) and math.isfinite(delta) and delta > 0:
            pools["complement"].append((float(delta), row))
        roles = row.get("roles", [row.get("role")])
        if "condition" in roles or "risk" in roles or row.get("route") == "condition":
            pools["condition"].append((float(evidence.get("mechanism_coverage_gain") or 0), row))
    for pool in pools.values():
        pool.sort(key=lambda item: (-item[0], str(item[1].get("factor_id", item[1].get("candidate_id", "")))))
    selected, identities = [], set()
    while len(selected) < limit and any(pools.values()):
        for route, pool in pools.items():
            while pool:
                score, row = pool.pop(0)
                key = row.get("factor_id", row.get("candidate_id"))
                if key not in identities:
                    selected.append({"parent_id": key, "selection_route": route, "selection_score": score,
                                     "evidence_id": row.get("evidence_id"), "historical_parent_metric_not_current_result": True,
                                     "mechanism": row.get("mechanism")})
                    identities.add(key)
                    break
            if len(selected) >= limit:
                break
    return selected


def build_revision(parent, expression, *, reason, repair_number, evidence_ids, allowed_fields=None):
    if type(repair_number) is not int or not 1 <= repair_number <= 2:
        raise ValueError("Implementation repair is bounded to two attempts per parent")
    if not reason or not evidence_ids:
        raise ValueError("Repair needs the recorded failure reason and evidence")
    original = parent if isinstance(parent, FactorSpec) else FactorSpec(**{
        k: parent[k] for k in ("name", "expression", "version", "parents", "metadata") if k in parent})
    admitted = set(allowed_fields) if allowed_fields is not None else required_fields(original.expression)
    if required_fields(expression) - admitted:
        raise ValueError("Repair cannot add unadmitted information fields")
    repaired = FactorSpec(original.name + ".repair" + str(repair_number), expression,
                          parents=(original.factor_id,), metadata={**original.metadata,
                          "repair_of": original.factor_id, "repair_number": repair_number,
                          "repair_reason": reason, "repair_evidence_ids": list(evidence_ids),
                          "must_recompute_values_and_acceptance": True})
    if repaired.factor_id == original.factor_id:
        raise ValueError("Repair has no executable definition change")
    return repaired


class LineageRegistry:
    def __init__(self, root):
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "campaign_lineage.sqlite"
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS candidates(candidate_id TEXT PRIMARY KEY,definition_id TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS relations(relation_id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS sightings(candidate_id TEXT NOT NULL,sighting_id TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(candidate_id,sighting_id))")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def register(self, candidate_id, expression=None, *, parents=(), role="return", route="quality", origin="new", version="V10A", source=None, aggregate_members=(), supervised=False, parent_definitions=(), registered_definitions=()):
        if isinstance(candidate_id, dict):
            row = candidate_id
            spec = row.get("spec", row)
            candidate_id, expression = row.get("candidate_id", row.get("factor_id")), spec["expression"]
            parents = row.get("parents", spec.get("parents", parents))
            role, route = row.get("roles", [row.get("role", role)])[0], row.get("route", route)
            origin, version, source = row.get("origin", origin), row.get("version", version), row.get("source", source)
            original_lineage = row.get("lineage", {})
            aggregate_members = original_lineage.get("aggregate_members", original_lineage.get("aggregation_of_registered_factors", aggregate_members))
            supervised = original_lineage.get("supervised", supervised)
            parent_definitions = original_lineage.get("parent_definitions", parent_definitions)
        normalized = canonical_definition(expression)
        payload = {"candidate_id": candidate_id, **normalized, "parents": list(parents), "role": role,
                   "route": route, "origin": origin, "version": version, "source": source,
                   **classify_transformation(expression, parent_definitions, aggregate_members=aggregate_members,
                                              supervised=supervised, registered_definitions=registered_definitions)}
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT payload FROM candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
            if previous and json.loads(previous[0])["definition_sha256"] != normalized["definition_sha256"]:
                raise ValueError("Candidate definition identity is immutable")
            twins = [r[0] for r in db.execute("SELECT candidate_id FROM candidates WHERE definition_id=? AND candidate_id<>?",
                                            (normalized["definition_sha256"], candidate_id))]
            db.execute("INSERT OR IGNORE INTO candidates VALUES (?,?,?,?)", (candidate_id, normalized["definition_sha256"], serial(payload), time.time()))
            db.execute("INSERT OR IGNORE INTO sightings VALUES (?,?,?,?)", (candidate_id, digest(payload), serial(payload), time.time()))
        return {**payload, "definition_duplicates": twins, "counts_as_new_definition": not previous and not twins and origin == "new",
                "inherited_definition": bool(previous)}

    def record_relation(self, kind, left, right, evidence):
        if kind not in {"definition_duplicate", "numeric_duplicate", "high_correlation", "incremental", "repair", "inherited"}:
            raise ValueError("Unknown lineage relation")
        if kind in {"numeric_duplicate", "high_correlation", "incremental"} and not evidence.get("common_sample_sha256"):
            raise ValueError("Numeric relation must identify the actual common sample")
        payload = {"left": left, "right": right, "kind": kind, "evidence": evidence}
        key = digest(payload)
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO relations VALUES (?,?,?,?)", (key, kind, serial(payload), time.time()))
        return key

    def export(self):
        with self._db() as db:
            return {"candidates": [json.loads(r[0]) for r in db.execute("SELECT payload FROM candidates ORDER BY created,candidate_id")],
                    "relations": [json.loads(r[0]) for r in db.execute("SELECT payload FROM relations ORDER BY created,relation_id")],
                    "sightings": [json.loads(r[0]) for r in db.execute("SELECT payload FROM sightings ORDER BY created,candidate_id,sighting_id")]}


LineageStore = LineageRegistry
