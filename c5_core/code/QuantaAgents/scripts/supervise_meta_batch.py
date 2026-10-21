"""Resume the same idempotent dispatcher after bounded report-file access errors.

This operational supervisor does not alter frozen research code, plans, prompts,
or model-call retry policy. Unknown creation/model results still stop the batch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def supervise(run_once, *, emit, sleep=time.sleep, monotonic=time.monotonic, max_consecutive=8):
    failures, last_failure = 0, None
    while True:
        try:
            report = run_once()
        except PermissionError as exc:
            failure = {'type': type(exc).__name__, 'detail': str(exc)}
        else:
            # tick can persist an attention result after a transient report write
            # error. Only this precise filesystem outcome may be retried here.
            if (report.get('status') == 'attention'
                    and report.get('stop_reason') == 'read-only API reconciliation unavailable: PermissionError'
                    and any(e.get('run_id') and e.get('run_status') in {'running', 'queued', 'paused', 'pausing', 'cancelling'}
                            for e in report.get('entries', []))):
                failure = {'type': 'PermissionError', 'detail': report['stop_reason']}
            else:
                return report
        current = monotonic()
        failures = failures + 1 if last_failure is not None and current - last_failure <= 60 else 1
        last_failure = current
        emit({**failure, 'consecutive_file_access_failures': failures,
              'action': 'resume_existing_dispatcher_via_ledger' if failures < max_consecutive else 'stop_supervisor'})
        if failures >= max_consecutive:
            raise RuntimeError('Repeated report-file access failures; supervisor stopped without creating a replacement run.')
        sleep(min(2.0, .25 * failures))


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project / 'src'))
    from quanta_agents.meta import batch
    root = args.output_dir.resolve()
    manifest = {'started_at': datetime.now(timezone.utc).isoformat(),
                'supervisor_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'frozen_batch_module_sha256': hashlib.sha256(Path(batch.__file__).read_bytes()).hexdigest(),
                'purpose': 'Bounded filesystem crash recovery only; same frozen plan and request keys; no model retry override.'}
    def emit(event):
        record = {'at': datetime.now(timezone.utc).isoformat(), **event}
        with (root / 'supervisor_events.jsonl').open('a', encoding='utf-8') as output:
            output.write(json.dumps(record, ensure_ascii=False) + '\n')
        print(json.dumps(record, ensure_ascii=False), flush=True)
    with batch.BatchLock(root / '.supervisor'):
        (root / 'supervisor_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        report = supervise(lambda: batch.run(root, poll_seconds=15), emit=emit)
        emit({'event': 'supervisor_finished', 'batch_status': report['status'], 'stop_reason': report.get('stop_reason')})


if __name__ == '__main__':
    main()
