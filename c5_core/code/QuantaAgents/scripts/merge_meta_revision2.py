"""Merge the reviewed isolated revision, checking the prior frozen source first."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    project = Path(__file__).resolve().parents[1]
    stage = project / 'experiment_traces/meta_ashare_revision2'
    frozen = json.loads((project / 'experiment_traces/meta_ashare/meta-20260906-001003-9b47f3/manifest.json').read_text(encoding='utf-8'))
    for relative, expected in frozen['source_manifest']['files'].items():
        if sha(project / 'src/quanta_agents' / relative) != expected:
            raise RuntimeError(f'Original source changed since the frozen run: {relative}')
    backup = project / 'experiment_traces/meta_ashare_revision2_merge_backup'
    if backup.exists():
        raise RuntimeError('Merge backup already exists; inspect rather than merging twice.')
    changed = []
    for base in ('src', 'tests'):
        for source in sorted((stage / base).rglob('*')):
            if not source.is_file() or source.suffix not in {'.py', '.yaml', '.html', '.js'}:
                continue
            relative = source.relative_to(stage)
            target = project / relative
            if sha(source) != sha(target):
                changed.append((relative, source, target, sha(target)))
    if not changed:
        raise RuntimeError('No revision changes found.')
    backup.mkdir()
    report = []
    for relative, source, target, old_hash in changed:
        assert target.resolve().is_relative_to(project.resolve())
        if target.exists():
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        report.append({'file': str(relative), 'old_sha256': old_hash, 'new_sha256': sha(target)})
    output = {'merged_at': datetime.now().astimezone().isoformat(),
              'frozen_source_run_id': 'meta-20260906-001003-9b47f3', 'files': report}
    (backup / 'merge_manifest.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'files': [entry['file'] for entry in report], 'backup': str(backup)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
