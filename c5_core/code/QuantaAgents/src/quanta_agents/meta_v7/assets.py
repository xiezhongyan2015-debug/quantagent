"""Shared V7 asset catalogue, exact reviewed daily calendar translations.

No source module is imported/executed, and no price, IC or 2025 data is read by
ingestion. Unsupported definitions stay visible; directory counts are not
executable counts. Numeric caches and their integrity checks remain the existing
AssetRegistry implementation shared by every study using the same root.
"""
from __future__ import annotations

import ast
import hashlib
import math
from pathlib import Path

from quanta_agents.meta_v6.factors import canonical_expression
from quanta_agents.research_kernel.assets import AssetRegistry as _BaseAssetRegistry, _digest, _sha

VERSION = "meta_v7_shared_assets_v1"
_SOURCE = "historical_standard_factors.py"
# Reviewed original, including helpers: fail closed on changed source semantics.
_SOURCE_SHA256 = "4b5b27a2761fd1f8558640127c9f71291d56476a654315af8e0111ef5f2cd61c"


def _lag(x, n=1):
    while n:
        step = min(n, 120)
        x, n = f"lag({x}, {step})", n - step
    return x


def _div(a, b):
    # Calendar helper uses abs(denominator)>1e-12; zero is NOT a valid fallback.
    return f"where(abs({b}) > 0.000000000001, ({a}) / ({b}), 0 / 0)"


def _max(a, b):
    return f"where(({a}) >= ({b}), {a}, {b})"


def _min(a, b):
    return f"where(({a}) <= ({b}), {a}, {b})"


def _ret(n=1):
    return f"({_div('close', _lag('close', n))} - 1)"


def _std(x, n=20):
    # Original helper computes population std. Source float32 export is not
    # reproduced: engine calculations are float64; roundoff is not bit identity.
    return f"(rolling_std({x}, {n}) * {math.sqrt((n-1)/n)!r})"


def _reviewed_expressions():
    change = "(close - lag(close, 1))"
    up = f"rolling_sum({_max(change, '0')}, 14)"
    down = f"rolling_sum({_max(f'-{change}', '0')}, 14)"
    body = "(close-open)"
    iu = f"rolling_sum({_max(body, '0')}, 14)"
    idown = f"rolling_sum({_max(f'-{body}', '0')}, 14)"
    avg = "rolling_mean(close, 20)"
    tr = _max("high-low", _max("abs(high-lag(close,1))", "abs(low-lag(close,1))"))
    typical = "lag((high+low+close)/3,1)"
    multiplier = _div("2*close-high-low", "high-low")
    illiquidity = _div(f"abs({_ret()})", "amount")
    vol = _std("close")
    br_up, br_down = _max("high-lag(close,1)", "0"), _max("lag(close,1)-low", "0")
    cr_up, cr_down = _max(f"high-({typical})", "0"), _max(f"({typical})-low", "0")
    # Exact decomposition into causal smaller windows, not a 120-day substitute.
    highest252 = _max(_max("rolling_max(close,120)", _lag("rolling_max(close,120)", 120)),
                      _lag("rolling_max(close,12)", 240))
    return {
        "short_reversal": _ret(20), "medium_momentum": _ret(126),
        "long_reversal": f"({_div(_lag('close',252),_lag('close',1260))}-1)",
        "high_52_week": _div("close", highest252),
        "mtm": "close-lag(close,12)",
        "roc": f"rolling_mean({_ret(12)},6)",
        "trend_score": "rolling_sum(where(close >= lag(close,1),1,-1),20)",
        "bias": f"100*{_div(f'close-{avg}', avg)}",
        "bbi": "(rolling_mean(close,3)+rolling_mean(close,6)+rolling_mean(close,12)+rolling_mean(close,20))/4",
        "tma": "rolling_mean(rolling_mean(close,10),11)",
        "cmo": f"100*{_div(f'({up})-({down})', f'({up})+({down})')}",
        "psy": "100*rolling_mean(where(close>lag(close,1),1,0),12)",
        "vhf": _div("rolling_max(high,28)-rolling_min(low,28)", f"rolling_sum(abs({change}),28)"),
        "true_range": tr,
        "ar": f"100*{_div('rolling_sum(high-open,26)', 'rolling_sum(open-low,26)')}",
        "br": f"100*{_div(f'rolling_sum({br_up},26)', f'rolling_sum({br_down},26)')}",
        "cr": f"100*{_div(f'rolling_sum({cr_up},26)', f'rolling_sum({cr_down},26)')}",
        "cmf": _div(f"rolling_sum(({multiplier})*volume,20)", "rolling_sum(volume,20)"),
        "imi": f"100*{_div(f'({iu})', f'({iu})+({idown})')}",
        "vroc": f"100*{_div('volume-lag(volume,12)', 'lag(volume,12)')}",
        "volume_oscillator": f"100*{_div('rolling_mean(volume,5)-rolling_mean(volume,10)', 'rolling_mean(volume,10)')}",
        "vama": _div("rolling_sum(close*volume,20)", "rolling_sum(volume,20)"),
        "emv": f"rolling_mean({_div('(high+low-lag(high,1)-lag(low,1))/2',_div('volume','high-low'))},14)",
        "amihud": f"rolling_mean({illiquidity},20)",
        "cv_illiquidity": _div(_std(illiquidity), f"rolling_mean({illiquidity},20)"),
        "total_volatility": _std(_ret()),
        "maximum_return": f"rolling_max({_ret()},20)",
        "minimum_return": f"-rolling_min({_ret()},20)",
        "overnight_return": f"({_div('open','lag(close,1)')}-1)",
        "intraday_return": f"({_div('close','open')}-1)",
        "overnight_gap": f"rolling_sum(abs(log({_div('open','lag(close,1)')})),20)",
        "qst": "rolling_mean(close-open,20)",
        "momentum_acceleration": f"({_div('close',_lag('close',126))}-1)-({_div(_lag('close',126),_lag('close',252))}-1)",
        "acd": f"rolling_sum(where(close>lag(close,1),close-({_min('low','lag(close,1)')}),where(close<lag(close,1),close-({_max('high','lag(close,1)')}),0)),20)",
        "dynamic_momentum_indicator": f"14*{_div(f'rolling_mean({vol},20)',vol)}",
    }


def _shape(expression):
    if not expression:
        return None
    class Shape(ast.NodeTransformer):
        def visit_Constant(self, node):
            return ast.copy_location(ast.Constant(value="parameter"), node)
    return ast.dump(Shape().visit(ast.parse(expression, mode="eval")), include_attributes=False)


class V7AssetRegistry(_BaseAssetRegistry):
    def describe_candidates(self, ids, diagnostic_report=None):
        ids = list(dict.fromkeys(ids))
        if len(ids) > 1000:
            raise ValueError("at most 1000 candidate references")
        selected = [self.get(identity) for identity in ids]
        all_assets, offset = [], 0
        while True:
            page = self.list(limit=1000, offset=offset)
            all_assets.extend(page)
            if len(page) < 1000:
                break
            offset += len(page)
        formula = []
        for item in selected:
            if not item.get("expression"):
                continue
            for other in all_assets:
                if item["id"] == other["id"] or not other.get("expression"):
                    continue
                identical = item["formula_id"] == other["formula_id"]
                if identical or _shape(item["expression"]) == _shape(other["expression"]):
                    formula.append({"query": item["id"], "neighbor": other["id"],
                                    "kind": "canonical_expression_identical" if identical else "same_syntax_shape_different_parameters"})
        neighbors, scope, report_hash = [], None, None
        if diagnostic_report is not None:
            report = diagnostic_report
            if not isinstance(report, dict):
                raise ValueError("training diagnostic report must be an object")
            scope = report.get("scope", {})
            if not isinstance(scope, dict):
                raise ValueError("training diagnostic report scope must be an object")
            if (report.get("status") != "completed" or scope.get("role") not in
                    {"training", "training_development", "previously_exposed_development"}
                    or not scope.get("start") or not scope.get("end")):
                raise ValueError("completed explicitly training-scoped diagnostic report required")
            report_hash = _digest(report)
            known = {item["id"] for item in all_assets}
            for pair in report.get("correlations", []):
                left, right, corr = pair.get("left"), pair.get("right"), pair.get("mean_ic")
                if left not in known or right not in known or not isinstance(corr, (int, float)) or isinstance(corr, bool):
                    continue
                if not math.isfinite(corr) or abs(corr) > 1.0000001:
                    continue
                for query, other in ((left, right), (right, left)):
                    if query in ids:
                        neighbors.append({"query": query, "neighbor": other,
                            "mean_daily_score_rank_correlation": corr,
                            "observed_days": pair.get("observed_days"), "calendar_days": pair.get("calendar_days"),
                            "scope": scope, "evidence_status": "supplied_training_report_descriptive"})
            neighbors.sort(key=lambda row: (-abs(row["mean_daily_score_rank_correlation"]), row["query"], row["neighbor"]))
        return {"version": VERSION, "candidates": selected, "formula_neighbors": formula,
                "score_neighbors": neighbors, "diagnostic_report_sha256": report_hash,
                "scope": scope, "independent_evidence_claimed": False,
                "limitations": ["syntax shape is not algebraic or mechanism equivalence",
                                "score correlation is not factor-return correlation or conditional value",
                                "report scope is caller-supplied; this method does not authenticate data access",
                                "no low-IC admission gate; catalogue availability is not profitability"]}

    def ingest_calendar(self, source_root):
        root = Path(source_root).resolve()
        path = root / _SOURCE
        source_hash = _sha(path)
        if source_hash != _SOURCE_SHA256:
            raise ValueError("calendar source version is unreviewed; no automatic formula translation")
        text = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(text)
        expressions = _reviewed_expressions()
        records, counts = [], {"executable": 0, "unavailable": 0}
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        helper_proofs = [{"function": node.name, "line": node.lineno,
                          "sha256": hashlib.sha256(ast.get_source_segment(text, node).encode()).hexdigest()}
                         for node in functions.values() if node.name.startswith("_")]
        for node in functions.values():
            decorators = [d for d in node.decorator_list if isinstance(d, ast.Call)
                          and isinstance(d.func, ast.Name) and d.func.id == "register_factor"]
            if not decorators:
                continue
            if len(decorators) != 1:
                raise ValueError("ambiguous calendar registration")
            deco = decorators[0]
            name, fields, note = [ast.literal_eval(arg) for arg in deco.args[:3]]
            aliases = next((ast.literal_eval(k.value) for k in deco.keywords if k.arg == "aliases"), ())
            source_segment = ast.get_source_segment(text, node)
            source = {"kind": "reviewed_calendar_original_implementation", "path": str(path),
                      "sha256": source_hash, "function": node.name, "line": node.lineno,
                      "end_line": node.end_lineno, "function_sha256": hashlib.sha256(source_segment.encode()).hexdigest(),
                      "helper_proofs": helper_proofs, "adapter_version": VERSION,
                      "adapter_sha256": _sha(Path(__file__))}
            metadata = {"source_note": note, "aliases": list(aliases), "source_required_fields": list(fields),
                        "signal_price_basis": "caller panel fixed-anchor signal OHLC; original source adjustment provenance remains caller responsibility",
                        "numeric_precision": "float64 engine; source exports float32; formula/missing semantics translated, not byte-identical rounding",
                        "source_parameters": "original implementation including explicitly trial/default windows, not a new optimized parameter choice",
                        "input_values_read": False, "original_direction": None,
                        "historical_arrival_verified": False, "execution_certified": False,
                        "profitability_claim": False}
            expression, reason = expressions.get(node.name), None
            if expression is None:
                if "_ema(" in source_segment or any(x in node.name for x in ("stochastic_rsi", "vidya")):
                    reason = "original EMA/state initialization or missing rules differ from generic engine; no approximation"
                elif set(fields) - {"open", "high", "low", "close", "volume", "amount"}:
                    reason = "requires unavailable share/valuation/raw-price fields and reviewed price semantics"
                else:
                    reason = "original operator/state/missing semantics not yet translated and verified; no approximation"
            else:
                try:
                    expression = canonical_expression(expression)
                except ValueError as exc:
                    reason = f"exact translation exceeds current generic expression bounds: {exc}"
            identity = f"calendar.standard.{node.name}"
            if reason is None:
                roles = ["risk"] if node.name in {"total_volatility", "maximum_return", "minimum_return", "overnight_gap", "cv_illiquidity"} else ["return", "condition"]
                record = self._definition({"id": identity, "name": name, "expression": expression,
                                           "roles": roles, "source": source, "metadata": metadata})
                record["validation"] = "reviewed_exact_formula_translation; generated-source-parity-tested; required panel fields checked at resolve"
            else:
                record = {"id": identity, "name": name, "expression": None, "roles": [],
                          "source": source, "metadata": metadata, "status": "unavailable", "executable": False,
                          "required_fields": list(fields), "formula_id": _digest({"source": source_hash, "function": node.name}),
                          "unavailable_reason": reason, "validation": "original implementation catalogued; not generically executable",
                          "profitability_claim": False, "version": VERSION}
            counts[record["status"]] += 1
            records.append(record)
        if _sha(path) != source_hash:
            raise ValueError("calendar source changed while reading metadata")
        saved = self._store(records)
        return {"version": VERSION, "source": {"path": str(path), "sha256": source_hash},
                "source_definitions": len(records), **counts, "catalogued": 0,
                "unavailable_count": counts["unavailable"],
                "ids": [r["id"] for r in saved],
                "executable_ids": [r["id"] for r in saved if r["executable"]],
                "unavailable": [{"id": r["id"], "reason": r["unavailable_reason"]} for r in saved if not r["executable"]],
                "limits": ["only reviewed standard daily implementation, not all calendar directory records",
                           "minute/industry/level2 and other modules are not admitted by this adapter",
                           "no source module execution, market data, IC or 2025 numeric data read",
                           "generic cache/same-formula identity includes actual panel and engine source"]}


# Controller compatibility: importing AssetRegistry from V7 always selects the
# shared V7 subclass, never accidentally the old catalogue-only implementation.
AssetRegistry = V7AssetRegistry
