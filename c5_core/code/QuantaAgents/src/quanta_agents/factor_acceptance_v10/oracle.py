"""Reference statistics and trusted-source replay, separate from research code.

This provides integrity checks, not protection from an administrator who can
rewrite both files and hashes. Arrays passed to the small metric functions are
test/diagnostic inputs only. Historical acceptance requires audit_package(),
which reconstructs labels from the separately pinned source authority and
replays the candidate before it can issue an accepted verdict.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

VERSION = "v10a_independent_acceptance_1"
YEARS = tuple(range(2019, 2025))
FROZEN_PROTOCOL = {
    "version": VERSION, "target_years": list(YEARS),
    "single_factor_threshold": .05, "combination_threshold": .10,
    "comparison": "strictly_greater", "metric": "annual_mean_daily_Pearson_IC",
    "label": "open[t+6]/open[t+1]-1", "return_transform": "none",
    "purge": "last_six_signal_sessions_each_natural_year",
    "direction_train_start": "2016-01-01", "direction_train_end": "2018-12-31",
    "numeric_start": "2015-01-01", "numeric_end": "2024-12-31",
    "min_cross_section": 100, "min_valid_days_per_year": 200,
    "min_evaluation_coverage": .8,
    "coverage_denominator": "all_signal_pool_cells_including_purged_and_missing",
    "universe": "historical_CSI300_120close_20positive_amount_nonST_nondelisting",
    "independent_holdout": False, "profitability_proven": False,
    "success_label": "六年历史达标",
}


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _write(path, value):
    Path(path).write_text(_json(value) + "\n", encoding="utf-8")


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def identity(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _dates(values):
    days = [str(value)[:10] for value in values]
    _need(bool(days) and days == sorted(set(days)), "unique chronological dates required")
    _need(all(len(day) == 10 and str(pd.Timestamp(day).date()) == day for day in days),
          "exact calendar dates required")
    _need(days[0] >= "2015-01-01" and days[-1] <= "2024-12-31",
          "2025 numeric data is unauthorized")
    return days


def scalar_pearson(left, right):
    """Independent scalar centered dot product; no production evaluator calls."""
    _need(len(left) == len(right), "paired lengths differ")
    pairs = [(float(a), float(b)) for a, b in zip(left, right)
             if math.isfinite(float(a)) and math.isfinite(float(b))]
    if len(pairs) < 2:
        return None
    mx = math.fsum(a for a, _ in pairs) / len(pairs)
    my = math.fsum(b for _, b in pairs) / len(pairs)
    xx = math.fsum((a - mx) ** 2 for a, _ in pairs)
    yy = math.fsum((b - my) ** 2 for _, b in pairs)
    if xx <= 0 or yy <= 0:
        return None
    return math.fsum((a - mx) * (b - my) for a, b in pairs) / math.sqrt(xx * yy)


def ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    result = np.empty(len(values), dtype=float)
    first = 0
    while first < len(order):
        last = first + 1
        while last < len(order) and values[order[last]] == values[order[first]]:
            last += 1
        result[order[first:last]] = (first + 1 + last) / 2
        first = last
    return result


def raw_labels(dates, opening, observed):
    """Build all labels directly; every year's six endpoints are unavailable."""
    dates = _dates(dates)
    opening, observed = np.asarray(opening, float), np.asarray(observed)
    _need(opening.ndim == 2 and opening.shape == observed.shape and len(opening) == len(dates),
          "source endpoint axes differ")
    result = np.full(opening.shape, np.nan)
    for row, day in enumerate(dates):
        terminal = row + 6
        if terminal >= len(dates) or dates[terminal][:4] != day[:4]:
            continue
        a, b = opening[row + 1], opening[terminal]
        valid = np.isfinite(a) & np.isfinite(b) & (a > 0) & (b > 0)
        valid &= (observed[row + 1] == 1) & (observed[terminal] == 1)
        result[row, valid] = b[valid] / a[valid] - 1
    return result


def independent_daily(dates, opening, scores, pool, *, observed=None,
                      start="2019-01-01", end="2024-12-31", direction=1,
                      minimum=100, diagnostics=True):
    dates = _dates(dates)  # Gate metadata before materializing numeric matrices.
    _need("2015-01-01" <= start <= end <= "2024-12-31", "unauthorized evaluation scope")
    _need(type(direction) is int and direction in (-1, 1), "one fixed direction required")
    _need(type(minimum) is int and minimum >= 2, "positive metric sample requirement")
    scores, pool, opening = np.asarray(scores, float), np.asarray(pool), np.asarray(opening, float)
    _need(scores.shape == pool.shape == opening.shape and len(scores) == len(dates), "score/source axes differ")
    _need(pool.dtype == bool, "explicit boolean signal pool required")
    observed = np.ones(opening.shape) if observed is None else observed
    labels = raw_labels(dates, opening, observed)
    output = []
    for row, day in enumerate(dates):
        if not start <= day <= end:
            continue
        eligible = pool[row]
        finite_score = eligible & np.isfinite(scores[row])
        common = finite_score & np.isfinite(labels[row])
        endpoint = row + 6 < len(dates) and dates[row + 6][:4] == day[:4] and dates[row + 6] <= end
        if not endpoint:
            common[:] = False
        x, y = direction * scores[row, common], labels[row, common]
        pearson = scalar_pearson(x, y) if len(x) >= minimum else None
        rank = scalar_pearson(ranks(x), ranks(y)) if diagnostics and len(x) >= minimum else None
        quintiles, top_return, top_excess, top_complete = None, None, None, False
        if diagnostics and len(x) >= minimum:
            order = np.argsort(x, kind="stable")
            quintiles = [float(np.mean(y[group])) for group in np.array_split(order, 5)]
            # Head selection happens before looking at any future label. No replacement.
            eligible_indices = np.flatnonzero(finite_score)
            order_head = np.argsort(direction * scores[row, eligible_indices], kind="stable")
            head = eligible_indices[order_head[-40:]]
            top_complete = len(head) == 40 and bool(np.isfinite(labels[row, head]).all()) and endpoint
            if top_complete:
                top_return = float(np.mean(labels[row, head]))
                pool_y = labels[row, eligible & np.isfinite(labels[row])]
                top_excess = top_return - float(np.mean(pool_y)) if len(pool_y) else None
        output.append({"date": day, "pearson_ic": pearson, "rank_ic": rank,
            "paired_count": int(common.sum()), "pool_count": int(eligible.sum()),
            "factor_count": int(finite_score.sum()), "label_safe": bool(endpoint),
            "quintile_returns": quintiles, "top40_return": top_return,
            "top40_excess": top_excess, "top40_complete": top_complete})
    return output


def _mean(values):
    finite = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return math.fsum(finite) / len(finite) if finite else None


def independent_annual(rows, *, years=YEARS):
    _need(len({row["date"] for row in rows}) == len(rows), "duplicate daily dates")
    output = []
    for year in years:
        part = [row for row in rows if row["date"][:4] == str(year)]
        pool = sum(row["pool_count"] for row in part)
        paired = sum(row["paired_count"] for row in part)
        output.append({"year": year, "mean_pearson_ic": _mean(row["pearson_ic"] for row in part),
            "pearson_interval": _interval([row["pearson_ic"] for row in part]),
            "mean_rank_ic": _mean(row["rank_ic"] for row in part),
            "valid_days": sum(row["pearson_ic"] is not None for row in part),
            "signal_days": len(part), "pool_cells": pool, "paired_cells": paired,
            "evaluation_coverage": paired / pool if pool else 0.,
            "purged_signal_dates": [row["date"] for row in part if not row["label_safe"]],
            "quintile_returns": [_mean(row["quintile_returns"][i] for row in part
                if row.get("quintile_returns") is not None) for i in range(5)],
            "top40_return": _mean(row.get("top40_return") for row in part),
            "top40_excess": _mean(row.get("top40_excess") for row in part),
            "top40_complete_days": sum(row.get("top40_complete", False) for row in part)})
    return output


def _interval(values):
    """Descriptive moving-session-block interval, never an acceptance gate."""
    data = np.array([np.nan if x is None else x for x in values], float)
    base = {"method": "moving_session_block_bootstrap", "block_sessions": 20,
            "samples": 300, "seed": 20260913, "multiple_testing_corrected": False}
    if len(data) < 40 or np.isfinite(data).sum() < 40:
        return {**base, "lower": None, "upper": None}
    rng = np.random.default_rng(20260913)
    means = []
    for _ in range(300):
        starts = rng.integers(0, len(data) - 19, size=math.ceil(len(data) / 20))
        sample = np.concatenate([data[s:s + 20] for s in starts])[:len(data)]
        sample = sample[np.isfinite(sample)]
        if len(sample):
            means.append(float(sample.mean()))
    return {**base, "lower": float(np.quantile(means, .025)), "upper": float(np.quantile(means, .975))}


def judge_annual(annual, entity_type):
    """Point-estimate/support decision only; this never certifies an artifact."""
    _need(entity_type in ("single_factor", "combination"), "unknown entity classification")
    _need([r["year"] for r in annual] == list(YEARS), "all six ordered years required")
    threshold = FROZEN_PROTOCOL[entity_type + "_threshold"]
    failures = []
    for row in annual:
        reasons = []
        value = row["mean_pearson_ic"]
        if value is None or not math.isfinite(value) or not value > threshold:
            reasons.append("pearson_not_strictly_above_threshold")
        if row["valid_days"] < 200:
            reasons.append("fewer_than_200_valid_days")
        if row["evaluation_coverage"] is None or not math.isfinite(row["evaluation_coverage"]) or row["evaluation_coverage"] < .8:
            reasons.append("evaluation_coverage_below_80_percent")
        if reasons:
            failures.append({"year": row["year"], "reasons": reasons})
    values = [r["mean_pearson_ic"] for r in annual]
    worst = min(values) if all(v is not None and math.isfinite(v) for v in values) else None
    return {"point_estimate_passed": not failures, "threshold": threshold, "failures": failures,
        "worst_year_pearson_ic": worst, "gap_to_target": threshold - worst if worst is not None else None,
        "accepted": False, "scope": "diagnostic_until_pinned_source_and_replay_verified"}


def freeze_oracle(directory):
    """Explicit initial freeze only; mismatches must block, never rewrite pins."""
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    files = {str(p.resolve()): file_hash(p) for p in Path(__file__).parent.glob("*.py")}
    package_root = Path(__file__).parent.parent
    for relative in ("__init__.py", "meta/__init__.py", "meta/factor_algebra.py", "meta_v6/__init__.py",
                     "meta_v6/data.py", "meta_v6/factors.py"):
        path = package_root / relative
        files[str(path.resolve())] = file_hash(path)
    record = {"version": VERSION, "protocol": json.loads(_json(FROZEN_PROTOCOL)), "source_files": files,
              "runtime": {"python": sys.version, "numpy": np.__version__, "pandas": pd.__version__},
              "integrity_boundary": "frozen_hash_checks_not_same_user_OS_isolation"}
    path = root / "oracle_freeze.json"
    if path.exists():
        _need(_read(path) == record, "frozen evaluator changed; external defect review required")
    else:
        _write(path, record)
    return record


def verify_oracle(directory):
    record = _read(Path(directory) / "oracle_freeze.json")
    _need(record["protocol"] == FROZEN_PROTOCOL and record["version"] == VERSION,
          "frozen evaluator protocol changed")
    _need(record["runtime"] == {"python": sys.version, "numpy": np.__version__, "pandas": pd.__version__},
          "frozen evaluator runtime changed")
    for path, expected in record["source_files"].items():
        _need(file_hash(path) == expected, "frozen evaluator source changed: " + path)
    _need(str(Path(__file__).resolve()) in record["source_files"], "running evaluator is not frozen evaluator")
    return record


def _source_pool(fields, dates, columns, membership_path):
    """Membership and eligibility independently reconstructed from raw inputs."""
    member = np.zeros((len(dates), len(columns)), dtype=bool)
    column_map = {name: i for i, name in enumerate(columns)}
    day_array = np.array(dates)
    for line in Path(membership_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        code, first, last = line.split()
        if code.lower() in column_map:
            member[(day_array >= first[:10]) & (day_array <= last[:10]), column_map[code.lower()]] = True
    close, amount = fields["close"], fields["amount"]
    count = np.cumsum(np.isfinite(close), axis=0, dtype=np.int32)
    previous = np.zeros(count.shape, np.int32)
    previous[120:] = count[:-120]
    seasoned = count - previous == 120
    valid_amount = np.isfinite(amount)
    amount_sum = np.cumsum(np.where(valid_amount, amount, 0.), axis=0)
    amount_count = np.cumsum(valid_amount, axis=0, dtype=np.int32)
    lag_sum = np.zeros(amount_sum.shape)
    lag_sum[20:] = amount_sum[:-20]
    lag_count = np.zeros(amount_count.shape, np.int32)
    lag_count[20:] = amount_count[:-20]
    liquid = (amount_count - lag_count == 20) & (amount_sum - lag_sum > 0)
    return member & seasoned & liquid & (fields["is_st"] == 0) & (fields["is_delisting"] == 0)


def freeze_authority(directory, request):
    """Authority is made by the pinned old source loader, never from submissions."""
    root = Path(directory).resolve()
    verify_oracle(root)
    required = {"data_root", "calendar_path", "membership_path", "start", "end"}
    _need(set(request) == required and request["start"] == "2015-01-01"
          and request["end"] == "2024-12-31", "fixed full authorized source request required")
    if (root / "authority.json").exists():
        authority = _read(root / "authority.json")
        _need(authority["request"] == request, "source authority already frozen with different request")
        _load_authority(root)
        return authority
    from quanta_agents.meta_v6.data import load_market_panel
    panel = load_market_panel(request["data_root"], start=request["start"], end=request["end"],
        authorized_start=request["start"], authorized_end=request["end"],
        calendar_path=request["calendar_path"], membership_path=request["membership_path"],
        optional_fields=("is_st", "is_delisting"), exposure="previously_exposed_development")
    dates = _dates(panel.dates.astype(str).tolist())
    columns = list(panel.eligible.columns)
    names = ("open", "high", "low", "close", "volume", "amount", "is_st", "is_delisting", "open_observed")
    fields = {name: panel.fields[name].to_numpy(dtype=float) for name in names}
    pool = _source_pool(fields, dates, columns, request["membership_path"])
    source = panel.provenance["request"]
    pins = {str(request["calendar_path"]): file_hash(request["calendar_path"]),
            str(request["membership_path"]): file_hash(request["membership_path"])}
    for proof in source["source_files"]:
        _need(file_hash(proof["path"]) == proof["sha256"], "source changed during authority creation")
        pins[proof["path"]] = proof["sha256"]
    source_manifest = Path(request["data_root"]) / "manifest.json"
    if source_manifest.exists():
        pins[str(source_manifest)] = file_hash(source_manifest)
    np.savez(root / "authority.npz", dates=np.array(dates), columns=np.array(columns), pool=pool, **fields)
    authority = {"version": VERSION, "request": request, "source_provenance": panel.provenance,
        "source_file_pins": pins, "snapshot_sha256": file_hash(root / "authority.npz"),
        "dates": dates, "columns": columns, "axes_identity": identity({"dates": dates, "columns": columns}),
        "created_by": "independent_source_loader_and_membership_pool_reconstruction",
        "source_limitations": ["existing_fixed_anchor_adjustment", "historical_arrival_times_unproven"],
        "numeric_end": dates[-1]}
    _write(root / "authority.json", authority)
    _write(root / "authority_identity.json", {"authority_sha256": file_hash(root / "authority.json")})
    return authority


def _load_authority(directory, *, verify_sources=True):
    root = Path(directory)
    verify_oracle(root)
    expected = _read(root / "authority_identity.json")["authority_sha256"]
    _need(file_hash(root / "authority.json") == expected, "source authority metadata changed")
    authority = _read(root / "authority.json")
    _need(file_hash(root / "authority.npz") == authority["snapshot_sha256"], "source authority snapshot corrupt")
    if verify_sources:
        for path, expected in authority["source_file_pins"].items():
            _need(file_hash(path) == expected, "original source changed: " + path)
    with np.load(root / "authority.npz", allow_pickle=False) as saved:
        dates = _dates(saved["dates"].tolist())
        columns = saved["columns"].tolist()
        _need(dates == authority["dates"] and columns == authority["columns"], "authority axes changed")
        calendar = [line.strip()[:10] for line in Path(authority["request"]["calendar_path"]).read_text(encoding="utf-8").splitlines() if line.strip()]
        _need(dates == [d for d in calendar if "2015-01-01" <= d <= "2024-12-31"], "incomplete source calendar")
        arrays = {name: saved[name].copy() for name in saved.files if name not in ("dates", "columns")}
    return authority, arrays


def _spec(value):
    from quanta_agents.meta_v6.factors import FactorSpec
    return FactorSpec(value["name"], value["expression"], value.get("version", "1"),
                      tuple(value.get("parents", [])), value.get("metadata", {}))


def _engine(authority, arrays, *, end=None):
    from quanta_agents.meta_v6.data import MarketPanel
    from quanta_agents.meta_v6.factors import FactorEngine
    dates = pd.DatetimeIndex(authority["dates"], name="date")
    columns = pd.Index(authority["columns"], name="symbol")
    limit = len(dates) if end is None else int(dates.searchsorted(end, side="right"))
    fields = {name: pd.DataFrame(arrays[name][:limit], index=dates[:limit], columns=columns)
              for name in ("open", "high", "low", "close", "volume", "amount")}
    pool = pd.DataFrame(arrays["pool"][:limit], index=dates[:limit], columns=columns)
    return FactorEngine(MarketPanel(fields, pool, {"oracle_source": authority["axes_identity"]}),
                        max_cache_bytes=64 * 1024**2)


def _replay_factors(authority, arrays, factors, *, causal=True):
    engine = _engine(authority, arrays)
    outputs = {}
    for value in factors:
        spec = _spec(value)
        _need(spec.factor_id not in outputs, "duplicate formula in exported dependency set")
        outputs[spec.factor_id] = engine.compute(spec).to_numpy(dtype=float)
    if causal:
        cut = "2018-12-31"
        prefix = _engine(authority, arrays, end=cut)
        for value in factors:
            spec = _spec(value)
            actual = prefix.compute(spec).to_numpy(dtype=float)
            _need(np.allclose(actual, outputs[spec.factor_id][:len(actual)], rtol=1e-11, atol=1e-12, equal_nan=True),
                  "formula changed past output after future tail extension")
    return outputs


def freeze_registry(directory, definitions, *, source_path):
    """Pin executable source catalogue before research; same contents on resume."""
    root = Path(directory)
    verify_oracle(root)
    record = {"definitions": definitions, "source_path": str(Path(source_path).resolve()),
              "source_sha256": file_hash(source_path)}
    path = root / "registry.json"
    if path.exists():
        _need(_read(path) == record, "independent registry already frozen")
    else:
        _write(path, record)
        _write(root / "registry_identity.json", {"sha256": file_hash(path)})
    return record


def classify_formula(formula, lineage, registered):
    """Catch explicit or inlined multi-factor aggregates without banning raw DSL."""
    kind = lineage.get("transformation_kind")
    _need(kind in ("raw_formula", "conditional_interaction", "registered_factor_aggregation", "supervised_predictor"),
          "explicit transformation_kind provenance required")
    sources = lineage.get("aggregation_of_registered_factors")
    _need(isinstance(sources, list) and type(lineage.get("supervised")) is bool,
          "complete aggregation and supervision provenance required")
    if sources or lineage["supervised"] or kind in ("registered_factor_aggregation", "supervised_predictor"):
        return "combination"
    tree = ast.parse(formula, mode="eval")
    pinned_whole = {ast.dump(ast.parse(entry["expression"], mode="eval").body, include_attributes=False)
                    for entry in registered}
    if ast.dump(tree.body, include_attributes=False) in pinned_whole:
        # Existing raw definitions are references, not newly inlined aggregates.
        # Explicit aggregation/supervision provenance above still takes priority.
        return "single_factor"
    # A single raw field is not an aggregate merely because a library entry
    # also exposes that field. Compare complete nontrivial registered formulas.
    known = {}
    for entry in registered + lineage.get("parent_definitions", []):
        node = ast.parse(entry["expression"], mode="eval").body
        if not isinstance(node, (ast.Name, ast.Constant)):
            known[ast.dump(node, include_attributes=False)] = entry.get("name", entry["expression"])
    def contained(node):
        found = set()
        for child in ast.walk(node):
            key = ast.dump(child, include_attributes=False)
            if key in known:
                found.add(key)
        return found
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            left, right = contained(node.left), contained(node.right)
            if left and right and len(left | right) > 1:
                return "combination"
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Div)):
            left, right = contained(node.left), contained(node.right)
            if left and right and len(left | right) > 1 and kind != "conditional_interaction":
                return "combination"
    return "single_factor"


def audit_package(package_dir, authority_dir):
    """Full-source verification + formula/model replay + independent acceptance."""
    started, cpu = time.perf_counter(), time.process_time()
    root = Path(package_dir).resolve()
    package = _read(root / "package.json")
    registration_path = (root / package["registration_file"]).resolve()
    _need(registration_path.is_relative_to(root), "registration artifact must stay inside package")
    _need(file_hash(registration_path) == package["registration_sha256"], "preregistered recipe changed")
    registration = _read(registration_path)
    if "factor" in package:
        _need(registration["factor_id"] == package["entity_id"] and registration["spec"] == package["factor"]
              and registration["entity_type"] == package["entity_type"] and registration["lineage"] == package["lineage"],
              "factor export differs from preregistered definition and provenance")
    else:
        _need(registration == package["spec"], "combination export differs from preregistered complete recipe")
    authority, arrays = _load_authority(authority_dir)
    path = (root / package.get("scores_file", "scores.npz")).resolve()
    _need(path.is_relative_to(root), "score artifact must stay inside package")
    _need(file_hash(path) == package["scores_sha256"], "submitted scores corrupted")
    with np.load(path, allow_pickle=False) as saved:
        dates = _dates(saved["dates"].tolist())
        _need(dates == authority["dates"] and saved["columns"].tolist() == authority["columns"],
              "submission must contain every authoritative date and exact column order")
        scores = saved["scores"].astype(float)
    _need(scores.shape == arrays["pool"].shape, "submitted score axes differ")
    kind = package["entity_type"]
    _need(kind in ("single_factor", "combination"), "unknown entity classification")
    direction, training = 1, None
    if "factor" in package:
        lineage = package["lineage"]
        _need(lineage.get("supervised") is False, "supervised predictor needs complete model scheme replay")
        registry_path = Path(authority_dir) / "registry.json"
        _need(registry_path.exists(), "independently pinned executable registry required")
        registry_pin = _read(Path(authority_dir) / "registry_identity.json")["sha256"]
        _need(file_hash(registry_path) == registry_pin, "independent registry changed")
        registry = _read(registry_path)
        _need(file_hash(registry["source_path"]) == registry["source_sha256"], "registered source catalogue changed")
        _need(classify_formula(package["factor"]["expression"], lineage, registry["definitions"]) == kind,
              "formula classification differs from registered-factor aggregation provenance")
        spec = _spec(package["factor"])
        _need(package["entity_id"] == spec.factor_id, "factor identity does not match formula")
        replay = _replay_factors(authority, arrays, [package["factor"]])[spec.factor_id]
        _need(np.allclose(scores, replay, rtol=1e-11, atol=1e-12, equal_nan=True), "factor reimport replay differs")
        train_rows = independent_daily(dates, arrays["open"], replay, arrays["pool"],
            observed=arrays["open_observed"], start="2016-01-01", end="2018-12-31", diagnostics=False)
        mean = _mean(r["pearson_ic"] for r in train_rows)
        count = sum(r["pearson_ic"] is not None for r in train_rows)
        _need(mean is not None and mean != 0 and count >= 60, "unknown training direction")
        direction = 1 if mean > 0 else -1
        _need(package["claimed_direction"] == direction and type(package["claimed_direction"]) is int,
              "direction differs from frozen 2016-2018 training")
        training = {"start": "2016-01-01", "end": "2018-12-31", "mean_pearson_ic": mean,
                    "valid_days": count, "direction": direction}
    else:
        from .replay import replay_combination
        spec = package["spec"]
        payload = {key: spec[key] for key in ("feature_ids", "method", "target", "ridge_lambda", "update_rule", "fit_start", "fit_end")}
        if spec["method"] == "equal_direction":
            payload.pop("target")
            payload.pop("ridge_lambda")
        _need(package["entity_id"] == "combo_" + identity(payload)[:24] == spec["combination_id"],
              "combination identity differs from complete fixed numerical scheme")
        factor_values = _replay_factors(authority, arrays, package["factors"])
        replay = replay_combination(package, authority, arrays, factor_values)
        _need(np.allclose(scores, replay, rtol=1e-9, atol=1e-11, equal_nan=True), "combination complete replay differs")
    daily = independent_daily(dates, arrays["open"], scores, arrays["pool"],
                              observed=arrays["open_observed"], direction=direction)
    annual = independent_annual(daily)
    verdict = judge_annual(annual, kind)
    result = {"version": VERSION, "entity_id": package["entity_id"], "entity_type": kind,
        "protocol_identity": identity(FROZEN_PROTOCOL), "package_sha256": file_hash(root / "package.json"),
        "authority_sha256": file_hash(Path(authority_dir) / "authority.json"),
        "scores_sha256": package["scores_sha256"], "source_verified": True,
        "replay_verified": True, "training_direction": training, "annual": annual, "daily": daily,
        "verdict": {**verdict, "accepted": verdict["point_estimate_passed"],
            "scope": "six_year_exposed_history", "conclusion": "六年历史达标" if verdict["point_estimate_passed"] else "六年历史未达标"},
        "independent_holdout": False, "profitability_proven": False,
        "cost": {"wall_seconds": time.perf_counter() - started, "cpu_seconds": time.process_time() - cpu,
                 "monetary_cost": None}}
    _write(root / "independent_acceptance.json", result)
    return result
