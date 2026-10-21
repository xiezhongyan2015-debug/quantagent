"""Source-bound primary candidates; templates expand without model calls."""
from __future__ import annotations
import ast
from collections import Counter
from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path
import re

from quanta_agents.meta_v6.factors import FactorSpec
from quanta_agents.factor_research.contracts import required_fields

FIELDS = {"open", "high", "low", "close", "volume", "amount"}


def validate_control_repair(original, corrected):
    """Only terminal bracket repair preserves this bounded syntax-failure intent."""
    before, after = re.sub(r"\s+", "", original), re.sub(r"\s+", "", corrected)
    alternatives = {before + ")"}
    if before.endswith(")"):
        alternatives.add(before[:-1])
    if after not in alternatives:
        raise ValueError("Control repair may only add/remove one terminal closing bracket; changed meaning requires a new structural proposal")
    if re.sub(r"[()]", "", before) != re.sub(r"[()]", "", after):
        raise ValueError("Control repair altered an operand, operator, constant or placeholder")
    return True


def inventory(path):
    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8-sig"))
    definitions = raw["executable_definitions"]
    if len(definitions) != 46:
        raise ValueError("Expected all 46 frozen executable definitions")
    pin = {"path": str(source.resolve()), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    rows = []
    for item in definitions:
        spec = FactorSpec(item["id"], item["expression"], parents=(), metadata={
            "source_id": item["id"], "roles": item["roles"], "source": item["source"],
            "source_inventory": pin, "prior_results_not_current_evidence": True})
        if required_fields(spec.expression) - FIELDS:
            raise ValueError("Unadmitted signal field in legacy definition")
        rows.append({"factor_id": spec.factor_id, "spec": spec.to_dict(),
                     "family_id": "legacy." + item["id"], "origin": "reference",
                     "roles": item["roles"], "route": "role_preserving_reference",
                     "parents": [], "mechanism": item.get("metadata", {}).get("hypothesis", item["name"]),
                     "source": pin, "controls": [], "entity_type": "single_factor",
                     "lineage": {"aggregation_of_registered_factors": [], "supervised": False}})
    return rows


def library_neighborhoods(references):
    """Window-only controls and primary variants reuse EVERY eligible old parent.

    Literal +/-1/2 constants are not mutated: only trailing window positions.
    They count as new formulas, never as new mechanisms/framework versions.
    """
    window_calls = {"lag", "pct_change", "rolling_mean", "rolling_std", "rolling_sum",
                    "rolling_min", "rolling_max", "ema", "rolling_corr", "rolling_residual", "ts_rank"}
    families = []
    for row in references:
        expression = row["spec"]["expression"]
        tree = ast.parse(expression, mode="eval")
        windows = sorted({n.args[-1].value for n in ast.walk(tree)
                          if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                          and n.func.id in window_calls and isinstance(n.args[-1], ast.Constant)
                          and type(n.args[-1].value) is int and n.args[-1].value >= 3})
        if not windows:
            continue
        for original in windows:
            # One trailing scale at a time is a transparent old-parent test.
            values = sorted(set(max(2, min(120, round(original * ratio)))
                                for ratio in (.4, .6, .8, 1., 1.25, 1.5, 2., 3.)))
            class Replace(ast.NodeTransformer):
                def visit_Call(self, node):
                    self.generic_visit(node)
                    if (isinstance(node.func, ast.Name) and node.func.id in window_calls
                        and isinstance(node.args[-1], ast.Constant) and node.args[-1].value == original):
                        node.args[-1] = ast.Name(id="WINDOW_TOKEN", ctx=ast.Load())
                    return node
            template = ast.unparse(Replace().visit(deepcopy(tree))).replace("WINDOW_TOKEN", "{w}")
            families.append({"family_id": row["family_id"] + f".window{original}",
                "expression_template": template, "parameters": [{"name": "w", "values": values}],
                "role": row["roles"][0], "route": "old_parent_window",
                "mechanism": row["mechanism"], "parents": [row["factor_id"]],
                "controls": [{"name": "original", "expression_template": expression,
                              "rationale": "Exact old definition at unchanged window"}],
                "falsifier": "Failure to improve shared-sample evidence over the original parent",
                "origin": "programmatic_legacy_neighborhood"})
    return families


def expand(families, *, origin, max_attempts=12000):
    """All attempts retain controls, invalid templates, exact duplicates and lineage."""
    rows, attempts, seen = [], [], {}
    for family in families:
        family = deepcopy(family)
        params = family.get("parameters", [])
        if isinstance(params, dict):
            params = [{"name": k, "values": v.get("values", v) if isinstance(v, dict) else v}
                      for k, v in params.items()]
        keys = [p["name"] for p in params]
        if len(set(keys)) != len(keys) or any(not k.isidentifier() for k in keys):
            raise ValueError("Unique identifier parameter names required")
        values = [p["values"] for p in params]
        if any(not v or len(v) > 24 or any(type(x) is not int or not 1 <= x <= 120 for x in v)
               for v in values):
            raise ValueError("Finite integer window ranges in [1,120] required")
        for grid in itertools.product(*values):
            setting = dict(zip(keys, grid))
            if "short" in setting and "long" in setting and setting["short"] >= setting["long"]:
                continue
            primary = {"name": family["family_id"], "expression_template": family["expression_template"]}
            for position, item in enumerate([primary] + family.get("controls", [])):
                if len(attempts) >= max_attempts:
                    return rows, attempts
                attempt = {"family_id": family["family_id"], "parameters": setting,
                           "control": bool(position), "control_name": item.get("name") if position else None}
                try:
                    expression = item["expression_template"].format(**setting)
                    spec = FactorSpec(item["name"], expression, parents=tuple(family.get("parents", [])),
                        metadata={"family_id": family["family_id"], "parameters": setting,
                                  "mechanism": family["mechanism"], "falsifier": family.get("falsifier"),
                                  "origin": origin, "modification": family.get("origin", "model_structure")})
                    if required_fields(spec.expression) - FIELDS:
                        raise ValueError("Unadmitted operand")
                    attempt.update(factor_id=spec.factor_id, status="duplicate" if spec.factor_id in seen else "admitted")
                    # Controls may later also receive explicit primary provenance.
                    if spec.factor_id not in seen:
                        record = {"factor_id": spec.factor_id, "spec": spec.to_dict(),
                            "family_id": family["family_id"], "origin": "control" if position else "new",
                            "roles": [family.get("role", "return")], "route": family.get("route", "structure"),
                            "mechanism": family["mechanism"], "parents": family.get("parents", []),
                            "controls": family.get("controls", []), "parameters": setting,
                            "entity_type": "single_factor", "source": origin,
                            "lineage": {"aggregation_of_registered_factors": family.get("aggregation_of_registered_factors", []),
                                        "supervised": bool(family.get("supervised", False))}}
                        if record["lineage"]["supervised"] or record["lineage"]["aggregation_of_registered_factors"]:
                            record["entity_type"] = "combination"
                        seen[spec.factor_id] = record
                        rows.append(record)
                    elif not position and seen[spec.factor_id]["origin"] == "control":
                        seen[spec.factor_id]["origin"] = "new"
                        seen[spec.factor_id]["primary_provenance"] = attempt.copy()
                except (ValueError, KeyError, TypeError, SyntaxError) as exc:
                    attempt.update(status="implementation_rejected", error=f"{type(exc).__name__}: {exc}",
                                   family=family)
                attempts.append(attempt)
    return rows, attempts


def interleave(rows):
    """Round robin across mechanisms prevents one wide family consuming a batch."""
    groups = {}
    for row in rows:
        groups.setdefault(row["family_id"], []).append(row)
    result = []
    for i in range(max((len(x) for x in groups.values()), default=0)):
        for group in groups.values():
            if i < len(group):
                result.append(group[i])
    return result


def merge_catalog(existing, proposed):
    known = {r["factor_id"]: r for r in existing}
    duplicates = []
    for row in proposed:
        if row["factor_id"] in known:
            duplicates.append({"factor_id": row["factor_id"], "family_id": row["family_id"],
                               "status": "canonical_duplicate", "origin": row["origin"]})
        else:
            known[row["factor_id"]] = row
    return list(known.values()), duplicates


def execution_queue(rows):
    """Reference first, then 1:1 model/legacy primary routes with family rotation.

    Matched controls follow their owning primary but do not consume route slots.
    Order depends only on preregistered metadata, never on measured IC.
    """
    known = {r["factor_id"]: r for r in rows}
    references = [r for r in rows if r["origin"] == "reference"]
    primary = [r for r in rows if r["origin"] not in ("reference", "control")]
    queues = []
    for is_legacy in (False, True):
        group = [r for r in primary if (r.get("route") == "old_parent_window") == is_legacy]
        # Current new exploration leads inherited re-evaluation inside each route.
        queues.append(interleave([r for r in group if r["origin"] == "new"]) +
                      interleave([r for r in group if r["origin"] != "new"]))
    mixed = []
    for pair in itertools.zip_longest(*queues):
        mixed.extend(r for r in pair if r is not None)
    result, seen = [], set()
    for row in references + mixed:
        if row["factor_id"] in seen:
            continue
        result.append(row); seen.add(row["factor_id"])
        for control in row.get("controls", []):
            expression = control["expression_template"].format(**row.get("parameters", {}))
            try:
                fid = FactorSpec(control["name"], expression).factor_id
            except ValueError:
                # Failed control proposals already have retained expansion-attempt evidence.
                continue
            if fid in known and fid not in seen and known[fid]["origin"] == "control":
                result.append(known[fid]); seen.add(fid)
    result.extend(r for r in rows if r["factor_id"] not in seen)
    return result
