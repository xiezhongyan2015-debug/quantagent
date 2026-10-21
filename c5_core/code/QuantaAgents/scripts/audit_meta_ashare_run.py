"""Recompute archived evidence without calling a model or loading market data."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd


def audit(root: Path, run: dict) -> dict:
    assert run['status'] == 'completed', 'Only audit a frozen completed run'
    folder = root / run['id']
    checks = []
    with zipfile.ZipFile(folder / 'source_snapshot.zip') as archive:
        for name, expected in run['config']['source_manifest']['files'].items():
            assert hashlib.sha256(archive.read(name.replace('\\', '/'))).hexdigest() == expected, name
    observations = {'starting_evidence': run['steps']['prepare']['evidence']}
    for architecture in ('baseline', 'candidate'):
        for record in run['research'][architecture]:
            if 'strategy_hash' in record['observation']:
                observations[f'{architecture}_round_{record["round"]}'] = record['observation']
        if architecture in (run.get('comparison') or {}):
            submitted = run['comparison'][architecture]
            assert submitted['split'] in {'development', 'confirmation'}
            observations[architecture + '_' + submitted['split']] = submitted
    for step, observation in observations.items():
        archive_path = folder / (step + '_execution.zip')
        entry = next(a for a in run['artifacts'] if a['name'] == archive_path.name)
        assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == entry['sha256'], archive_path.name
        with zipfile.ZipFile(archive_path) as archive:
            daily = pd.read_csv(archive.open('daily.csv'))
            trades = pd.read_csv(archive.open('trades.csv'))
        metrics = observation['metrics']
        equity = np.r_[metrics['capital'], daily.balance.to_numpy()]
        returns = equity[1:] / equity[:-1] - 1
        rf = (1.02 ** (1 / 252) - 1)
        sharpe = (returns.mean() - rf) / returns.std(ddof=1) * math.sqrt(252)
        drawdown = (equity / np.maximum.accumulate(equity) - 1).min()
        np.testing.assert_allclose(returns, daily['return'], atol=1e-12)
        np.testing.assert_allclose(sharpe, metrics['sharpe_ratio'], atol=1e-12)
        np.testing.assert_allclose(drawdown, metrics['max_ddpercent'], atol=1e-12)
        np.testing.assert_allclose(daily.balance, daily.cash + daily.position_value, atol=1e-6)
        np.testing.assert_allclose(trades.commission.sum(), metrics['total_commission'], atol=1e-6)
        dated_tax = np.where(pd.to_datetime(trades.date) < '2023-08-28', .001, .0005)
        expected_fees = np.maximum(trades.turnover.to_numpy() * .0003, 5)
        expected_fees += np.where(trades.side.eq('sell'), trades.turnover * dated_tax, 0)
        np.testing.assert_allclose(trades.commission, expected_fees, atol=1e-6)
        cash_flows = np.where(trades.side.eq('buy'), -trades.turnover, trades.turnover) - trades.commission
        cash_by_date = pd.Series(np.asarray(cash_flows), index=pd.to_datetime(trades.date)).groupby(level=0).sum()
        rebuilt_cash = metrics['capital'] + cash_by_date.reindex(pd.to_datetime(daily.date), fill_value=0).cumsum()
        np.testing.assert_allclose(rebuilt_cash, daily.cash, atol=1e-6)
        checks.append({'step': step, 'split': observation['split'], 'days': len(daily),
                       'orders': len(trades), 'recomputed_sharpe': float(sharpe),
                       'recomputed_max_drawdown': float(drawdown), 'accounting_reconciled': True})
    assert 0 < len(run['calls']) <= run['budget']['max_calls']
    assert all(call['status'] in {'completed', 'failed'} for call in run['calls'])
    assert all((call['model'], call['effort']) == ('gpt-6-astra', 'xhigh') for call in run['calls'])
    assert all(call.get('usage') and all(type(call['usage'].get(key)) is int
               for key in ('input_tokens', 'output_tokens')) for call in run['calls']), 'Unknown usage cannot be certified'
    total_tokens = sum(call['usage']['input_tokens'] + call['usage']['output_tokens'] for call in run['calls'])
    assert total_tokens == run['usage']['total_tokens']
    assert run['final_opened'] is False
    return {'run_id': run['id'], 'status': run['status'], 'total_tokens': total_tokens,
            'calls': len(run['calls']), 'elapsed_seconds': run['elapsed_seconds'],
            'failed_calls_retained': sum(call['status'] == 'failed' for call in run['calls']),
            'audit_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'source_archive_verified': True, 'final_opened': False, 'accounting_checks': checks,
            'execution_valid': False, 'promotion': False,
            'limitation': 'Archive arithmetic is consistent. Fractional raw-share exits, corporate-action cash accounting and capacity remain unverified.'}


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--api', default='http://192.0.2.10:8768')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    with urlopen(args.api.rstrip('/') + '/api/runs/' + args.run_id, timeout=20) as response:
        run = json.load(response)
    result = audit(args.root, run)
    target = args.root / args.run_id / 'independent_accounting_audit.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'audit': str(target), 'checked': len(result['accounting_checks']),
                      'tokens': result['total_tokens'], 'execution_valid': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
