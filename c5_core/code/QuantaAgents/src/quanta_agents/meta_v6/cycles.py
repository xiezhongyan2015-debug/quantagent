"""Versioned evidence-led combination cycles after the initial study closes.

A new cycle retains all earlier proposals and outcomes. It cannot rename earlier
data as unseen, change their receipts, or silently extend an old stage's budget.
The user's continuing goal has no overall search budget; local cycle allocations
are explicit and cumulative attempts remain visible.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from .portfolio import PortfolioSpec
from .portfolio_study import file_hash, read
from .research import call_researcher, dump, object_schema, save_once, TEXT


CYCLE = "02_evidence_led_combinations"
COMPONENT_SCHEMA = object_schema({"factor_key": TEXT, "weight": {"type": "number"}})
PORTFOLIO_SCHEMA = object_schema({
    "name": TEXT, "components": {"type": "array", "items": COMPONENT_SCHEMA},
    "top_n": {"type": "integer"}, "membership_buffer": {"type": "integer"},
    "weighting": {"type": "string", "enum": ["equal", "inverse_volatility"]},
    "market_filter": {"type": "string", "enum": ["none", "trend60", "trend120"]},
    "crowding_gate_factor_key": TEXT, "hypothesis": TEXT,
    "difference_from_other_candidate": TEXT, "falsification": TEXT,
})
DESIGN_SCHEMA = object_schema({
    "portfolios": {"type": "array", "items": PORTFOLIO_SCHEMA},
    "learning_from_rejected_plans": TEXT, "evidence_and_limitations": TEXT,
    "selection_and_stop_rule": TEXT,
})


def initialize_cycle(root):
    root = Path(root)
    previous = read(root / "account_stage_result.json")
    if previous.get("status") != "no_admitted_combination":
        raise ValueError("this cycle follows the terminal rejection of initial A-D")
    rejection_path = root / "model_calls/02_combination_admission/admitted_receipt.json"
    rejection = read(rejection_path)
    if rejection.get("runtime_identity", {}).get("verified") is not True:
        raise ValueError("previous researcher decision lacks verified local identity")
    if (len(rejection["response"]["decisions"]) != 4
            or {d["candidate"] for d in rejection["response"]["decisions"]} != {"A", "B", "C", "D"}) or any(
            d["decision"] != "reject" for d in rejection["response"]["decisions"]):
        raise ValueError("all four previous proposals must remain explicitly rejected")
    if previous.get("decisions") != rejection["response"]["decisions"]:
        raise ValueError("terminal stage decisions differ from the verified researcher rejection")
    index = read(root / "factor_index.json")
    if file_hash(root / "fit_factor_view.json") != index["fit_factor_view_sha256"]:
        raise ValueError("factor fitting evidence changed")
    folder = root / "cycles" / CYCLE
    manifest = {"cycle_id": CYCLE, "reason": "design combinations after factor testing rather than preassemble all combinations before evidence",
                "prior_stage": {"result_sha256": file_hash(root / "account_stage_result.json"),
                                "model_rejection_sha256": file_hash(rejection_path),
                                "portfolio_proposals": 4, "account_candidates_executed": 0,
                                "all_proposals_and_failures_retained": True},
                "numeric_scope": "2016-2020 only, using original factor view with 21-session terminal purge",
                "fit_view_sha256": index["fit_factor_view_sha256"],
                "factor_library_snapshot_id": index["library_snapshot_id"],
                "all_data_exposure": "2015-2024 previously exposed development; no reset of exposure at cycle boundary",
                "new_portfolio_proposals_max": 2, "new_factor_directions_allowed": False,
                "cumulative_portfolio_proposals_ceiling_after_this_cycle": 6,
                "capital": 1000000, "gross_exposure": 1., "max_stock_weight": .05,
                "schedule": "weekly last scheduled session close to next session open",
                "execution_eligibility": "same 120-session price and 20-session positive amount filter as initial stage",
                "cost_and_stress": "same AccountPolicy, doubled-slippage only, half participation, estimated terminal liquidation",
                "selection": "highest 2016-2020 mean full-year net Sharpe among candidates passing all fixed stress cases; lower turnover exact tie-break",
                "time_separation": "freeze a single winner before generating any 2021-2024 account result; run same continuous 2016-2024 account and compare saved fitting prefix",
                "independent_financial_validation": False}
    save_once(folder / "protocol.json", manifest)
    return folder, manifest


def design_combinations(root):
    root = Path(root)
    folder, manifest = initialize_cycle(root)
    fit = read(root / "fit_factor_view.json")
    if fit.get("2021_2024_result_values_included") is not False:
        raise ValueError("cycle does not authorize later-period values")
    rejection = read(root / "model_calls/02_combination_admission/admitted_receipt.json")["response"]
    prompt = """You are the actual QuantaAgents V6 strategy researcher, gpt-6-astra/xhigh. Return concise Chinese JSON.
The first study is terminal: your frozen A-D proposals were all rejected using factor evidence and zero candidate
accounts were run. Those four attempts and rejected directions remain in history. The user explicitly wants
factor testing FIRST, then combinations BASED ON the information factors provide. Our old first-call template
prematurely froze entire combinations before testing and recreated the old strategy-first bottleneck. This new
versioned cycle corrects that workflow while retaining every prior attempt; it does not rewrite their outcomes.

Use the supplied, already available 2016-2020 factor evidence to design at most TWO simple portfolios. No new
2021-2024 account results or 2025 numbers have been observed in this study. No sign-flipping of rejected factors,
window optimization, stock/year selectors, ad hoc cherry-picked exclusion or retrospective risk regimes.
Component weights must be strictly positive: the engine separately applies each factor's original registered
direction exactly once. You may omit rejected factors in these newly registered portfolios, explain the retained
information, and use a conditional/risk factor only with a falsifiable incremental role. Omission here is a NEW
recorded combination, not a repaired claim about A-D. Zero portfolios is allowed if evidence cannot support any.
F3 and F4 are explicitly forbidden as components in this cycle because their original mechanisms were rejected.

Available general portfolio controls: equal or inverse-20-day-volatility stock weighting; top_n in 20,40,60;
actual-holding ranking buffer=0 or 2*top_n; weekly last-session close signals, next opening execution; cash retained
for missing slots or capped weights. Long-only gross <=1, max stock weight .05. Optional market_filter none/trend60/
trend120 scales exposure to zero when the causally compounded equal-member-return market index is below its
past 60/120-session average. This is a new risk-rule trial if used, not a previously verified alpha. Optional
crowding_gate_factor_key F6 keeps the previously declared rising-5-day and highest-original-activity-quintile
half-weight gate; empty string means no gate. Do not vary many controls between the two candidates. Prefer a
simple information combination plus one controlled, economically reasoned application difference.

Underlying account is a tested adjusted-unit daily approximation, not certified live fills. All costs/constraints
and uncertainty remain. Preserve the separate protocol, fixed training selection and subsequent locked temporal
diagnostic. Full-period stress net return must exceed 2% annual risk-free growth AFTER estimated liquidation cost.
No claim of independence or success from fitted IC, residuals, new seed identity or the architecture itself.
State expected failure modes and how each change uses the measured information. Do not invent test outputs.
""" + "\nNEW CYCLE PROTOCOL\n" + dump(manifest) + "\nORIGINAL RESEARCHER REJECTIONS\n" + dump(rejection) + "\nFIT FACTOR EVIDENCE\n" + dump(fit)
    receipt = call_researcher(root, "03_evidence_led_combination_design", prompt, DESIGN_SCHEMA)
    proposals = receipt["response"]["portfolios"]
    if len(proposals) > 2:
        raise ValueError("new cycle exceeds its explicit two-proposal allocation")
    factors = {f["factor_key"]: f for f in fit["factors"] if f["status"] == "evaluated"}
    specs = []
    for i, p in enumerate(proposals):
        if p["top_n"] not in {20, 40, 60} or p["membership_buffer"] not in {0, 2 * p["top_n"]}:
            raise ValueError("new portfolio violates frozen application choices")
        if not p["components"] or len({c["factor_key"] for c in p["components"]}) != len(p["components"]):
            raise ValueError("explicit unique factor components required")
        weights = {}
        for component in p["components"]:
            key, weight = component["factor_key"], component["weight"]
            if key not in factors or type(weight) not in (int, float) or not 0 < weight <= 1:
                raise ValueError("registered factors with positive finite weights required; no direction reversal")
            if key in {"F3", "F4"}:
                raise ValueError("previously rejected directions cannot be silently reinstated in this cycle")
            weights[factors[key]["name"]] = weight * factors[key]["direction"]
        gate = p["crowding_gate_factor_key"]
        if gate not in {"", "F6"}:
            raise ValueError("only the original crowding gate or no gate is registered")
        spec = PortfolioSpec(name="R" + str(i + 1), factor_weights=weights,
              top_n=p["top_n"], max_stock_weight=.05, weighting=p["weighting"],
              market_filter=p["market_filter"], rebalance_schedule="weekly_last_session",
              membership_buffer=p["membership_buffer"],
              crowding_gate_factor=factors[gate]["name"] if gate else "",
              metadata={"model_name": p["name"], "hypothesis": p["hypothesis"],
                        "falsification": p["falsification"], "cycle_id": CYCLE,
                        "source_call": "03_evidence_led_combination_design"})
        specs.append(spec)
    declaration = {"specs": [asdict(s) for s in specs], "protocol_sha256": file_hash(folder / "protocol.json"),
                   "source_receipt_sha256": file_hash(root / "model_calls/03_evidence_led_combination_design/admitted_receipt.json"),
                   "implementation_sha256": file_hash(Path(__file__).with_name("portfolio.py")),
                   "prior_proposals_retained": 4, "new_proposals": len(specs),
                   "cumulative_proposals": 4 + len(specs), "no_account_results_used_for_this_design": True}
    save_once(folder / "combination_declaration.json", declaration)
    return declaration
