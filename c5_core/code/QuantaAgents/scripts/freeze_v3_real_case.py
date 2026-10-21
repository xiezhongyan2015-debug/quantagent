"""Bind reviewed scope to saved real inputs; no prices fetched or trial run."""
from pathlib import Path
import json,hashlib,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from quanta_agents.meta_v3.ledger import digest
from quanta_agents.meta_v3.research_tools import save_once
from quanta_agents.meta_v3 import real_program


def freeze():
    repo=Path(__file__).resolve().parents[1]
    root=repo/"experiment_traces/meta_framework_v3/real_inputs_001"
    prep=json.loads((root/"plan.json").read_text(encoding="utf-8"))
    rows=json.loads((root/"selected_rows.json").read_text(encoding="utf-8"))
    ref=json.loads((root/"reference_audit.json").read_text(encoding="utf-8"))
    assert digest(rows)==ref["selected_rows_sha256"] and ref["all_references_explained_within_cent"]
    paths=[repo/"docs/research/meta_framework_v3_handoff/real_scope_admission_001.md",
        root/"plan.json",root/"selected_rows.json",root/"reference_audit.json",
        repo/"experiment_traces/meta_framework_v3/company_action_evidence_001/sh600004.pdf",
        repo/"experiment_traces/meta_framework_v3/company_action_evidence_001/sh600006.pdf",
        repo/"experiment_traces/meta_framework_v3/company_action_evidence_002/baiyun_implementation.pdf",
        repo/"experiment_traces/meta_raw_matrix_corporate_probe_v15/query_1_result.json",
        repo/"experiment_traces/meta_raw_matrix_corporate_probe_v15/query_2_result.json"]
    proofs=[{"path":str(p),"sha256":hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]
    source=digest(proofs); selected=[r for r in rows if r["date"] in prep["calendar"]]
    fields=[{"name":"close","unit":"CNY raw close"},{"name":"volume","unit":"shares"},{"name":"amount","unit":"CNY turnover"}]
    cells=[]; eligibility=[]
    for r in selected:
        d,c=r["date"],r["code"]
        for name,value in (("close",r["raw_close"]),("volume",r["volume"]),("amount",r["amount"])):
            cells.append({"session":d,"symbol":c,"field":name,"value":value,"effective_at":d+"T15:05:00+08:00",
                "available_at":d+"T15:05:00+08:00","source_evidence_id":source})
        eligibility.append({"session":d,"symbol":c,"eligible":True,"effective_at":d+"T15:05:00+08:00",
            "available_at":d+"T15:05:00+08:00","source_evidence_id":source})
    notes={"corporate_actions":"Scoped account rights review: issuer 2019 annual no share-capital change, cash distributions accounted for; zero initial inventory precedes first buy, 600006 prior record no entitlement; 600004 record after end. Does not certify full announcement history.",
        "membership":"Fixed original two-stock research pool, not historical CSI500 eligibility; original membership and rejected rows preserved.",
        "availability":"Nominal complete bars 15:05, decisions 15:10, next-session opening; historical actual arrival unverified; no late backfill.",
        "fee_policy":"Declared full fee schedule: commission max(5,.0003*gross), transfer .00002 both sides, sell stamp .001 in2019; component cents; adverse .001 slippage in fill price. Real account contract unknown.",
        "market_status":"Declared prior-name ordinary board/ST limit proxy and positive raw reference; exact selected reference audit explains sole ex-cash date within one cent; invalid/missing inputs reject.",
        "capacity":"Declared prior-session volume*5% estimate, not executable lower bound; round lots/T+1/partial fills/cash with fees. No redistribution of failed targets."}
    obligations=[{"code":c,"date":d,"kind":k,"status":"documented_scope" if k in ("corporate_actions","membership") else "declared_simulation",
        "evidence_sha256":source,"note":note} for c in prep["codes"] for d in prep["calendar"] for k,note in notes.items()]
    case={"research_class":"real_saved_development","description":"Original fixed two real A-share stocks, 16 exposed development sessions. Full initial cash CNY1,000,000. Daily activity/price research; execution uses explicit daily-bar simulation, not certified real fills. Issuer annual reports are accounting coverage only and not 2019 decision signals. No independent OOS inference.",
        "initial_cash":prep["original_capital"],"decision_fixture":{"kind":"exposed_real_decision_table","codes":prep["codes"],"calendar":prep["calendar"],
            "fields":fields,"field_rows":cells,"eligibility_rows":eligibility},
        "raw_source_bindings":{"source_artifacts":prep["source_artifacts"],"obligations":obligations,"corporate_actions":[]},
        "evidence_sources":proofs,"scope_evidence_hash":source,"historical_data_available_at_verified":False,
        "company_action_scope":"account rights in fixed 16-day window only; issuer original 600006 implementation unverified",
        "execution_valid":False,"formal_target_success":False}
    real_program.matrices(case["decision_fixture"])
    save_once(root/"case.json",case)
    print(json.dumps({"case_hash":digest(case),"rows":len(selected),"field_cells":len(cells),"obligations":len(obligations),"initial_cash":case["initial_cash"]}))


if __name__=="__main__":freeze()
