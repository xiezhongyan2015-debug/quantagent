"""Training-only memberships and evidence-aware three-route factor pools."""
from __future__ import annotations
from collections import Counter, defaultdict
import itertools
import json
import numpy as np


def annual_floor(report):
    annual = report.get("annual", [])
    values = [r.get("mean_pearson_ic") for r in annual if r.get("year") in range(2019, 2025)]
    if len(values) != 6 or any(v is None or not np.isfinite(v) for v in values):
        return None
    return min(values)


def descriptive_pass(report, entity="single_factor"):
    rows = {r["year"]: r for r in report.get("annual", [])}
    threshold = .05 if entity == "single_factor" else .10
    return all(y in rows and rows[y].get("mean_pearson_ic") is not None
               and rows[y]["mean_pearson_ic"] > threshold
               and rows[y].get("valid_days", 0) >= 200
               and (rows[y].get("evaluation_coverage") or 0) >= .8 for y in range(2019, 2025))


def training_quality(row):
    fit = row.get("direction_fit", {})
    value = fit.get("raw_train_mean_pearson_ic")
    return abs(value) if value is not None and np.isfinite(value) else -1.


def factor_pool(rows, reports, *, maximum=46):
    """Retain distinct quality, complement and condition routes after each batch."""
    ready = [dict(r, direction_fit=reports[r["factor_id"]].get("direction_fit", {}))
             for r in rows if r["factor_id"] in reports and r.get("origin") != "control"
             and not reports[r["factor_id"]].get("numeric_duplicate_of")]
    ready.sort(key=lambda r: (-training_quality(r), r["factor_id"]))
    routes = {
        "quality": ready,
        "complementarity": sorted([r for r in ready if reports[r["factor_id"]].get("complementarity_passed")],
                                  key=lambda r: -reports[r["factor_id"]].get("complementarity_delta", 0)),
        "condition": [r for r in ready if set(r.get("roles", [])) & {"risk", "condition", "interaction"}]}
    selected, seen = [], set()
    for i in range(max(map(len, routes.values()), default=0)):
        for route, items in routes.items():
            if i < len(items) and items[i]["factor_id"] not in seen:
                row = dict(items[i], selection_route=route)
                candidate_id = row["factor_id"]
                related = set(reports[candidate_id].get("high_correlation_ids", []))
                redundant = any(x["factor_id"] in related or candidate_id in reports[x["factor_id"]].get("high_correlation_ids", []) for x in selected)
                if redundant and not reports[candidate_id].get("complementarity_passed"):
                    seen.add(candidate_id)
                    continue
                seen.add(row["factor_id"]); selected.append(row)
                if len(selected) >= maximum:
                    return selected
    return selected


def memberships(rows, reports, signatures, *, sizes=(4, 8, 12, 24), offset=0):
    """Three prespecified training-only rules; correlations are labelled sampled.

    offset rotates a training-ranked seed to explore genuine alternative members;
    it never changes orientation or selects a different annual winner.
    """
    viable = [r for r in rows if r["factor_id"] in reports
              and reports[r["factor_id"]].get("direction") in (-1, 1)
              and not reports[r["factor_id"]].get("numeric_duplicate_of")]
    viable.sort(key=lambda r: (-training_quality({"direction_fit": reports[r["factor_id"]].get("direction_fit", {})}), r["factor_id"]))
    if not viable:
        return []
    offset %= len(viable)
    rotated = viable[offset:] + viable[:offset]
    orders = {"quality": rotated}
    chosen, pending = [], list(rotated)
    while pending:
        if not chosen:
            item = pending[0]
        else:
            def score(row):
                corr = max((signature_correlation(signatures.get(row["factor_id"]), signatures.get(x["factor_id"]))
                            for x in chosen), default=0)
                return (corr, -training_quality({"direction_fit": reports[row["factor_id"]].get("direction_fit", {})}), row["factor_id"])
            item = min(pending, key=score)
        chosen.append(item); pending.remove(item)
    orders["diversity"] = chosen
    buckets = defaultdict(list)
    for r in rotated:
        role = "condition" if set(r.get("roles", [])) & {"risk", "condition", "interaction"} else "return"
        buckets[role].append(r)
    ordered = []
    for i in range(max(map(len, buckets.values()), default=0)):
        for role in ("condition", "return"):
            if i < len(buckets[role]):
                ordered.append(buckets[role][i])
    orders["role_balanced"] = ordered
    return [{"rule": rule + f"_training2016_2018_seed{offset}",
             "feature_ids": tuple(sorted(r["factor_id"] for r in order[:size])),
             "candidate_ids": tuple(sorted(r["factor_id"] for r in viable))}
            for rule, order in orders.items() for size in sizes if len(order) >= size]


def signature_correlation(left, right):
    if left is None or right is None:
        return 1.
    mask = np.isfinite(left) & np.isfinite(right)
    if mask.sum() < 100 or np.std(left[mask]) == 0 or np.std(right[mask]) == 0:
        return 1.
    return float(abs(np.corrcoef(left[mask], right[mask])[0, 1]))


def complement_candidates(rows, reports, excluded=(), *, limit=6):
    """Suppressed correlated factors still get a reachable increment experiment."""
    available = [r for r in rows if r["factor_id"] in reports and r["origin"] != "control"
                 and r["factor_id"] not in excluded and not reports[r["factor_id"]].get("numeric_duplicate_of")
                 and not reports[r["factor_id"]].get("complementarity_checked")]
    groups = [[r for r in available if reports[r["factor_id"]].get("high_correlation_ids")],
              [r for r in available if set(r.get("roles", [])) & {"condition", "risk", "interaction"}], available]
    result, seen = [], set()
    for group_items in itertools.zip_longest(*groups):
        for row in group_items:
            if row is not None and row["factor_id"] not in seen:
                result.append(row); seen.add(row["factor_id"])
                if len(result) >= limit:
                    return result
    return result


def combination_specs(rows, reports, signatures, references, *, offset=0):
    from .combination import CombinationSpec
    members = memberships(rows, reports, signatures, offset=offset)
    members.append({"rule": "all_46_legacy_dense_control", "feature_ids": tuple(sorted(references)),
                    "candidate_ids": tuple(sorted(references))})
    result, seen = [], set()
    # Alternate target/update before all lambdas; an interrupted batch stays balanced.
    for target, update, regularizer, member in itertools.product(
            ("raw_return_demeaned", "rank_return_demeaned"),
            ("fixed", "quarterly_rolling3y", "quarterly_expanding"),
            (.001, .01, .1, 1.), members):
        identity = (member["feature_ids"], target, update, regularizer)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(CombinationSpec(name=member["rule"], feature_ids=member["feature_ids"],
            target=target, ridge_lambda=regularizer, update_rule=update,
            selection_rule=member["rule"], candidate_ids=member["candidate_ids"]))
    for member, update in itertools.product(members[:-1], ("fixed",)):
        identity = (member["feature_ids"], "equal_direction", update)
        if identity in seen:
            continue
        seen.add(identity)
        result.insert(0, CombinationSpec(name=member["rule"], feature_ids=member["feature_ids"],
            method="equal_direction", update_rule=update,
            selection_rule=member["rule"], candidate_ids=member["candidate_ids"]))
    return result


def execution_order(specs, completed_reports):
    """Freeze a coverage-first order over unchanged registered specifications.

    Only registration metadata, terminal status and numerical-duplicate status
    are inspected. Performance statistics never affect scheduling. Returned
    objects are the original declarations, once per numeric identity. Previously
    attempted declarations remain at the end for the controller to retain/skip.

    The caller must persist this order and execute it without re-sorting. Planned
    counts below assume an item succeeds; they are NOT realized research counts.
    New failures are replenished when the next order is frozen from new reports.
    No fixed list can guarantee availability of a cell whose every member fails.
    """
    from .combination import CombinationSpec

    sizes = (4, 8, 12, 24)
    targets = ("raw_return_demeaned", "rank_return_demeaned")
    updates = ("fixed", "quarterly_rolling3y", "quarterly_expanding")
    lambdas = (.1, .001, 1., .01)
    identities = {}

    def spec_id(spec):
        key = id(spec)
        if key not in identities:
            # Keep the object alive too: a generator may otherwise release an
            # unused alias and let Python recycle its identity for another spec.
            identities[key] = (spec, spec.combination_id)
        return identities[key][1]

    unique = {}
    for spec in specs:
        if not isinstance(spec, CombinationSpec):
            raise TypeError("execution_order requires registered CombinationSpec objects")
        previous = unique.get(spec_id(spec))
        # Aliases of one numeric recipe do not manufacture extra equal-weight
        # specifications. Choose an existing representative deterministically.
        if previous is None or json.dumps(spec.to_dict(), sort_keys=True) < json.dumps(previous.to_dict(), sort_keys=True):
            unique[spec_id(spec)] = spec

    def shape(spec):
        return len(spec.feature_ids)

    def group(spec):
        return spec.feature_ids

    def cell(spec):
        return (shape(spec), spec.target, spec.update_rule)

    def terminal(report):
        status = report.get("status")
        return isinstance(status, str) and status not in ("pending", "registered", "running")

    def successful(report):
        return report.get("status") == "evaluated" and not report.get("numeric_duplicate_of")

    history = {}
    attempted = set()
    for identifier, report in sorted(completed_reports.items()):
        if not isinstance(report, dict):
            raise TypeError("completed_reports values must be report dictionaries")
        if not terminal(report):
            continue
        attempted.add(identifier)
        spec = unique.get(identifier)
        if spec is None and isinstance(report.get("spec"), dict):
            spec = CombinationSpec.from_dict(report["spec"])
            if spec_id(spec) != identifier:
                raise ValueError("completed report identity differs from its specification")
        if spec is not None:
            history[identifier] = (spec, successful(report))

    pending = {key: spec for key, spec in unique.items() if key not in attempted}
    cells, equal_sizes, size_counts = Counter(), Counter(), Counter()
    member_counts, lambda_counts, method_counts = Counter(), Counter(), Counter()
    failure_groups, failure_cells = Counter(), Counter()
    covered_by_group = defaultdict(set)

    def count(spec):
        n = shape(spec)
        if n not in sizes:
            return
        size_counts[n] += 1
        member_counts[group(spec)] += 1
        method_counts[(n, spec.method)] += 1
        if spec.method == "ridge":
            cells[cell(spec)] += 1
            lambda_counts[(n, spec.ridge_lambda)] += 1
            covered_by_group[group(spec)].add(cell(spec))
        else:
            equal_sizes[n] += 1

    for spec, valid in history.values():
        if valid:
            count(spec)
        else:
            failure_groups[group(spec)] += 1
            failure_cells[(group(spec), spec.target, spec.update_rule)] += 1

    def lambda_rank(value):
        return lambdas.index(value) if value in lambdas else len(lambdas)

    def rule_rank(spec):
        # Rotate the preferred membership route by size while preserving matched
        # targets/updates on an anchor member set. Missing/aliased routes fall
        # back to the existing deterministic declaration, never a new member set.
        preferences = ("quality", "role_balanced", "diversity")
        desired = preferences[sizes.index(shape(spec)) % len(preferences)]
        rule = spec.selection_rule
        return 0 if rule.startswith(desired) else 1

    anchors = {}
    for n in sizes:
        options = [s for s in pending.values() if shape(s) == n and s.method == "ridge"]
        if options:
            preferred = min(options, key=lambda s: (
                failure_groups[group(s)], -len(covered_by_group[group(s)]),
                lambda_rank(s.ridge_lambda), rule_rank(s), group(s), spec_id(s)))
            anchors[n] = group(preferred)

    normal = []

    def take(spec):
        pending.pop(spec_id(spec))
        normal.append(spec)
        count(spec)

    # The first 24 empty cells cycle through sizes before moving to the next
    # target/update. Existing successes skip filled cells, even from older pools.
    for update, target, n in itertools.product(updates, targets, sizes):
        wanted = (n, target, update)
        if cells[wanted]:
            continue
        options = [s for s in pending.values() if s.method == "ridge" and cell(s) == wanted]
        if options:
            take(min(options, key=lambda s: (
                failure_cells[(group(s), s.target, s.update_rule)],
                group(s) != anchors.get(n), lambda_rank(s.ridge_lambda),
                rule_rank(s), group(s), spec_id(s))))
    for n in sizes:
        if equal_sizes[n]:
            continue
        options = [s for s in pending.values() if shape(s) == n and s.method == "equal_direction"]
        if options:
            take(min(options, key=lambda s: (failure_groups[group(s)],
                group(s) != anchors.get(n), rule_rank(s), group(s), spec_id(s))))

    # Thereafter balance sizes, targets/updates, lambdas and genuinely different
    # member sets. Equal weights have one recipe per member set rather than a
    # fictitious lambda/target/update grid, so use the registered method totals.
    method_totals = Counter((shape(s), s.method) for s in unique.values() if shape(s) in sizes)
    while True:
        options = [s for s in pending.values() if shape(s) in sizes]
        if not options:
            break

        def priority(spec):
            n = shape(spec)
            ridge = spec.method == "ridge"
            return (size_counts[n],
                method_counts[(n, spec.method)] / max(1, method_totals[(n, spec.method)]),
                failure_cells[(group(spec), spec.target, spec.update_rule)] if ridge else failure_groups[group(spec)],
                lambda_counts[(n, spec.ridge_lambda)] if ridge else equal_sizes[n],
                member_counts[group(spec)], cells[cell(spec)] if ridge else equal_sizes[n],
                lambda_rank(spec.ridge_lambda) if ridge else -1,
                rule_rank(spec), targets.index(spec.target), updates.index(spec.update_rule),
                group(spec), spec_id(spec))

        take(min(options, key=priority))

    # Dense controls never increment normal coverage counts. A first control is
    # visible early; after an already failed dense attempt, its remaining variants
    # are spaced later. All remain declared and present in the returned queue.
    dense = sorted((s for s in pending.values() if shape(s) == 46), key=lambda s: (
        lambda_rank(s.ridge_lambda), targets.index(s.target), updates.index(s.update_rule),
        group(s), spec_id(s)))
    dense_attempted = any(shape(spec) == 46 for spec, _ in history.values())
    output = []
    first_dense_after = 40 if dense_attempted else 8
    for position, spec in enumerate(normal, start=1):
        output.append(spec)
        if dense and (position == first_dense_after or (position > first_dense_after and (position-first_dense_after) % 40 == 0)):
            output.append(dense.pop(0))
    output.extend(dense)
    # Registered sizes beyond the current main grid are retained, not silently
    # relabelled as a supported coverage cell or removed from evidence.
    output.extend(sorted((s for s in pending.values() if shape(s) not in sizes + (46,)),
                         key=lambda s: spec_id(s)))
    output.extend(unique[key] for key in sorted(unique) if key in attempted)
    if len(output) != len(unique) or len({spec_id(s) for s in output}) != len(unique):
        raise AssertionError("execution scheduling changed the registered identity set")
    return output


def version_decision(state, budget):
    """A budget end triggers a review, never historical goal completion."""
    scale = state.get("primary_evaluated", 0) >= budget["minimum_factors"] and state.get("combinations_evaluated", 0) >= budget["minimum_combinations"]
    cap = state.get("primary_evaluated", 0) >= budget["max_factors"] or state.get("combinations_evaluated", 0) >= budget["max_combinations"] or state.get("numeric_wall_seconds", 0) >= budget["max_numeric_seconds"]
    if cap:
        return "review_completed_scale" if scale else "review_incomplete_scale"
    if scale and state.get("plateau_extensions", 0) >= budget["plateau_extensions"]:
        return "review_completed_scale"
    return "extend" if scale else "complete_minimum"
