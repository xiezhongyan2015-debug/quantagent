"""Freeze a full 2019 development case from saved rows and verified cash notice."""
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from quanta_agents.meta_v3.kernel import ROOT, module
from quanta_agents.meta_v3.ledger import Ledger, digest
from quanta_agents.meta_v3.closing import ClosingPolicy
from quanta_agents.meta_v3.research_tools import save_once
from quanta_agents.meta_v3.runtime import ResearchRuntime, source_pins
from quanta_agents.meta_v3 import real_program


def main():
    base = ROOT/"experiment_traces/meta_framework_v3"
    root = base/"year_inputs_001"
    root.mkdir(exist_ok=False)
    parent = json.loads((base/"real_inputs_001/case.json").read_text(encoding="utf-8"))
    artifact = next(a for a in parent["raw_source_bindings"]["source_artifacts"] if a["code"] == "sh600004")
    calendar_path = ROOT/"experiment_traces/meta_development_raw_coverage_v13/attempts/20260906T184719050829Z/plan.json"
    catalog = json.loads(calendar_path.read_text(encoding="utf-8"))
    days = [d for d in catalog["days"] if "2019-01-01" <= d <= "2019-12-31"]
    previous_day = catalog["days"][catalog["days"].index(days[0])-1]
    save_once(root/"preparation_plan.json", {"created_at":datetime.now(timezone.utc).isoformat(),
        "selection":"Original first code with verified whole-year cash notice and no-share-change review; no annual outcome inspected before freeze",
        "codes":["sh600004"],"calendar":days,"previous_reference_day":previous_day,"source_artifacts":[artifact],
        "maximum_rows_per_file":2000,"maximum_compressed_bytes":2097152,"maximum_decompressed_bytes":8388608,
        "maximum_source_manifests":1,"original_csv_reads":0,"model_calls":0,"strategy_trials":0,
        "calendar_plan_sha256":hashlib.sha256(calendar_path.read_bytes()).hexdigest(),"initial_cash":"1000000.00"})
    binary = (Path(artifact["root"])/artifact["rows_file"]).read_bytes()
    assert len(binary) <= 2097152 and hashlib.sha256(binary).hexdigest() == artifact["rows_sha256"]
    mraw = (Path(artifact["root"])/artifact["manifest_file"]).read_bytes()
    assert len(mraw) <= 262144 and hashlib.sha256(mraw).hexdigest() == artifact["manifest_sha256"]
    with gzip.GzipFile(fileobj=io.BytesIO(binary)) as stream:
        body = stream.read(8388609)
    assert len(body) <= 8388608
    all_rows = json.loads(body)
    assert len(all_rows) <= 2000 and [r["date"] for r in all_rows] == catalog["days"]
    selected = [r for r in all_rows if r["date"] in days]
    boundary = next(r for r in all_rows if r["date"] == previous_day)
    assert len(selected) == len(days) and all(r["code"] == "sh600004" for r in selected)
    references = []
    for previous, row in zip([boundary]+selected, selected):
        difference = Decimal(row["raw_price_text"]["raw_prev_close"]) - Decimal(previous["raw_price_text"]["raw_close"])
        expected = Decimal("-0.17") if row["date"] == "2019-08-09" else Decimal(0)
        references.append({"date":row["date"],"difference":str(difference),"expected_cash_difference":str(expected),
            "explained_within_cent":abs(difference-expected) <= Decimal("0.01")})
    save_once(root/"selected_rows.json", selected)
    save_once(root/"reference_audit.json", {"rows":references,"all_explained":all(r["explained_within_cent"] for r in references),
        "selected_rows_sha256":digest(selected),"accepted_counts":{str(v):sum(r.get("accepted") is v for r in selected) for v in (True,False)},
        "no_price_replacement":True,"formal_target_success":False})
    assert all(r["explained_within_cent"] for r in references), "new unexplained action/reference; keep failed preparation and investigate"
    receipt_path = base/"company_action_evidence_002/baiyun_implementation_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    notice = base/"company_action_evidence_002/baiyun_implementation.pdf"
    annual = base/"company_action_evidence_001/sh600004.pdf"
    assert hashlib.sha256(notice.read_bytes()).hexdigest() == receipt["sha256"] == "4296ecb245390903a59b5d945390959bb0fb623569c47214f83d642806ddf3bd"
    assert hashlib.sha256(annual.read_bytes()).hexdigest() == "7fac0eb9db9a149453e39ceeb5e47d3e43ea00798c20511fd0148e7351b47d1d"
    adapter = module("corporate_action_adapter")
    action = adapter.CashDividendAnnouncement(action_id="sh600004-2019-015",symbol="sh600004",
        announcement_date="2019-08-03",record_date="2019-08-08",ex_date="2019-08-09",cash_payment_date="2019-08-09",
        gross_cash_per_share="0.17",short_holding_tax_rate="0.20",tax_rule=adapter.SUPPORTED_TAX_RULE,
        account_type=adapter.ACCOUNT_TYPE,stock_distribution_per_share="0",stock_distribution_kind="none",rights_issue=False,
        implementation_status="implementation_notice",source_url=receipt["url"],source_sha256=receipt["sha256"],
        source_fetched_at=receipt["fetched_at"],facts_verified=True)
    manifest = adapter.CashDividendAdapter(action,days,rounding_policy="aggregate_half_up_simulated").manifest()
    save_once(root/"cash_action_manifest.json", manifest)
    review = ROOT/"docs/research/meta_framework_v3_handoff/baiyun_2019_scope_admission_001.md"
    paths = [review,root/"preparation_plan.json",root/"selected_rows.json",root/"reference_audit.json",
             root/"cash_action_manifest.json",notice,annual,receipt_path,calendar_path]
    proofs = [{"path":str(p),"sha256":hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]
    evidence = digest(proofs)
    cells, eligibility = [], []
    for row in selected:
        day = row["date"]
        metadata = {"session":day,"symbol":"sh600004","effective_at":day+"T15:05:00+08:00",
            "available_at":day+"T15:05:00+08:00","source_evidence_id":evidence}
        for field,key in (("close","raw_close"),("volume","volume"),("amount","amount")):
            cells.append({**metadata,"field":field,"value":row[key]})
        eligibility.append({**metadata,"eligible":True})
    notes = {r["kind"]:r["note"] for r in parent["raw_source_bindings"]["obligations"]}
    notes["corporate_actions"] = "Whole 2019 issuer annual no-share-change and implemented cash reconciliation; verified notice 2019-015 is included. Any registered holdings retain cash entitlement/tax after disposal. Expost review, not historical arrival certification."
    notes["membership"] = "Fixed first source-reviewed stock for full-year accounting development, not historical index membership. All saved dates/quality and membership flags retained."
    notes["market_status"] = "Declared prior-name/ordinary-board status and reference limits; full-year raw reference audit keeps every date and explains cash ex-date without adjusted-price replacement. Missing/invalid held prices stop NAV."
    obligations = [{"code":"sh600004","date":d,"kind":k,"status":"documented_scope" if k in ("corporate_actions","membership") else "declared_simulation",
        "evidence_sha256":evidence,"note":note} for d in days for k,note in notes.items()]
    case = {"research_class":"real_saved_development","execution_backend":"v3_streamed_001",
        "description":"Full 2019 development on original first source-reviewed stock sh600004. All supplied sessions retained; CNY1,000,000 continuous account and an explicit cash dividend/tax event. Fixed single-stock development, not independent OOS or a cross-case success. Annual corporate-action review is ex-post accounting evidence only.",
        "initial_cash":"1000000.00","decision_fixture":{"kind":"exposed_real_decision_table","codes":["sh600004"],"calendar":days,
            "fields":[{"name":"close","unit":"CNY raw close"},{"name":"volume","unit":"shares"},{"name":"amount","unit":"CNY turnover"}],
            "field_rows":cells,"eligibility_rows":eligibility},
        "raw_source_bindings":{"source_artifacts":[artifact],"obligations":obligations,"corporate_actions":[asdict(action)]},
        "evidence_sources":proofs,"execution_valid":False,"formal_target_success":False}
    real_program.matrices(case["decision_fixture"])
    save_once(root/"case.json",case)
    research_root = base/"year_research_001"
    deadline = json.loads((base/"real_research_002/plan.json").read_text(encoding="utf-8"))["deadline_epoch"]
    protocol_path = ROOT/"docs/research/meta_framework_v3_handoff/year_research_manifest_001.json"
    protocol = {"created_at":datetime.now(timezone.utc).isoformat(),"kind":"full_2019_declared_execution_development",
        "ordinary_input":"研究股票经历下跌后出现企稳迹象时，是否存在覆盖成本的后续收益。自主取证、开发仓位与退出规则、核查失败和成本；使用完整资金与公司行动账本交付结论，证据不足可以弃权。",
        "scope":"2019 entire supplied market calendar, one preselected source-reviewed stock; exposed development, not formal validation",
        "case_hash":digest(case),"case_path":str(root/"case.json"),"calendar_sessions":len(days),"initial_cash":"1000000.00",
        "model":"gpt-6-astra","effort":"xhigh","max_model_calls":12,"nominal_tokens":600000,"maximum_candidates":3,
        "deadline_epoch":deadline,"old_v3_known_tokens":707254,"old_v2_known_tokens":491954,"old_v2_unknown_reserve":80000,
        "stops":["one actual final or delivery failure","unknown cost/child state blocks without paid retry","source drift, deadline or admission budget"],
        "formal_success_denominator":0,"original_small_plan_192_pending_unchanged":True,"old_campaign_resume_authorized":False}
    save_once(protocol_path, protocol)
    Ledger.create(research_root,policy=ClosingPolicy(task_calls=12,stage_calls=12,stage_tokens=600000),
        tasks={"year_reversal_001":{"idea":protocol["ordinary_input"],"documents":[],"case":case,"case_hash":digest(case)}},
        deadline_epoch=deadline,provenance={"source_pins":source_pins(),"preregistered_protocol":str(protocol_path),
            "preregistered_protocol_sha256":hashlib.sha256(protocol_path.read_bytes()).hexdigest(),"old_v3_known_tokens":707254,
            "old_v2_known_tokens":491954,"old_v2_unknown_reserve":80000,"old_campaign_resume_authorized":False,
            "exposure":"Full-year single-stock exposed development; not an OOS case or independent architecture comparison"})
    ResearchRuntime(research_root).verify_inputs()
    print(json.dumps({"root":str(research_root),"sessions":len(days),"fields":len(cells),"obligations":len(obligations),
        "reference_anomalies":sum(not r["explained_within_cent"] for r in references),"cash_action":manifest["cash_available_at"],"model_calls":0}))


if __name__ == "__main__":
    main()
