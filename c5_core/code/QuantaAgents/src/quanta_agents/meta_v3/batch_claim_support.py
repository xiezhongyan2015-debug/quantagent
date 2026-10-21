"""Read-only custody checks for saved batch scalars, not account re-execution.

The caller supplies artifacts verified against its settled ledger, plus the
frozen case. No files, recovery paths, models or strategy workers are invoked.
"""
from decimal import localcontext

from .ledger import digest, need
from .research_iteration import executable_program_hash, public_diagnosis


def _artifact(saved, action):
    need(type(saved) is dict and saved.get('action') == action, 'saved action binding')
    value = saved.get('_registered_artifact')
    need(type(value) is dict and digest(value) == saved.get('artifact_hash'), 'saved artifact binding')
    return value


def _calendar(case):
    need(type(case) is dict and case.get('research_class') in
         ('real_saved_development', 'synthetic_calibration'), 'frozen development case required')
    days = case['decision_fixture']['calendar']
    need(type(days) is list and days and all(type(d) is str for d in days)
         and days == sorted(set(days)), 'frozen full calendar required')
    return days


def _complete(account, case):
    from .claim_support import _number
    days = _calendar(case)
    need(type(account) is dict and account.get('complete_account') is True
         and account.get('status') == 'completed_mechanical'
         and account.get('statistics_scope') == 'complete saved calendar', 'complete saved account required')
    need(type(account.get('saved_cash_days')) is int and account['saved_cash_days'] == len(days)
         and account.get('last_complete_date') == days[-1], 'full frozen cash calendar required')
    need(type(account.get('unvalued_trade_count')) is int and account['unvalued_trade_count'] == 0,
         'unvalued trades prevent complete metric support')
    need(_number(account['initial_cash']) == _number(case['initial_cash']) > 0, 'full initial capital required')


def bound_unit(saved, evidence, path, frozen_case):
    """Recognize only an originally delivered results-page account scalar."""
    from .claim_support import ACCOUNT_UNITS, _number
    if (len(path) != 4 or path[0] != 'rows' or type(path[1]) is not int
            or path[1] < 0 or path[2] != 'account' or path[3] not in ACCOUNT_UNITS):
        return None
    days = _calendar(frozen_case)
    page = _artifact(saved, 'inspect_batch')
    request = saved.get('_registered_arguments')
    need(type(request) is dict and set(request) ==
         {'registration_evidence_id', 'candidate_id', 'table', 'offset', 'limit'}
         and request['candidate_id'] is None and request['table'] == 'results', 'original results request required')
    public = saved['public']
    need(not public.get('delivery_blocked') and digest({k: public.get(k) for k in page}) == digest(page),
         'delivered page differs from saved artifact')
    rows, offset, total = page['rows'], page['offset'], page['total_rows']
    need(type(rows) is list and type(offset) is int and type(total) is int
         and 0 <= offset <= total and 0 < len(rows) <= 32 and offset + len(rows) <= total
         and type(public.get('returned_rows')) is int and public['returned_rows'] == len(rows)
         and type(request['offset']) is int and request['offset'] == offset
         and type(request['limit']) is int and 1 <= request['limit'] <= 32
         and len(rows) <= request['limit'] and public.get('requested_limit') == request['limit']
         and (page['next_offset'] is None or type(page['next_offset']) is int)
         and page['next_offset'] == (offset + len(rows) if offset + len(rows) < total else None),
         'whole contiguous original page required')
    batch_id = page['registration_evidence_id']
    need(request['registration_evidence_id'] == batch_id and batch_id in evidence, 'original registration must be cited')
    registration = _artifact(evidence[batch_id], 'register_batch')
    need(registration.get('kind') == 'frozen_research_batch_v1'
         and registration.get('case_hash') == digest(frozen_case)
         and registration.get('data_and_cost_contract_hash') == digest(frozen_case)
         and _number(registration['initial_cash']) == _number(frozen_case['initial_cash']), 'batch frozen case binding')
    executions = []
    for item in evidence.values():
        if type(item) is dict and item.get('action') == 'execute_batch':
            value = _artifact(item, 'execute_batch')
            if value.get('batch_id') == batch_id and digest(value.get('candidates')) == page['batch_results_hash']:
                executions.append(value)
    need(executions and all(x == executions[0] for x in executions), 'matching original execution must be cited')
    execution = executions[0]
    need(execution.get('kind') == 'batch_execution_evidence_v1'
         and execution['registration_hash'] == digest(registration), 'execution registration binding')
    all_rows, declared = execution['candidates'], registration['candidates']
    need(type(all_rows) is list and type(declared) is list and len(all_rows) == len(declared) == total
         and digest(rows) == digest(all_rows[offset:offset + len(rows)]), 'execution full row hash and page slice binding')
    identities = []
    for row, original in zip(all_rows, declared):
        cid = original['candidate_id']; identities.append(cid)
        expected_executable = (executable_program_hash(original['program'])
                               if original.get('validation_error') is None else None)
        need(type(cid) is str and row['candidate_id'] == cid
             and original['program_hash'] == digest(original['program'])
             and original['executable_program_hash'] == expected_executable
             and ('program_hash' not in row or row['program_hash'] == original['program_hash'])
             and ('executable_program_hash' not in row or row['executable_program_hash'] == original['executable_program_hash']),
             'ordered candidate and executable identity binding')
    need(len(identities) == len(set(identities)), 'unique original candidate identities required')
    row = rows[path[1]]
    need(row.get('status') == 'completed' and row.get('raw_status') == 'completed_mechanical'
         and declared[offset + path[1]].get('validation_error') is None,
         'partial failed unknown and reused accounts are not supported')
    account = row['account']; _complete(account, frozen_case)
    need(account.get('evidence_id') == row['candidate_id']
         and account.get('program_hash') == row['program_hash']
         and account.get('executable_program_hash') == row['executable_program_hash'], 'saved account candidate binding')
    scan = row.get('observed_scan_completion', {})
    need(scan.get('full_calendar_completed') is True and scan.get('raw_result_saved') is True
         and type(scan.get('valued_calendar_sessions')) is int and scan['valued_calendar_sessions'] == len(days),
         'saved full calendar completion required')
    return ACCOUNT_UNITS[path[3]]


def bound_fee_difference_unit(saved, path, frozen_case):
    """Check comparison-minus-reference fees against two original full accounts."""
    from .claim_support import _number
    value = _artifact(saved, 'diagnose_execution')
    need(value.get('kind') == 'saved_execution_attribution_v1'
         and saved.get('public') == public_diagnosis(value), 'original diagnosis projection required')
    days = _calendar(frozen_case)
    pair = value['pairs'][path[1]]
    need(pair.get('same_cash_calendar_and_initial_capital') is True, 'aligned pair required')
    accounts = []
    for key in ('reference_evidence_id', 'comparison_evidence_id'):
        found = [a for a in value['accounts'] if a.get('evidence_id') == pair[key]]
        need(len(found) == 1 and pair[key] in value['input_evidence_ids'], 'unique original pair account required')
        account = found[0]; _complete(account, frozen_case)
        need([r['date'] for r in account['daily']] == days, 'original paired daily calendar required')
        accounts.append(account)
    need(pair['reference_evidence_id'] != pair['comparison_evidence_id'], 'distinct pair accounts required')
    with localcontext() as context:
        context.prec = 220
        difference = _number(accounts[1]['fees_on_recorded_trades']) - _number(accounts[0]['fees_on_recorded_trades'])
        need(_number(pair['comparison_minus_reference_fees']) == difference, 'original comparison fee subtraction mismatch')
    return 'CNY'
