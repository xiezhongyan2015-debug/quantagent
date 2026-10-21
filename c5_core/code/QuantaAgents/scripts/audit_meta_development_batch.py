"""Read-only audit of a frozen development batch; never loads market data or models.

Reads terminal run bodies and calls in one SQLite read transaction. Writes only
under the batch's independent_audit directory. Financial arithmetic is checked
against the archived simulator, not certified as executable A-share accounting.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import zipfile

import numpy as np
import pandas as pd

from audit_meta_ashare_run import audit as audit_completed_run


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value) -> str:
    return sha(json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False).encode('utf-8'))


def runtime_digest(value) -> str:
    return sha(json.dumps(value, ensure_ascii=False, allow_nan=False,
                          default=str).encode('utf-8'))


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                               indent=2), encoding='utf-8')


def verify(condition, message):
    if not condition:
        raise ValueError(message)


def observations(run):
    yield 'starting_evidence', run['steps']['prepare']['evidence']
    for architecture in ('baseline', 'candidate'):
        for record in run['research'][architecture]:
            if 'strategy_hash' in record['observation']:
                yield f'{architecture}_round_{record["round"]}', record['observation']
        final = (run.get('comparison') or {}).get(architecture)
        if final:
            verify(final['split'] == 'development', 'Non-development result is forbidden')
            yield architecture + '_development', final


def audit_archive(path: Path, observation: dict):
    """Also audits the failed run's completed observations, without relabeling it."""
    verify(observation['split'] == 'development', 'Non-development artifact is forbidden')
    with zipfile.ZipFile(path) as archive:
        content_hashes = {name: sha(archive.read(name)) for name in archive.namelist()}
        daily = pd.read_csv(archive.open('daily.csv'))
        trades = pd.read_csv(archive.open('trades.csv'))
    dates = pd.to_datetime(daily.date)
    verify(dates.min() >= pd.Timestamp('2017-01-01') and dates.max() <= pd.Timestamp('2021-12-31'), 'Date boundary')
    verify(dates.is_unique and dates.is_monotonic_increasing, 'Invalid daily dates')
    metrics = observation['metrics']
    equity = np.r_[metrics['capital'], daily.balance.to_numpy()]
    returns = equity[1:] / equity[:-1] - 1
    sharpe = float((returns.mean() - (1.02 ** (1 / 252) - 1)) / returns.std(ddof=1) * np.sqrt(252))
    np.testing.assert_allclose(returns, daily['return'], atol=1e-12, rtol=0)
    np.testing.assert_allclose(sharpe, metrics['sharpe_ratio'], atol=1e-12, rtol=0)
    np.testing.assert_allclose((equity / np.maximum.accumulate(equity) - 1).min(), metrics['max_ddpercent'], atol=1e-12, rtol=0)
    np.testing.assert_allclose(daily.balance, daily.cash + daily.position_value, atol=1e-6, rtol=0)
    np.testing.assert_allclose(trades.turnover, trades.price * trades.shares, atol=1e-6, rtol=0)
    # The frozen simulator caps adverse prices at limit prices. This archive
    # lacks the full opening bar, so verify booked slippage and its bound, and
    # retain clipped orders as unverified limit-price calculations.
    adverse = (trades.price - trades.reference_price) * np.where(trades.side.eq('buy'), 1, -1)
    verify(bool((adverse >= -1e-10).all()) and bool((adverse <= trades.reference_price * .001 + 1e-10).all()), 'Adverse price bound')
    np.testing.assert_allclose(trades.slippage, adverse * trades.shares, atol=1e-6, rtol=0)
    clipped = adverse < trades.reference_price * .001 - 1e-10
    expected_fees = np.maximum(trades.turnover.to_numpy() * .0003, 5) + np.where(trades.side.eq('sell'), trades.turnover * .001, 0)
    np.testing.assert_allclose(trades.commission, expected_fees, atol=1e-6, rtol=0)
    np.testing.assert_allclose(trades.commission.sum(), metrics['total_commission'], atol=1e-6, rtol=0)
    np.testing.assert_allclose(trades.slippage.sum(), metrics['total_slippage'], atol=1e-6, rtol=0)
    cash_flow = np.where(trades.side.eq('buy'), -trades.turnover, trades.turnover) - trades.commission
    by_date = pd.Series(np.asarray(cash_flow), index=pd.to_datetime(trades.date)).groupby(level=0).sum()
    np.testing.assert_allclose(metrics['capital'] + by_date.reindex(dates, fill_value=0).cumsum(), daily.cash, atol=1e-6, rtol=0)
    fraction = (trades.raw_shares - trades.raw_shares.round()).abs() > 1e-6
    return {'archive': path.name, 'archive_sha256': sha(path.read_bytes()),
            'member_sha256': content_hashes, 'split': 'development', 'date_min': str(dates.min().date()),
            'date_max': str(dates.max().date()), 'days': len(daily), 'orders': len(trades),
            'recomputed_sharpe': sharpe, 'cash_fees_slippage_reconciled': True,
            'non_integer_raw_share_orders': int(fraction.sum()),
            'non_integer_buy_orders': int((fraction & trades.side.eq('buy')).sum()),
            'non_integer_sell_orders': int((fraction & trades.side.eq('sell')).sum()),
            'price_limit_clipped_orders_not_independently_certified': int(clipped.sum()),
            'strategy_hash': observation['strategy_hash'], 'strategy': observation['strategy'],
            'metrics': metrics, 'yearly': observation.get('yearly'), 'execution_valid': False}


def audit_call(call: dict, folder: Path):
    workdir = folder / 'calls' / f'{call["step"]}-{call["attempt"]}'
    verify(workdir.resolve() == Path(call['workdir']).resolve(), 'Call workdir mismatch')
    paths = {p.name: sha(p.read_bytes()) for p in workdir.iterdir() if p.is_file()}
    prompt = (workdir / 'prompt.txt').read_text(encoding='utf-8')
    verify(runtime_digest(prompt) == call['prompt_hash'], 'Prompt mismatch')
    request = read(workdir / 'codex_request.json')
    verify((request['model'], request['effort'], call['model'], call['effort']) ==
           ('gpt-6-astra', 'xhigh', 'gpt-6-astra', 'xhigh'), 'Model request mismatch')
    command = request['command']
    verify(command[command.index('-m') + 1] == 'gpt-6-astra' and 'model_reasoning_effort="xhigh"' in command
           and '--ephemeral' in command and 'resume' not in command, 'Request is not fresh Astra/xhigh')
    events = [json.loads(line) for line in (workdir / 'codex_events.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    thread_ids = [event['thread_id'] for event in events if event.get('type') == 'thread.started']
    verify(len(thread_ids) == 1, 'Expected exactly one CLI thread')
    terminal = [event for event in events if event.get('type') == 'turn.completed']
    verify(len(terminal) == 1, 'Missing or duplicate usage event')
    usage = call['usage']
    for key in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens'):
        verify(type(usage.get(key)) is int and terminal[0]['usage'].get(key) == usage[key], 'Usage mismatch: ' + key)
    output = read(workdir / 'response.json')
    messages = [e['item']['text'] for e in events if e.get('item', {}).get('type') == 'agent_message']
    verify(len(messages) == 1 and json.loads(messages[0]) == output, 'Response does not match provider event')
    if call['status'] == 'completed':
        receipt = read(workdir / 'receipt.json')
        verify(receipt == call['receipt'] and receipt['response'] == output, 'Completed receipt mismatch')
        verified = receipt['model_verified']
    else:
        verify(call['status'] == 'failed' and not (workdir / 'receipt.json').exists(), 'Unexpected failed receipt')
        verified = None
    return {'call_id': call['id'], 'step': call['step'], 'attempt': call['attempt'],
            'status': call['status'], 'started_at': call['started_at'], 'ended_at': call['ended_at'],
            'thread_id': thread_ids[0], 'prompt_hash': call['prompt_hash'], 'prompt_characters': len(prompt),
            'response_canonical_sha256': canonical(output), 'request_sha256': paths['codex_request.json'],
            'file_sha256': paths, 'usage': usage, 'total_tokens': usage['input_tokens'] + usage['output_tokens'],
            'provider_settings_verified': verified, 'requested_model': request['model'], 'requested_effort': request['effort'],
            'error': call.get('error'), 'stderr': (workdir / 'codex_stderr.log').read_text(encoding='utf-8'),
            'output_completed_before_process_failure': call['status'] == 'failed',
            'response_action': output['action'], 'response_strategy': output['strategy']}


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('experiment_traces/meta_ashare_v2'))
    parser.add_argument('--batch', default='dev_20260906_01')
    args = parser.parse_args()
    root = args.root.resolve()
    batch = root / 'batches' / args.batch
    plan, report = read(batch / 'plan.json'), read(batch / 'report.json')
    verify(plan['plan_sha256'] == canonical({k: v for k, v in plan.items() if k != 'plan_sha256'}), 'Plan hash mismatch')
    verify(report['plan_sha256'] == plan['plan_sha256'] and report['status'] == 'completed', 'Batch is not frozen completed')
    verify(plan['scope'] == 'development_only' and plan['evaluation_split'] == 'development', 'Scope mismatch')
    verify(len(plan['entries']) == len(report['entries']) == 8, 'All eight planned entries required')
    verify(sha(plan['research_instructions'].encode('utf-8')) == plan['research_instructions_sha256'], 'Instructions hash mismatch')
    out = batch / 'independent_audit'
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect((root / 'ledger.sqlite3').as_uri() + '?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')
    conn.execute('BEGIN')
    runs = []
    raw_bodies = {}
    for entry in report['entries']:
        raw = conn.execute('SELECT body FROM runs WHERE id=?', (entry['run_id'],)).fetchone()[0]
        raw_bodies[entry['run_id']] = sha(raw.encode('utf-8'))
        run = json.loads(raw)
        run['calls'] = [json.loads(row[0]) for row in conn.execute('SELECT body FROM calls WHERE run_id=? ORDER BY rowid', (run['id'],))]
        run['usage'] = {key: sum(c['usage'][key] for c in run['calls']) for key in
                        ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')}
        run['usage']['total_tokens'] = run['usage']['input_tokens'] + run['usage']['output_tokens']
        verify(run.get('active_started_epoch') is None, 'Still active')
        run['elapsed_seconds'] = run['active_seconds']
        runs.append(run)
    exposures = [dict(zip(('case_key', 'split', 'run_id', 'opened_at', 'submissions_hash'), row))
                 for row in conn.execute('SELECT case_key,split,run_id,opened_at,submissions_hash FROM evaluation_exposures')
                 if row[2] in {r['id'] for r in runs}]
    conn.rollback()
    conn.close()
    verify(not exposures, 'This development-only batch should not register confirmation or final exposure')
    summaries, all_calls, all_archives = [], [], []
    identities = set()
    for planned, entry, run in zip(plan['entries'], report['entries'], runs):
        folder = root / run['id']
        verify(planned['entry_id'] == entry['entry_id'] and run['request_key'] == planned['request_key'], 'Member mismatch')
        verify(run['status'] == entry['status'] and run['status'] in {'completed', 'failed'}, 'Terminal mismatch')
        verify(run['mode'] == 'live' and run['comparable'] and not run['interventions'], 'Intervened run')
        config = run['config']
        verify(config['source_manifest']['hash'] == plan['source_hash'] == runtime_digest(config['source_manifest']['files']), 'Source identity mismatch')
        verify(config['only_architecture'] == planned['architecture'] and config['case']['task_key'] == planned['task'], 'Task/arm mismatch')
        verify(config['evaluation_split'] == 'development' and config['final_opened'] is False
               and not run.get('confirmation_opened', False) and not run.get('final_opened', False), 'Holdout opened')
        verify(config['research_calls_per_architecture'] == plan['research_calls'] == 12, 'Call budget mismatch')
        verify(sha(config['frozen_candidate']['research_instructions'].encode('utf-8')) == plan['research_instructions_sha256'], 'Frozen instructions mismatch')
        verify(config['frozen_candidate']['source_run_id'] == plan['source_run_id'] and
               canonical({k: v for k, v in config['frozen_candidate'].items() if k != 'source_run_id'}) == plan['source_proposal_sha256'],
               'Frozen full proposal mismatch')
        verify(read(folder / 'development_packet.json') == run['steps']['prepare']['packet'], 'Packet artifact mismatch')
        verify(run['steps']['prepare']['packet']['datahash'] == run['steps']['prepare']['evidence']['datahash']
               and run['steps']['prepare']['packet']['loaded_through'] == '2021-12-31', 'Packet identity mismatch')
        with zipfile.ZipFile(folder / 'source_snapshot.zip') as archive:
            expected = {k.replace('\\', '/'): v for k, v in config['source_manifest']['files'].items()}
            verify(set(archive.namelist()) == set(expected), 'Source file set mismatch')
            verify(all(sha(archive.read(k)) == v for k, v in expected.items()), 'Source bytes mismatch')
        artifact_map = {item['name']: item for item in run['artifacts']}
        for name, item in artifact_map.items():
            if item.get('sha256'):
                verify(sha((folder / name).read_bytes()) == item['sha256'], f'Artifact mismatch: {run["id"]}/{name}')
        source_rows = []
        for label, observation in observations(run):
            identity = (observation['snapshot_id'], observation['datahash'], observation['loaded_through'])
            identities.add(identity)
            verify(identity[0] == config['case']['datahash'] and identity[2] == '2021-12-31', 'Data identity/date mismatch')
            result = audit_archive(folder / (label + '_execution.zip'), observation)
            result.update(run_id=run['id'], step=label)
            source_rows.append(result)
            all_archives.append(result)
        expected_zips = {row['archive'] for row in source_rows}
        verify(expected_zips == {path.name for path in folder.glob('*_execution.zip')}, 'Unaccounted execution ZIP')
        call_results = [audit_call(call, folder) for call in run['calls']]
        verify(len({c['thread_id'] for c in call_results}) == len(call_results), 'Repeated thread within run')
        verify(all(c['attempt'] == 1 for c in call_results), 'Unexpected retry')
        all_calls.extend(call_results)
        verify(run['usage']['total_tokens'] == entry['known_tokens'], 'Reported usage mismatch')
        if run['status'] == 'completed':
            existing = audit_completed_run(root, run)
            verify(len(existing['accounting_checks']) == len(source_rows), 'Independent archive coverage mismatch')
            write(out / (run['id'] + '_accounting.json'), existing)
        trajectory = []
        arm = entry['architecture']
        previous = run['steps']['prepare']['evidence']
        for record in run['research'][arm]:
            response, observation = record['response'], record['observation']
            verify(observation['datahash'] == run['steps']['prepare']['packet']['datahash'], 'Diagnostic data identity mismatch')
            verify(runtime_digest(response) == record['request_hash'] and runtime_digest(observation) == record['observation_hash'], 'Research hash mismatch')
            verify(record['reviewed_evidence_hash'] == runtime_digest(previous), 'Self-check review timing mismatch')
            call = next(c for c in run['calls'] if c['step'] == f'{arm}_round_{record["round"]}')
            verify(call['receipt']['response'] == response, 'Research response not from this call')
            trajectory.append({'round': record['round'], 'action': response['action'],
                               'strategy': response['strategy'], 'diagnostic_expression': response['diagnostic_expression'],
                               'diagnostic_horizon': response['diagnostic_horizon'], 'hypothesis': response['hypothesis'],
                               'expected_observation': response['expected_observation'],
                               'decision_summary': response['decision_summary'], 'self_check': response['self_check'],
                               'observation_error': observation.get('error'), 'observation_hash': record['observation_hash'],
                               'net_development_sharpe': observation.get('metrics', {}).get('sharpe_ratio')})
            previous = observation
        final = (run.get('comparison') or {}).get(arm)
        actions = dict(Counter(row['action'] for row in trajectory))
        summary = {'entry_id': entry['entry_id'], 'run_id': run['id'], 'task': entry['task'], 'repeat': entry['repeat'],
                   'architecture': arm, 'status': run['status'], 'usage': run['usage'], 'elapsed_seconds': run['elapsed_seconds'],
                   'calls': len(call_results), 'failed_calls': sum(c['status'] == 'failed' for c in call_results),
                   'research_actions': actions, 'tool_errors': sum(bool(x['observation_error']) for x in trajectory),
                   'all_execution_archives': len(source_rows), 'source_and_artifacts_verified': True,
                   'final_strategy': final['strategy'] if final else None, 'final_metrics': final['metrics'] if final else None,
                   'final_strategy_hash': final['strategy_hash'] if final else None,
                   'best_observed_development_sharpe': max((x['net_development_sharpe'] for x in trajectory if x['net_development_sharpe'] is not None), default=None),
                   'trajectory': trajectory, 'error': run.get('last_error'), 'execution_valid': False,
                   'confirmation_opened': False, 'final_opened': False}
        if final:
            verify(final['metrics']['sharpe_ratio'] == entry['net_development_sharpe'], 'Reported metric mismatch')
        summaries.append(summary)
        write(out / (run['id'] + '_details.json'), {'summary': summary, 'calls': call_results, 'archives': source_rows})
        print(json.dumps({'entry': entry['entry_id'], 'status': run['status'], 'archives': len(source_rows), 'tokens': run['usage']['total_tokens']}, ensure_ascii=False), flush=True)
    verify(len(identities) == 1, 'Data content differs between runs')
    verify(len({c['thread_id'] for c in all_calls}) == len(all_calls), 'Repeated CLI thread across runs')
    verify(len({r['request_key'] for r in runs}) == 8, 'Repeated create request')
    verify(sum(r['usage']['total_tokens'] for r in runs) == report['usage']['known_tokens'], 'Batch cost mismatch')
    pairs = []
    for task in plan['tasks']:
        for repeat in range(1, plan['repeats'] + 1):
            members = {s['architecture']: s for s in summaries if s['task'] == task and s['repeat'] == repeat}
            complete = all(s['status'] == 'completed' for s in members.values())
            pairs.append({'task': task, 'repeat': repeat, 'complete': complete,
                          'entries': {a: s['entry_id'] for a, s in members.items()},
                          'candidate_minus_baseline': (members['candidate']['final_metrics']['sharpe_ratio'] -
                                                       members['baseline']['final_metrics']['sharpe_ratio']) if complete else None,
                          'tokens_all_members': sum(s['usage']['total_tokens'] for s in members.values())})
    duplicate_finals = []
    for architecture in ('baseline', 'candidate'):
        ss = [s for s in summaries if s['task'] == 'volume_expansion' and s['architecture'] == architecture]
        aa = [next(a for a in all_archives if a['run_id'] == s['run_id'] and a['step'] == architecture + '_development') for s in ss]
        cc = [[c for c in all_calls if c['call_id'].startswith(s['run_id'] + '/')] for s in ss]
        duplicate_finals.append({'architecture': architecture, 'run_ids': [s['run_id'] for s in ss],
                                 'strategy_text_equal': ss[0]['final_strategy'] == ss[1]['final_strategy'],
                                 'daily_trades_targets_content_equal': aa[0]['member_sha256'] == aa[1]['member_sha256'],
                                 'shared_thread_ids': sorted(set(c['thread_id'] for c in cc[0]) & set(c['thread_id'] for c in cc[1])),
                                 'shared_exact_response_hashes': sorted(set(c['response_canonical_sha256'] for c in cc[0]) & set(c['response_canonical_sha256'] for c in cc[1])),
                                 'same_round_prompt_equal': [a['prompt_hash'] == b['prompt_hash'] for a, b in zip(*cc)],
                                 'same_round_response_equal': [a['response_canonical_sha256'] == b['response_canonical_sha256'] for a, b in zip(*cc)],
                                 'interpretation': 'Distinct ephemeral CLI calls and responses; same final executed strategy. Input prefix caching does not mean cached model output. Provider-side independence is not proved.'})
    result = {'schema_version': 1, 'audited_at': datetime.now(timezone.utc).isoformat(), 'batch_id': args.batch,
              'script_sha256': sha(Path(__file__).read_bytes()), 'helper_script_sha256': sha(Path(__file__).with_name('audit_meta_ashare_run.py').read_bytes()),
              'plan_sha256': plan['plan_sha256'], 'plan_file_sha256': sha((batch / 'plan.json').read_bytes()),
              'report_file_sha256': sha((batch / 'report.json').read_bytes()), 'source_hash': plan['source_hash'],
              'ledger_read_transaction': 'SQLite URI mode=ro + query_only, committed terminal members only; no market-file access',
              'run_body_sha256': raw_bodies, 'data_identity': dict(zip(('snapshot_id', 'development_datahash', 'loaded_through'), next(iter(identities)))),
              'planned_runs': 8, 'completed_runs': sum(s['status'] == 'completed' for s in summaries),
              'failed_runs': sum(s['status'] == 'failed' for s in summaries), 'all_calls': len(all_calls),
              'failed_calls': sum(c['status'] == 'failed' for c in all_calls), 'all_execution_archives': len(all_archives),
              'total_tokens': sum(c['total_tokens'] for c in all_calls),
              'usage': {key: sum(c['usage'][key] for c in all_calls) for key in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')},
              'elapsed_batch_seconds': report['elapsed_seconds'], 'sum_run_active_seconds': sum(s['elapsed_seconds'] for s in summaries),
              'pairs': pairs, 'volume_repeat_independence': duplicate_finals,
              'summaries': [{k: v for k, v in s.items() if k != 'trajectory'} for s in summaries],
              'development_sharpe_gt_1_completed_count': sum(bool(s['final_metrics'] and s['final_metrics']['sharpe_ratio'] > 1) for s in summaries),
              'provider_settings_verified_calls': sum(c['provider_settings_verified'] is True for c in all_calls),
              'execution_valid': False, 'formal_target_success': False, 'promotion': False,
              'self_checks': [
                  {'step': 'accounting', 'assessment': 'All archived development cash, fees, slippage and Sharpe were recomputed, including completed observations from the failed run; this does not repair fractional-share/corporate-action/capacity limitations.', 'next_step': 'Evaluate future architectures only on a separately frozen execution version once the execution gaps are resolved.'},
                  {'step': 'independence_and_behavior', 'assessment': 'CLI requests, thread IDs and responses were checked; repeat convergence is observable, provider-side stochastic independence and architecture superiority remain unproved.', 'next_step': 'Use recorded action allocation and actual economic failure diagnostics to design one new controller-level improvement; retain all four pair slots and the failed cost.'}]}
    write(out / 'audit.json', result)
    write(out / 'calls_index.json', all_calls)
    write(out / 'archives_index.json', all_archives)
    print(json.dumps({k: result[k] for k in ('completed_runs', 'failed_runs', 'all_calls', 'all_execution_archives', 'total_tokens', 'formal_target_success')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
