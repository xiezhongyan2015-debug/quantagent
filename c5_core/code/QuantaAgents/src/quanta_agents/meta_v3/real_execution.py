"""One recorded real saved-data candidate, full cash and fail-closed raw replay."""
from .kernel import module
from .ledger import digest, need
from . import real_program


def develop(folder, case, program, save):
    from .archive_codec import case_policy
    from .target_schedule import validate_case_policy, apply_schedule
    validate_case_policy(case)
    storage = case_policy(case)
    storage_args = {'archive_storage_policy': storage} if storage is not None else {}
    if case.get("execution_backend") == "v3_structural_001":
        from . import structural_execution as raw
    elif case.get("execution_backend") == "v3_streamed_001":
        from . import saved_execution as raw
    else:
        raw=module("raw_saved_research")
    from .conditional_risk_derivation import verify_case as verify_conditional_derivation
    derivation_check = verify_conditional_derivation(case)
    work=folder/"workbench"
    work.mkdir()
    if derivation_check is not None:
        save(work/"conditional_derivation_verification.json", derivation_check)
    save(work/"intent.json",{"case_hash":digest(case),"program_hash":digest(program),
        "requested_subattempts":["compile","target_generation","raw_mechanical_execution"],"research_class":"real_saved_development"})
    phases=[]; raw_result=None; error=None
    try:
        compiled=real_program.validate_program(program,public_field_contract=case["decision_fixture"]["fields"])
        save(work/"compiled.json",compiled);phases.append({"name":"compile","status":"completed"})
        targets=real_program.build_targets(compiled,decision_fixture=case["decision_fixture"],frozen_policy=real_program.POLICY)
        targets=apply_schedule(targets,case=case)
        save(work/"targets.json",targets);phases.append({"name":"target_generation","status":"completed"})
    except ValueError as e:
        error={"type":type(e).__name__,"message":str(e)}
    if error is None:
        need(all(x["status"] != "fixture_complete" for x in case["raw_source_bindings"]["obligations"]),"real source cannot use synthetic completeness")
        plan=raw.freeze_saved_research_plan(identity={"run_id":folder.parent.parent.parent.name,
            "architecture":"meta_v3_real_saved","observation_id":folder.name,"strategy_hash":digest(program),
            "data_hash":digest(case),"source_hash":digest(raw.engine_sources(**storage_args))},
            codes=case["decision_fixture"]["codes"],calendar=case["decision_fixture"]["calendar"],
            targets=targets["targets"],initial_cash=case["initial_cash"],fixture_only=False,
            **case["raw_source_bindings"], **storage_args)
        child=work/"raw_children/program"
        job=raw.SavedRawResearch.create(child,plan)
        raw_result=job.execute_saved(expected_plan_sha256=plan["plan_sha256"])
        need(raw_result["status"] in ("completed_mechanical","failed"),"real child unresolved")
        phases.append({"name":"raw_mechanical_execution","status":raw_result["status"]})
        error=raw_result["error"]
    result={"status":"failed" if error else "completed","error":error,"subattempts":phases,
        "execution_valid":False,"formal_target_success":False,"fixture_only":False}
    if derivation_check is not None:
        result.update(evidence_class='generated_engineering', real_research=False,
                      conditional_derivation_verification=derivation_check)
    save(work/"application.json",result)
    artifact = {"workbench":result,"raw":raw_result,"program":program,
            "audit":{"candidate_count":1,"requested_subattempt_count":3,"subattempts":phases,"research_class":"real_saved_development"}}
    if derivation_check is not None:
        artifact.update(evidence_class='generated_engineering', real_research=False)
        artifact['audit'].update(evidence_class='generated_engineering', real_research=False)
    return artifact
