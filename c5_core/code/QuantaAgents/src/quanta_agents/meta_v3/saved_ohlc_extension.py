"""Add explicitly requested OHLC fields from one already pinned 2019 slice.

This adapter authenticates a saved request, reads the selected slice only, and
writes an additive case plus derivation evidence. It grants no research budget,
loads no broader physical archive, and never executes a strategy or model.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from .ledger import Ledger, digest, need
from .research_tools import save_once
from .source_admission import preflight_case, substantive_change


VERSION = 'saved_year_ohlc_extension_v1'
YEAR_AXES_HASH = '3c3d286ce25842725bfade02ae69a6307ef4651b3ebfdea604db74e41435ca5c'
SELECTED_ROWS_SHA256 = 'e2ae35c519f9b3db17d00ff7263e2d9e8e6354a7bbdef570c0db4d8cf8a7c9c0'
ORIGINAL_FIELDS = [{'name': 'close', 'unit': 'CNY raw close'},
                   {'name': 'volume', 'unit': 'shares'}, {'name': 'amount', 'unit': 'CNY turnover'}]
MAPPING = {'open': 'raw_open', 'high': 'raw_high', 'low': 'raw_low'}
MAX_SELECTED_BYTES = 2 * 1024**2


def _parent(parent_root, task_id, request_id):
    # Shares authentic completion/artifact binding with the admission route;
    # it neither creates an extension decision nor changes the parent ledger.
    from .research_extension import _parent_request
    return _parent_request(Ledger(parent_root), task_id, request_id)


def prepare(parent_root, task_id, request_id, output_dir):
    """Return a prepared child and the *new manifest only* source admission.

    The controller must supply admissions for all preserved parent source paths
    before invoking extension admission. Their physical date ranges are not
    guessed from the selected 2019 decision calendar. An existing output is
    refused, including an incomplete previous preparation, without replacement.
    """
    output = Path(output_dir).resolve()
    need(not output.exists(), 'OHLC output already exists; inspect saved preparation, no replacement')
    plan, task, request, parent_proofs, _ = _parent(parent_root, task_id, request_id)
    parent = task['case']
    need(digest(parent) == task['case_hash'], 'parent case identity changed')
    declaration = request['request']
    requested = declaration.get('requested_fields')
    need(declaration.get('request_kind') == 'data' and type(requested) is list
         and requested and all(type(name) is str for name in requested)
         and len(set(requested)) == len(requested) and set(requested) <= set(MAPPING),
         'saved request must ask for a nonempty distinct subset of open/high/low')
    fixture = parent['decision_fixture']
    need(parent.get('research_class') == 'real_saved_development'
         and parent.get('execution_backend') == 'v3_streamed_001', 'year development execution scope required')
    need(digest({key: fixture[key] for key in ('codes', 'calendar')}) == YEAR_AXES_HASH,
         'only the original year_inputs_001 244-session sh600004 axes are supported')
    need(fixture['fields'] == ORIGINAL_FIELDS, 'only the original close/volume/amount contract is supported')
    preflight_case(parent)
    proofs = [proof for proof in parent.get('evidence_sources', [])
              if Path(proof['path']).name == 'selected_rows.json'
              and Path(proof['path']).parent.name == 'year_inputs_001']
    need(len(proofs) == 1 and proofs[0]['sha256'] == SELECTED_ROWS_SHA256,
         'exact pinned year_inputs_001 selected_rows source required')
    source = Path(proofs[0]['path']).resolve()
    need(source.is_file() and source.stat().st_size <= MAX_SELECTED_BYTES, 'bounded selected source required')
    content = source.read_bytes()
    need(hashlib.sha256(content).hexdigest() == proofs[0]['sha256'], 'selected source hash drift')
    selected = json.loads(content)
    days, codes = fixture['calendar'], fixture['codes']
    expected = [(day, code) for day in days for code in codes]
    need(type(selected) is list and len(selected) == len(expected)
         and [(row['date'], row['code']) for row in selected] == expected,
         'selected source must retain the exact ordered parent grid')
    original_rows = {(row['session'], row['symbol'], row['field']): row for row in fixture['field_rows']}
    child = deepcopy(parent)
    additions, checks = [], []
    for row in selected:
        day, code = row['date'], row['code']
        close = original_rows[(day, code, 'close')]
        nominal = day + 'T15:05:00+08:00'
        need(close['effective_at'] == nominal and close['available_at'] == nominal,
             'original nominal close timestamps changed')
        for name, source_name in (('close', 'raw_close'), ('volume', 'volume'), ('amount', 'amount')):
            need(original_rows[(day, code, name)]['value'] == row[source_name],
                 'original decision values do not match the pinned selected source')
        for name in requested:
            source_name = MAPPING[name]
            value = row.get(source_name)
            need(type(value) in (int, float) and math.isfinite(value) and value > 0,
                 'requested selected price is absent or invalid; no imputation')
            addition = {**close, 'field': name, 'value': value, 'source_evidence_id': proofs[0]['sha256']}
            additions.append(addition)
            checks.append({'session': day, 'symbol': code, 'field': name, 'source_field': source_name,
                           'source_value': value, 'delivered_value': addition['value'],
                           'equal': addition['value'] == row[source_name],
                           'effective_at': addition['effective_at'], 'available_at': addition['available_at']})
    added_fields = [{'name': name, 'unit': 'CNY raw ' + name} for name in requested]
    child['decision_fixture']['fields'].extend(added_fields)
    child['decision_fixture']['field_rows'].extend(additions)
    batch_update = None
    if 'batch_policy' in parent:
        # The copied batch policy must cover the new field contract as well.
        # Source-byte identity cannot recover raw precision lost before JSON;
        # it does not authorize a tick-size or digit-feature interpretation.
        added_semantics = {field['name']: {'unit': field['unit'], 'raw_precision': 'unknown',
                                         'digit_derivation': None} for field in added_fields}
        child['batch_policy']['field_semantics'].update(added_semantics)
        batch_update = {
            'added_field_semantics': added_semantics,
            'parent_batch_policy_hash': digest(parent['batch_policy']),
            'child_batch_policy_hash': digest(child['batch_policy']),
            'preserved_original_semantics_hash': digest(parent['batch_policy']['field_semantics']),
            'budget_fields_unchanged': True,
            'precision_evidence': 'Values are copied from the pinned selected JSON; original raw precision and minimum price increment are not established. No rounding or digit derivation is inferred.'}
    preflight_case(child)
    material = substantive_change(parent, child, 'data', requested)
    manifest = {'kind': VERSION, 'parent_root': str(Path(parent_root).resolve()),
        'parent_task_id': task_id, 'request_id': request_id, 'parent_plan_hash': digest(plan),
        'parent_case_hash': task['case_hash'], 'request_hash': digest(request),
        'parent_request_evidence': parent_proofs,
        'selected_source': {'path': str(source), 'sha256': proofs[0]['sha256'], 'bytes': len(content)},
        'requested_fields': requested, 'field_mapping': {name: MAPPING[name] for name in requested},
        'codes': codes, 'calendar': days, 'same_axes': True, 'new_sessions': 0, 'new_symbols': 0,
        'preserved_original_rows_hash': digest(fixture['field_rows']),
        'added_rows_hash': digest(additions), 'added_rows': len(additions),
        'child_decision_table_hash': digest(child['decision_fixture']),
        'substantive_addition': material, 'value_checks': checks, 'every_value_matches': all(row['equal'] for row in checks),
        'timing': 'Nominal same-session 15:05 effective/available timestamps copied from the original close rows. Historical actual publication/arrival remains unverified; no backfill or intraday availability claim.',
        'unchanged': 'All prior rows, eligibility, capital, execution backend, source bindings and research policies are preserved. Only the requested field contracts/rows and this evidence source are appended.',
        'exposure': 'Previously saved exposed 2019 development prices; no new independent market observations or OOS evidence.',
        'broader_raw_archives_read': False, 'all_child_sources_reverified': False,
        'downloads': 0, 'model_calls': 0, 'strategy_executions': 0, 'formal_target_success': False}
    if batch_update is not None:
        manifest['batch_field_semantics_update'] = batch_update
        manifest['unchanged'] = 'All prior rows, eligibility, capital, execution backend, source bindings, research action limits, batch budgets and original field semantics are preserved. Only the requested field contracts/rows, their appended batch field semantics and this evidence source are added.'
    output.mkdir(parents=True, exist_ok=False)
    manifest_path = output / 'derivation_manifest.json'
    save_once(manifest_path, manifest)
    proof = {'path': str(manifest_path), 'sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest()}
    child['evidence_sources'].append(proof)
    case_path = output / 'child_case.json'
    save_once(case_path, child)
    return {'child_case': child, 'child_case_path': str(case_path),
        'derivation_manifest_path': str(manifest_path), 'derivation_proof': proof,
        'source_admissions': [{'path': str(manifest_path), 'role': 'development_market_data',
            'partition': 'exposed_2017_2021', 'content_date_range': [days[0], days[-1]]}],
        'source_admissions_complete': False,
        'remaining_admission': 'The controller must retain or construct metadata admissions for every preserved parent path; physical raw market files span 2017-2021, not only the selected 2019 calendar.'}
