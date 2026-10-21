"""Explicit bounded scheduling of families; never consult confirmation outcomes."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Mapping

from .contracts import FactorFamily
from .generation import require_generation_phase


@dataclass(frozen=True)
class FamilyHistory:
    family_id: str
    attempts: int = 0
    evaluable: int = 0
    duplicates: int = 0
    implementation_failures: int = 0
    best_development_ic: float | None = None
    best_paired_increment: float | None = None
    split: str = "development_check"
    evidence_end_year: int = 2020

    def __post_init__(self):
        if self.split not in {"train", "development_check"} or type(self.evidence_end_year) is not int or self.evidence_end_year > 2020:
            raise ValueError("parent selection cannot consume confirmation or unauthorized years")
        counts = (self.attempts, self.evaluable, self.duplicates, self.implementation_failures)
        if any(type(v) is not int or v < 0 for v in counts) or any(v > self.attempts for v in counts[1:]):
            raise ValueError("history counts must be valid attempted denominators")
        if self.evaluable + self.duplicates + self.implementation_failures > self.attempts:
            raise ValueError("history outcome categories cannot overlap")
        for value in (self.best_development_ic, self.best_paired_increment):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                raise ValueError("history quality must be finite or unknown")


def select_parents(histories: Iterable[FamilyHistory], *, limit: int = 3,
                   phase: str = "development") -> tuple[dict, ...]:
    """Alternate quality, paired-complementarity and underexplored routes.

    This is a transparent allocation heuristic. Low correlation by itself is
    deliberately absent; quality values do not imply acceptance or profitability.
    """
    require_generation_phase(phase)
    histories = tuple(histories)
    if type(limit) is not int or not 0 <= limit <= 12:
        raise ValueError("parent selection limit must be in [0,12]")
    if len({h.family_id for h in histories}) != len(histories):
        raise ValueError("duplicate family history")
    quality = sorted((h for h in histories if h.evaluable and h.best_development_ic is not None),
                     key=lambda h: (-h.best_development_ic, h.attempts, h.family_id))
    complementary = sorted((h for h in histories if h.evaluable and h.best_paired_increment is not None
                             and h.best_paired_increment > 0),
                           key=lambda h: (-h.best_paired_increment, h.attempts, h.family_id))
    exploration = sorted(histories, key=lambda h: (h.attempts, h.implementation_failures, h.family_id))
    routes = (("quality", quality), ("paired_complementarity", complementary), ("underexplored", exploration))
    chosen = []
    seen = set()
    while len(chosen) < min(limit, len(histories)):
        previous = len(chosen)
        for route, ordered in routes:
            available = next((h for h in ordered if h.family_id not in seen), None)
            if available is not None and len(chosen) < limit:
                seen.add(available.family_id)
                chosen.append({"family_id": available.family_id, "route": route,
                               "attempts": available.attempts, "split": available.split,
                               "evidence_end_year": available.evidence_end_year})
        if len(chosen) == previous:
            break
    return tuple(chosen)


def allocate_family_budget(families: Iterable[FactorFamily], *, primary_per_arm: int = 12,
                           max_unique_formulas: int = 72, measured_seconds_per_formula: float | None = None,
                           worker_private_bytes: int | None = None, available_memory_bytes: int | None = None,
                           phase: str = "development") -> dict:
    require_generation_phase(phase)
    families = tuple(families)
    if type(primary_per_arm) is not int or not 1 <= primary_per_arm <= 24:
        raise ValueError("primary arm budget must be a positive bounded integer")
    if type(max_unique_formulas) is not int or not 1 <= max_unique_formulas <= 72:
        raise ValueError("unique formula cap must be in [1,72]")
    by_arm = {}
    for family in families:
        by_arm.setdefault(family.arm, []).append(family)
    allocation = {}
    for arm, group in sorted(by_arm.items()):
        left = primary_per_arm
        for depth in range(max(len(f.parameter_points()) for f in group)):
            for family in group:
                if left and depth < len(family.parameter_points()):
                    allocation[family.family_id] = allocation.get(family.family_id, 0) + 1
                    left -= 1
    control_attempts = sum(allocation.get(f.family_id, 0) * len(f.controls) for f in families)
    primary = sum(allocation.values())
    workers = 1
    if worker_private_bytes is not None or available_memory_bytes is not None:
        if (type(worker_private_bytes) is not int or worker_private_bytes <= 0
                or type(available_memory_bytes) is not int or available_memory_bytes <= 0):
            raise ValueError("both positive measured memory quantities are required")
        # Leave half of currently available memory and never scale above four
        # merely because logical CPUs are plentiful. This is a suggestion only.
        workers = max(1, min(4, available_memory_bytes // (2 * worker_private_bytes)))
    estimate = None
    if measured_seconds_per_formula is not None:
        if type(measured_seconds_per_formula) not in (int, float) or not math.isfinite(measured_seconds_per_formula) or measured_seconds_per_formula <= 0:
            raise ValueError("runtime estimate must come from a finite positive calibration")
        estimate = measured_seconds_per_formula * min(max_unique_formulas, primary + control_attempts) / workers
    return {"family_primary_attempts": allocation, "primary_per_arm_cap": primary_per_arm,
            "primary_attempts": primary, "matched_control_attempts": control_attempts,
            "unique_formula_cap": max_unique_formulas, "suggested_workers": workers,
            "estimated_wall_seconds": estimate, "max_batches": 2, "max_repairs_per_trial": 2,
            "llm_calls_per_parameter_point": 0, "confirmation_access_cap": 1,
            "cost_status": "estimate_only_actual_CPU_wall_memory_tokens_must_be_recorded_by_runner"}


def parameter_neighbors(family: FactorFamily, center: Mapping[str, int], *, limit: int = 4,
                        phase: str = "development") -> tuple[dict[str, int], ...]:
    """Select nearest still-declared joint points without broadening the range."""
    require_generation_phase(phase)
    points = family.parameter_points()
    if dict(center) not in points or type(limit) is not int or not 0 <= limit <= 24:
        raise ValueError("neighbor center and limit must remain in the declared family")
    def distance(point):
        return sum(abs(family.parameters[k].values.index(point[k]) -
                       family.parameters[k].values.index(center[k])) for k in center)
    return tuple(p for p in sorted(points, key=lambda p: (distance(p), tuple(sorted(p.items()))))
                 if p != dict(center))[:limit]
