"""Verify weekly market-cap coverage and create a consolidated artifact."""
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEST = Path('E:/中证500_历年成分股_基本面_2010-2026_iFinD_PIT')


def main():
    universe = pd.read_parquet(DEST / 'plan/weekly_universe.parquet')
    conn = sqlite3.connect(DEST / 'manifest.sqlite')
    entries = conn.execute('SELECT asof,rows,path,sha256 FROM market').fetchall()
    frames = []
    for date, rows, file, sha256 in entries:
        path = DEST / file
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
            raise ValueError(f'Hash mismatch: {file}')
        frame = pd.read_parquet(path)
        if len(frame) != rows or set(frame.asof_date) != {date}:
            raise ValueError(f'Manifest mismatch: {file}')
        frames.append(frame)
    merged = pd.concat(frames, ignore_index=True).sort_values(['asof_date', 'thscode'])
    if merged.duplicated(['asof_date', 'thscode']).any():
        raise ValueError('Duplicate stock/date keys')
    keys = ['asof_date', 'thscode']
    coverage = merged[keys].merge(universe[keys], on=keys, how='outer', indicator=True, validate='one_to_one')
    if not (coverage['_merge'] == 'both').all():
        raise ValueError('Weekly membership coverage mismatch')
    output = DEST / 'weekly_market_cap.parquet'
    merged.to_parquet(output, index=False)
    result = dict(phase='weekly_market_cap', status='verified', audited_at=datetime.now().isoformat(),
                  files=len(entries), rows=len(merged), weekly_dates=int(merged.asof_date.nunique()),
                  duplicate_keys=0, membership_exact_match=True, all_file_sha256_verified=True,
                  null_market_cap=int(merged.total_market_cap_cny.isna().sum()),
                  min_date=merged.asof_date.min(), max_date=merged.asof_date.max(),
                  consolidated_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                  incomplete_phases=['financial_statements_PIT', 'historical_industry'],
                  note='This audit covers market cap only; it does not certify completion of all fundamentals.')
    (DEST / 'audit/market_cap_quality_report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    for name in ['prepare_ifind_fundamentals.py', 'download_ifind_fundamental_market.py',
                 'audit_ifind_fundamental_market.py', 'ifind_fundamental_worker.py',
                 'ifind_api_worker.py', 'ifind_parallel_dispatch.py']:
        shutil.copy2(ROOT / 'scripts' / name, DEST / 'scripts_backup' / name)
    probes = DEST / 'audit/validation_samples'
    probes.mkdir(exist_ok=True)
    for path in (ROOT / 'data_expansion/ifind_fundamental_control/results').glob('00*.json'):
        shutil.copy2(path, probes / path.name)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
