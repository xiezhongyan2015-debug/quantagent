"""Root capture for one exact populated source-key audit invocation."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PREP = ROOT / 'artifacts/daily_member_source_keys_audit_preparation_v1_20261002'
CAPTURE = ROOT / 'artifacts/daily_member_source_keys_once_capture_v1_20261002/attempt1'
OUTPUT = ROOT / 'artifacts/daily_member_source_keys_audit_actual_v1_20261002/attempt1'
PYTHON = '/Users/leslie/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3'
PINS = [
    (ROOT / 'isolated_audits/daily_member_source_keys_audit_v1.py', 12640, 'd4c8f9fcb7920a430ea3e10317b4a463823bb5598be80adc1b0eac8b5e78aa03'),
    (PREP / 'sealed_manifest_v1.json', 1419374, 'f3c50fe039df05db501f6ea61a9eaf77511420b11d7016bbdcdaf65da1b46b05'),
    (PREP / 'preparation_receipt_v1.json', 1021, '875f0a4b6a0eefa5d5ded2bfed903b9c559bcc52796c63e08c990de1901ba53f'),
    (PREP / 'root_static_acceptance_v1.json', 1380, '25f874ae30bbbee1716d03d5db53d02f7bbdd2d55d19d804a95eb0031af4f681'),
    (ROOT / 'artifacts/daily_member_source_keys_audit_independent_static_review_v1_20261002.json', 12160, '207f8949176eb98206507058d72c9d84f641b2db0e22f703508328fb70cc8ffb'),
]

def now():
    return datetime.now(timezone.utc).isoformat()

def new(name, data):
    with (CAPTURE / name).open('x', encoding='utf-8') as file:
        json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write('\n')

def byte_guard():
    observed = []
    for path, count, expected in PINS:
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if len(raw) != count or actual != expected:
            raise ValueError('sealed source/prep/review identity mismatch: ' + path.name)
        observed.append({'path': str(path), 'bytes': len(raw), 'sha256': actual})
    return observed

def source_guard(manifest):
    base = Path(manifest['source_root'])
    groups = manifest['source_files']
    actual_members = sorted(str(p) for p in (base / 'memberships').glob('*.json.gz')
                            if '2010-01-01' <= p.name[:10] <= '2026-09-23')
    actual_bars = sorted(str(p) for p in (base / 'bars').glob('*/*/*.parquet'))
    if len(actual_members) != 4063 or len(actual_bars) != 280:
        raise ValueError('exact positive roster counts changed')
    if actual_members != [x['path'] for x in groups['memberships']] or actual_bars != [x['path'] for x in groups['daily_bars']]:
        raise ValueError('exact positive source path roster changed')
    sources = groups['memberships'] + groups['daily_bars']
    if len({x['path'] for x in sources}) != 4343 or sum(x['stat']['bytes'] for x in sources) != 141175566:
        raise ValueError('sealed source count/bytes changed')
    for item in sources:
        path = Path(item['path'])
        if not path.is_absolute() or not path.is_relative_to(base) or any(p.is_symlink() for p in [path, *path.parents]) or not path.is_file():
            raise ValueError('source path is not a regular exact local file')
        st = path.stat()
        if {'bytes': st.st_size, 'mtime_ns': st.st_mtime_ns, 'device': st.st_dev, 'inode': st.st_ino} != item['stat']:
            raise ValueError('source STAT changed: ' + path.name)
    small = []
    for item in manifest['small_bindings']:
        raw = Path(item['path']).read_bytes()
        actual = {'path': item['path'], 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        if actual != item:
            raise ValueError('named small binding changed')
        small.append(actual)
    return {'memberships': 4063, 'daily_bars': 280, 'total_sources': 4343,
            'source_bytes': 141175566, 'source_STAT_exact': True,
            'source_bodies_read_by_wrapper': 0, 'small_byte_bindings': small}

def main():
    CAPTURE.mkdir(parents=True, exist_ok=False)
    new('ROOT_CAPTURE_STARTED_v1.json', {'started_utc': now(), 'retry_allowed': False})
    pid = None
    exit_code = None
    try:
        before_pins = byte_guard()
        manifest = json.loads((PREP / 'sealed_manifest_v1.json').read_bytes())
        if manifest['fixed_output'] != str(OUTPUT) or OUTPUT.exists():
            raise ValueError('fixed actual output already exists or changed')
        before_sources = source_guard(manifest)
        new('root_before_guard_v1.json', {'created_utc': now(), 'pins': before_pins, 'source_guard': before_sources})
        args = [PYTHON, str(PINS[0][0]), '--manifest', str(PREP / 'sealed_manifest_v1.json'), '--output', str(OUTPUT)]
        new('ROOT_DISPATCH_ONCE_v1.json', {'created_utc': now(), 'argv': args, 'retry_allowed': False})
        with (CAPTURE / 'stdout.txt').open('xb') as out, (CAPTURE / 'stderr.txt').open('xb') as err:
            started = now()
            process = subprocess.Popen(args, cwd=str(ROOT), stdout=out, stderr=err)
            pid = process.pid
            new('root_process_started_v1.json', {'started_utc': started, 'pid': pid, 'retry_allowed': False})
            exit_code = process.wait()
        after_pins = byte_guard()
        after_sources = source_guard(manifest)
        if before_pins != after_pins or before_sources != after_sources:
            raise ValueError('before/after guard changed')
        identities = []
        for name in ['stdout.txt', 'stderr.txt']:
            path = CAPTURE / name
            raw = path.read_bytes()
            identities.append({'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        receipt = {'status': 'ONCE_EXECUTED_CAPTURED', 'started_utc': started, 'ended_utc': now(),
                   'pid': pid, 'exit_code': exit_code, 'pins_before_after_equal': True,
                   'exact_source_roster_and_STAT_before_after_equal': True,
                   'source_bodies_read_by_wrapper': 0, 'source_guard': after_sources,
                   'stdout_stderr': identities, 'retry_allowed': False, 'full_PLAN_complete': False}
        new('root_once_execution_capture_v1.json', receipt)
        print(json.dumps({'status': receipt['status'], 'pid': pid, 'exit_code': exit_code, 'capture': str(CAPTURE)}))
        return exit_code
    except Exception as exc:
        new('ROOT_CAPTURE_FAILED_v1.json', {'created_utc': now(), 'pid': pid, 'exit_code': exit_code,
                                          'error': type(exc).__name__ + ': ' + str(exc), 'retry_allowed': False})
        raise

if __name__ == '__main__':
    raise SystemExit(main())
