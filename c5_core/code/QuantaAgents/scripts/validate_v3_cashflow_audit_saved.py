"""Bounded offline audit checks on saved executions; never execute a strategy."""
import copy
import hashlib
import json
from pathlib import Path

from audit_raw_cashflows_v3 import cash_action_audit


def main():
    repo = Path(__file__).resolve().parents[1]
    root = repo / "experiment_traces/meta_framework_v3/year_research_001"
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    case = plan["tasks"]["year_reversal_001"]["case"]
    valid = []
    for f in sorted(root.glob("tools/*/*/artifact.json")):
        artifact = json.loads(f.read_text(encoding="utf-8"))
        raw = artifact.get("raw")
        if raw and raw.get("result"):
            r = raw["result"]
            audited = cash_action_audit(r, case)
            valid.append({"call_id":f.parent.name, "artifact_sha256":hashlib.sha256(f.read_bytes()).hexdigest(),
                          "days":len(audited["daily"]), "final_cash":audited["final_cash"]})
            if len(valid) == 1:
                original = r
    assert valid
    def event(result, kind):
        return next(e["event"] for e in result["journal"] if e["event"]["kind"] == kind)
    def set_event(kind, field, value):
        return lambda r: event(r, kind)["data"].__setitem__(field, value)
    changes = {
        "alter_dividend_credit":set_event("cash_dividend_paid", "net_cash_credit", "5118.00"),
        "alter_dividend_entitlement":set_event("record_entitlement", "entitled_shares", 30000),
        "remove_dividend_payment":lambda r:r["journal"].remove(next(e for e in r["journal"] if e["event"]["kind"]=="cash_dividend_paid")),
        "erase_receivable_day":lambda r:next(d for d in r["daily"] if d["cash_receivable_gross"]!="0.00").__setitem__("cash_receivable_gross", "0.00"),
        "erase_tax_paid":set_event("tax_paid", "amount", "0.00"),
        "erase_reserve_only":lambda r:next(d for d in r["daily"] if d["remaining_tax_reserve"]!="0.00").__setitem__("remaining_tax_reserve", "0.00"),
        "free_fee":lambda r:r["trades"][0]["fees"].__setitem__("total", "0.00"),
        "free_extra_capital":set_event("cash_deposit", "amount", "1000100.00"),
        "erase_loss_day":lambda r:r["daily"].pop(-2),
    }
    rejected = []
    for label, mutation in changes.items():
        changed = copy.deepcopy(original)
        mutation(changed)
        try:
            cash_action_audit(changed, case)
        except (AssertionError, KeyError):
            rejected.append(label)
        else:
            raise AssertionError("Failed to reject "+label)
    output = repo / "experiment_traces/meta_framework_v3/validation/cashflow_audit_001"
    output.mkdir(exist_ok=False)
    report = {"saved_actual_executions_passed":valid, "mutations_rejected":rejected,
              "new_gateway_calls":0, "new_strategy_executions":0, "original_artifacts_modified":False,
              "independent_tax_classification_certification":False,
              "audit_source_sha256":hashlib.sha256((repo/"scripts/audit_raw_cashflows_v3.py").read_bytes()).hexdigest()}
    (output/"saved_replay.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
