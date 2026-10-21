"""One retained postprocessing of one fixed, already-exposed lineage report.

Reads only the three pinned prior JSON artifacts plus implementation source pins.
Never opens CSV/gzip/PDF, old audit backends, study databases or trading accounts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

VERSION = 'saved_asof_return_postprocessing_v1'
SUBJECT = 'saved_return_lineage_001/sh600004/2019/point_in_time_postprocessing_v1'
PRIOR_DIRECTORY = ROOT / 'validation/saved_return_lineage_001'
PINNED_INPUTS = {
    'intent.json': 'c24f86bb5f7d2ef87a6c8afa19ede037ba22279718f66574f88596ae96381ff3',
    'report.json': '88bf2c79ca2f67de831d860acc86d4aad6c6ed31478bae9f0141e094fbdcd1ea',
    'receipt.json': '363d24e32ee1b11cd5a4c56d5405dfff095881d778cc8476f7749b4675ddfa72',
}
MAX_INPUT_BYTES = 2 * 1024**2
MAX_OUTPUT_BYTES = 64 * 1024**2
ORIGINAL_SUBJECT = 'year_inputs_001/sh600004/2019/return_lineage_v1'


def need(value, message):
    if not value:
        raise ValueError(message)


def serial(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(serial(value).encode('utf-8')).hexdigest()


def code_pins():
    # Pin every current package Python source, the existing execution source
    # inventory, and this script. This is not third-party runtime authentication
    # and does not instantiate or dispatch any old backend.
    from quanta_agents.meta_v3.runtime import source_pins
    result = dict(source_pins())
    for path in sorted((ROOT / 'src/quanta_agents').rglob('*.py')):
        result[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    result[Path(__file__).relative_to(ROOT).as_posix()] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return result


def save_once(path, body):
    text = serial(body) + '\n'
    need(len(text.encode('utf-8')) <= MAX_OUTPUT_BYTES, 'bounded postprocessing artifact required')
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def read_saved_inputs():
    result = {}
    for name, expected in PINNED_INPUTS.items():
        path = PRIOR_DIRECTORY / name
        need(path.resolve() == path.absolute() and not path.is_symlink(), 'fixed prior artifact cannot be redirected')
        need(path.is_file() and path.stat().st_size <= MAX_INPUT_BYTES, 'bounded fixed prior artifact required')
        with path.open('rb') as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
        need(len(raw) <= MAX_INPUT_BYTES and hashlib.sha256(raw).hexdigest() == expected, 'fixed prior artifact drift: ' + name)
        result[name] = json.loads(raw.decode('utf-8'))
    intent, report, receipt = (result[name] for name in ('intent.json', 'report.json', 'receipt.json'))
    need(receipt['report_file_sha256'] == PINNED_INPUTS['report.json'] and receipt['report_sha256'] == digest(report),
         'prior report/receipt binding changed')
    need(intent['scope']['subject'] == receipt['subject'] == ORIGINAL_SUBJECT and report['scope'] == intent['scope'],
         'fixed original exposed scope changed')
    need(receipt['formal_target_success'] is False and receipt['strategy_executed'] is False and
         report['strategy_executed'] is False and report['account_executed'] is False,
         'prior retained input is not the fixed numerical-only audit')
    need(report['historical_arrival_verified'] is False and report['raw_price_origin_authenticated'] is False and
         report['complete_announcement_inventory_verified'] is False, 'prior provenance boundaries changed')
    need(report['normalized_inputs']['as_of'] == intent['as_of'], 'prior normalized-input cutoff changed')
    return result


def prepare_inputs(report):
    """Fixed data mapping only; no receipt or observed historical time is made up."""
    original = report['normalized_inputs']
    days = original['calendar']; symbols = original['symbols']; anchors = original['previous_anchors']
    need(symbols == ['sh600004'] and len(days) == 244 and days[0] == '2019-01-02' and days[-1] == '2019-12-31',
         'fixed original symbol/calendar changed')
    need(len(anchors) == 1 and anchors[0]['symbol'] == 'sh600004' and anchors[0]['session'] == '2018-12-28',
         'one explicit original prior-close anchor required')
    calendar = [anchors[0]['session']] + list(days)
    need(calendar == sorted(set(calendar)), 'original calendar cannot be reordered or compressed')
    prices = list(anchors) + list(original['prices'])
    need([(r['session'], r['symbol']) for r in prices] == [(d, 'sh600004') for d in calendar], 'complete fixed price grid required')
    price_versions = []
    for row in prices:
        price_versions.append({'version_id': 'price-' + row['symbol'] + '-' + row['session'].replace('-', ''),
            'symbol': row['symbol'], 'effective_at': None,
            'observed_arrival_at': None, 'arrival_basis': 'unverified', 'source_id': row['source_id'], 'evidence_id': None,
            'session': row['session'], 'close': row['close'], 'reference_previous_close': row['reference_previous_close']})
    action_versions = []
    for row in original['actions']:
        need(row['availability_basis'] in ('declared_simulated', 'unverified'), 'saved action cannot become observed arrival')
        action_versions.append({**{k: v for k, v in row.items() if k not in ('available_at', 'availability_basis')},
            'version_id': 'action-' + row['action_id'] + '-saved',
            'effective_at': None, 'observed_arrival_at': None,
            'arrival_basis': row['availability_basis'], 'evidence_id': None})
    original_coverage = {(r['session'], r['symbol']): r for r in original['coverage']}
    need(set(original_coverage) == {(d, 'sh600004') for d in days} and all(r['status'] == 'unknown' for r in original_coverage.values()),
         'fixed original action coverage must remain unknown')
    coverage_versions = []
    for day in calendar:
        row = original_coverage.get((day, 'sh600004'))
        # No pre-anchor coverage source was certified. The prior price source is
        # retained as the identity of this explicitly unknown placeholder only.
        source_id = row['source_id'] if row else anchors[0]['source_id']
        coverage_versions.append({'version_id': 'coverage-sh600004-' + day.replace('-', ''),
            'symbol': 'sh600004', 'session': day, 'effective_at': None,
            'observed_arrival_at': None, 'arrival_basis': 'unverified', 'source_id': source_id, 'evidence_id': None,
            'status': 'unknown', 'action_ids': sorted(a['action_id'] for a in original['actions'] if a['ex_date'] == day)})
    signals = list(days[::5])
    need(len(signals) == 49 and signals[12] == '2019-04-03', 'original opportunity anchors changed')
    return {'calendar': calendar, 'symbols': symbols, 'signal_dates': signals,
        'price_versions': price_versions, 'action_versions': action_versions, 'coverage_versions': coverage_versions,
        'evidence_receipts': [], 'evidence_class': 'caller_bound_exposed_development'}


def mapping_notes(report):
    original = report['normalized_inputs']
    contract = report['original_archive_manifest']['availability_contract']
    return {'historical_price_observed_arrival_at': None,
        'saved_archive_actual_available_at': contract['actual_available_at'],
        'saved_archive_actual_arrival_evidence': contract['actual_arrival_evidence'],
        'saved_bar_policy': contract['simulated_economic_use_policy'],
        'saved_action_time_declarations': [{'action_id': a['action_id'], 'declared_available_at': a['available_at'],
            'declared_basis': a['availability_basis'], 'announcement_date': a['announcement_date'],
            'record_date': a['record_date'], 'ex_date': a['ex_date'], 'payment_date': a['payment_date'],
            'observed_arrival_at': None} for a in original['actions']],
        'effective_time_policy': 'All version effective_at values remain null. Session, announcement, record and ex dates are preserved as domain dates, never invented publication or arrival times.',
        'missing_anchor_coverage': '2018-12-28 receives an explicit unknown-coverage placeholder referring to its saved price identity; this is not coverage evidence.',
        'evidence_receipts_created': 0, 'historical_arrival_authenticated': False,
        'opportunities': {'original_account_sessions': 244, 'prior_close_anchors': 1, 'original_five_session_anchors': 49,
            'first_twelve_anchors_lack_sixty_return_history': True, 'first_shape_complete_anchor': '2019-04-03',
            'shape_complete_anchors': 37, 'first_daily_shape_complete_date': '2019-04-02', 'daily_shape_complete_windows': 185},
        'history_scope': 'The first twelve short windows arise only from this fixed saved report carrying one prior close and 244 sessions. This does not establish missing warmup history in the original 2017-2021 archive or an external blocker. Additional archive history may be separately bound later; this postprocessing reads none of it.',
        'scope': 'Same exposed case, point-in-time postprocessing only. No new research attempt, strategy, account, case admission or source acquisition.'}


def run(output):
    wall_start, cpu_start = time.perf_counter(), time.process_time()
    output = Path(output).absolute()
    validation = (ROOT / 'validation').resolve()
    need(output.resolve() == output and output.is_relative_to(validation) and output != validation,
         'new output must be a non-redirected child of workspace validation')
    need(not output.exists(), 'retained output already exists; inspect it, do not rerun or overwrite')
    pins = code_pins()
    intent = {'version': VERSION, 'subject': SUBJECT, 'prepared_at': datetime.now(timezone.utc).isoformat(),
        'inputs': {str(PRIOR_DIRECTORY / name): sha for name, sha in PINNED_INPUTS.items()}, 'code_pins': pins,
        'output': str(output), 'maximum_input_bytes_per_file': MAX_INPUT_BYTES, 'maximum_output_bytes_per_file': MAX_OUTPUT_BYTES,
        'scope': {'saved_json_postprocessing_only': True, 'csv_gzip_pdf_reads': False, 'old_backend_dispatch': False,
            'strategy_or_account_execution': False, 'new_research_attempts': 0, 'formal_target_success': False}}
    intent['input_manifest_sha256'] = digest(intent['inputs'])
    output.mkdir(parents=True, exist_ok=False)
    save_once(output / 'intent.json', intent)
    receipt = {'version': VERSION, 'subject': SUBJECT, 'intent_sha256': digest(intent),
        'input_manifest_sha256': intent['input_manifest_sha256'],
        'new_research_attempts': 0, 'strategy_executed': False, 'account_executed': False, 'formal_target_success': False}
    try:
        need(code_pins() == pins, 'postprocessing implementation changed before saved input reads')
        saved = read_saved_inputs()
        bundle = prepare_inputs(saved['report.json'])
        from quanta_agents.meta_v3 import asof_return_views
        result = asof_return_views.build(**bundle)
        need(type(result) is dict and type(result.get('views')) is list, 'asof core must return explicit views')
        need(result['submitted_input_sha256'] == digest({'version': asof_return_views.VERSION, **bundle}),
             'asof core submitted-input binding changed')
        views = result['views']
        need([v['signal_date'] for v in views] == bundle['signal_dates'], 'all original signals must remain in order')
        for view in views:
            index = bundle['calendar'].index(view['signal_date'])
            window = bundle['calendar'][max(1, index-59):index+1]
            need([(row['session'], row['symbol']) for row in view['rows']] == [(day, symbol) for day in window for symbol in bundle['symbols']],
                 'every available original return coordinate must remain in its view')
            need(view['available_return_sessions'] == len(window), 'original available-history count changed')
        need(all(v['status'] == 'insufficient_history' and v['available_return_sessions'] < 60 for v in views[:12]),
             'initial twelve incomplete-history opportunities must remain explicit')
        need(all(row['qualified_return'] is None and row['generated_qualified_return'] is None
                 for view in views for row in view['rows']), 'saved real archive cannot acquire qualified returns')
        report = {'version': VERSION, 'subject': SUBJECT, 'input_sha256': digest(bundle), 'normalized_inputs': bundle,
            'input_manifest_sha256': intent['input_manifest_sha256'],
            'prior_artifact_hashes': dict(PINNED_INPUTS), 'prior_lineage_report_sha256': saved['receipt.json']['report_sha256'],
            'mapping_notes': mapping_notes(saved['report.json']), 'asof_views': result,
            'historical_arrival_authenticated': False, 'source_authenticated': False, 'new_research_attempts': 0,
            'strategy_executed': False, 'account_executed': False, 'formal_target_success': False}
        need(code_pins() == pins, 'postprocessing implementation changed during execution')
        # Recheck bytes only; never re-enter a former numerical/audit backend.
        for name, expected in PINNED_INPUTS.items():
            path = PRIOR_DIRECTORY / name
            need(path.is_file() and path.stat().st_size <= MAX_INPUT_BYTES, 'prior saved artifact grew during postprocessing')
            with path.open('rb') as stream:
                raw = stream.read(MAX_INPUT_BYTES + 1)
            need(len(raw) <= MAX_INPUT_BYTES and hashlib.sha256(raw).hexdigest() == expected,
                 'prior saved artifact changed during postprocessing')
        save_once(output / 'report.json', report)
        receipt.update(state='COMPLETE', report_sha256=digest(report),
            input_sha256=report['input_sha256'],
            report_file_sha256=hashlib.sha256((output / 'report.json').read_bytes()).hexdigest(), error=None)
    except Exception as exc:
        receipt.update(state='INCOMPLETE_TERMINAL', report_sha256=None, report_file_sha256=None,
            error={'type': type(exc).__name__, 'message': str(exc)[:4000]})
    receipt.update(wall_ms=(time.perf_counter()-wall_start)*1000, controller_cpu_ms=(time.process_time()-cpu_start)*1000)
    save_once(output / 'receipt.json', receipt)
    print(serial({'state': receipt['state'], 'output': str(output), 'formal_target_success': False}))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New directory below workspace validation; never overwritten')
    args = parser.parse_args()
    receipt = run(args.output)
    if receipt['state'] != 'COMPLETE':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
