"""Shared single-program mechanics for ordinary and batch research actions."""
import hashlib
import json

from .kernel import module
from .ledger import need


def develop(folder,case,program,save):
    if case.get('execution_backend')=='v3_l2_cash_001':
        from . import l2_saved_execution
        return l2_saved_execution.develop(folder,case,program,save)
    if case['research_class']=='real_saved_development':
        from .real_execution import develop as real_develop
        return real_develop(folder,case,program,save)
    development=module('meta.strategy_development');raw=module('raw_saved_research')
    plan=development.freeze_scope(identity={'run_id':folder.parent.name,'arm':'v3_shared',
        'task_run_id':folder.parent.parent.name,'scope_id':folder.name},
        decision_fixture=case['decision_fixture'],raw_source_bindings=case['raw_source_bindings'],initial_cash=case['initial_cash'])
    wb=development.StrategyDevelopmentWorkbench.create(folder/'workbench',frozen_plan=plan,
        controller_cursor_key=hashlib.sha256(str(folder.resolve()).encode()).digest())
    result=wb.execute('program',{'action':'develop_strategy','program':program},expected_state_sha256=wb.summary()['state_sha256'])
    child=folder/'workbench/raw_children/program';raw_result=None
    if child.exists():
        raw_plan=json.loads((child/'plan.json').read_text(encoding='utf-8'))
        raw_result=raw.SavedRawResearch(child).reconcile_saved_only(expected_plan_sha256=raw_plan['plan_sha256'])
        need(raw_result['status'] in ('completed_mechanical','failed'),'raw execution unresolved')
    need(result['status'] in ('completed','failed'),'strategy application unresolved')
    return {'workbench':result,'raw':raw_result,'program':program,'audit':wb.export_for_audit()}
