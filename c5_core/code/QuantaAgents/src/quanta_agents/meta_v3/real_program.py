"""Causal decisions on explicitly real saved inputs; never label them synthetic.

Expression validation is reused from revision 18. Timing, target generation and
source class are explicit here; execution still belongs to the raw-share kernel.
"""
from datetime import date, datetime
from decimal import Decimal
import math
import re
import pandas as pd
from .kernel import module
from .ledger import digest, need

base = module("meta.factor_strategy_program")
validate_program = base.validate_program
POLICY = dict(base.POLICY)


def fixture_axes(table):
    need(set(table) == {"kind", "codes", "calendar", "fields", "field_rows", "eligibility_rows"}, "exact real input fields")
    need(table["kind"] in ("exposed_real_decision_table", "exposed_l2_decision_table"), "real input identity required")
    codes, days = table["codes"], table["calendar"]
    need(type(codes) is list and 1 <= len(codes) <= 16 and len(set(codes)) == len(codes), "real symbol bound")
    need(all(type(c) is str and re.fullmatch(r"(?:sh[69]|sz[023])\d{5}", c) for c in codes), "symbol identity")
    need(type(days) is list and 3 <= len(days) <= 512 and days == sorted(set(days)), "real session bound")
    if table['kind']=='exposed_l2_decision_table':
        # A separately declared post-seal development source class. Never opens
        # the reserved 2024-2025 market data or labels 2026 as unseen OOS.
        need(all(type(d) is str and '2026-01-01' <= d <= date.today().isoformat() and date.fromisoformat(d).isoformat()==d for d in days), 'L2 exposed development dates')
    else:
        need(all(type(d) is str and "2017-01-01" <= d <= "2021-12-31" and date.fromisoformat(d).isoformat() == d for d in days), "real development dates")
    return codes, days, base.field_names(table["fields"])


def matrices(table):
    codes, days, names = fixture_axes(table)
    fields = {n: pd.DataFrame(float("nan"), index=days, columns=codes) for n in names}
    eligible = pd.DataFrame(False, index=days, columns=codes)
    masks = []
    for kind in ("field", "eligibility"):
        rows = table[kind + "_rows"]
        expected = {(d,c,n) for d in days for c in codes for n in names} if kind == "field" else {(d,c) for d in days for c in codes}
        need(type(rows) is list and len(rows) == len(expected), "complete real input grid")
        for r in rows:
            extra = {"field", "value"} if kind == "field" else {"eligible"}
            need(set(r) == {"session","symbol","effective_at","available_at","source_evidence_id"} | extra, "input row metadata")
            key = (r["session"],r["symbol"],r["field"]) if kind == "field" else (r["session"],r["symbol"])
            need(key in expected, "duplicate or foreign real input"); expected.remove(key)
            need(type(r["source_evidence_id"]) is str and re.fullmatch("[a-f0-9]{64}",r["source_evidence_id"]), "input evidence identity")
            effective, available = base._time(r["effective_at"]), base._time(r["available_at"])
            need(effective <= available, "arrival precedes effective time")
            late = available > base._time(r["session"] + "T" + POLICY["decision_clock"])
            if kind == "field":
                v=r["value"]
                need(v is None or type(v) in (int,float), "real numeric nullable field")
                valid = v is not None and math.isfinite(v)
                if valid and not late: fields[r["field"]].loc[key[:2]]=float(v)
            else:
                need(type(r["eligible"]) is bool, "eligibility must be explicit boolean")
                valid=True
                eligible.loc[key]=r["eligible"] and not late
            if late or not valid: masks.append({"coordinate":list(key),"reason":"after_decision_cutoff" if late else "missing_or_nonfinite"})
    return fields, eligible, masks


def build_targets(compiled, *, decision_fixture, frozen_policy):
    need(frozen_policy == POLICY, "real target policy drift")
    table=decision_fixture
    need(validate_program(compiled["spec"],public_field_contract=table["fields"]) == compiled,"compiled program drift")
    fields,eligible,masks=matrices(table)
    algebra=module("meta.factor_algebra")
    for f in compiled["spec"]["factors"]:
        fields[f["name"]]=algebra.evaluate_expression(f["expression"],fields,rank_universe=eligible)
    weights=algebra.evaluate_expression(compiled["spec"]["target_weight_expression"],fields,rank_universe=eligible)
    targets,decisions,untraded=[],[],[]
    days,codes=table["calendar"],table["codes"]
    for i,day in enumerate(days):
        total=Decimal(0); group=[]
        for code in codes:
            value=float(weights.loc[day,code]); finite=math.isfinite(value)
            need(not finite or 0 <= value <= 1,"finite real weight outside [0,1]")
            reasons=[]
            if not finite:reasons.append("missing_or_nonfinite_weight")
            if not bool(eligible.loc[day,code]):reasons.append("ineligible_at_signal")
            weight=Decimal(str(value)) if not reasons else Decimal(0)
            total+=weight
            group.append((code,value if finite else None,weight,reasons))
        need(total <= 1,"real weights exceed full capital; no normalization")
        for code,value,weight,reasons in group:
            if i == len(days)-1:
                untraded.append({"signal_date":day,"symbol":code,"weight_raw":value,"reason":"no_next_session_no_target"});continue
            if i == len(days)-2:
                weight=Decimal(0);reasons.append("frozen_terminal_zero")
            row={"symbol":code,"signal_date":day,"trade_date":days[i+1],"available_at":day+"T"+POLICY["decision_clock"],"target_weight":format(weight,"f")}
            targets.append(row);decisions.append({**row,"weight_raw":value,"reasons":reasons})
    return {"kind":"real_saved_strategy_target_artifact","program_hash":compiled["program_hash"],
        "decision_table_hash":digest(table),"policy":POLICY,"targets":targets,"decisions":decisions,
        "masked_inputs":masks,"untraded_terminal_signals":untraded,"execution_valid":False,"formal_target_success":False}
