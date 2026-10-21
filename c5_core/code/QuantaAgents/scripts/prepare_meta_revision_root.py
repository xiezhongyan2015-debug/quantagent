"""Copy a completed research ledger with SQLite backup; keep the old server intact."""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path
from urllib.request import urlopen


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    project = Path(__file__).resolve().parents[1]
    old = project / 'experiment_traces/meta_ashare'
    target = project / 'experiment_traces/meta_ashare_v2'
    assert target.resolve().is_relative_to(project.resolve()) and target != old
    if target.exists():
        raise RuntimeError('Destination already exists; inspect rather than overwrite a ledger.')
    with urlopen('http://192.0.2.10:8768/api/runs', timeout=20) as response:
        runs = json.load(response)['runs']
    if any(run['status'] != 'completed' for run in runs):
        raise RuntimeError('Only a completely idle, completed ledger may be copied.')
    target.mkdir()
    for name in ('ledger.sqlite3', 'checkpoints.sqlite3'):
        with sqlite3.connect(str(old / name)) as origin, sqlite3.connect(str(target / name)) as destination:
            origin.backup(destination)
            assert destination.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    for run in runs:
        name = run['id']
        assert Path(name).name == name and name.startswith('meta-')
        shutil.copytree(old / name, target / name)
    manifest = {'source_root': str(old), 'destination_root': str(target),
        'run_ids': [run['id'] for run in runs], 'method': 'SQLite online backup plus completed immutable run folders',
        'exposure_history_preserved': True, 'old_service_stopped': False,
        'note': 'Imported paid receipts preserve their original workdir provenance; no model was called.'}
    (target / 'ledger_import.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == '__main__':
    main()
