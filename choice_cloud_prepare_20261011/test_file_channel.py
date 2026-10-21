"""Reuse user-authenticated SSH to upload and verify an inert staging test file.

No Choice calls, credentials, target lists, live-directory writes, or task creation.
"""
import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import uuid


def main():
    prep = Path(__file__).resolve().parent
    control = Path('/tmp/choice-ssh-leslie-20261011/control')
    if not control.exists():
        raise SystemExit('SSH authentication pending: run SSH登录.command and keep the window open.')
    token = uuid.uuid4().hex
    payload = dict(kind='file_channel_smoke_test', orders_enabled=False, id=token,
                   generated_at=dt.datetime.now(dt.timezone.utc).isoformat())
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    expected = hashlib.sha256(raw).hexdigest()
    encoded = base64.b64encode(raw).decode()
    # Only generated tokens enter this command. Never interpolate user-provided paths.
    script = r"""$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$dir='C:\ChoiceBridge\staging'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$path=Join-Path $dir 'smoke_TOKEN.json'
[IO.File]::WriteAllBytes($path,[Convert]::FromBase64String('PAYLOAD'))
$hash=(Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
if($hash -ne 'EXPECTED'){throw 'Transfer hash mismatch'}
@{path=$path;sha256=$hash;timezone=(Get-TimeZone).Id;authenticated=$true;orders_enabled=$false}|ConvertTo-Json -Compress
""".replace('TOKEN', token).replace('PAYLOAD', encoded).replace('EXPECTED', expected)
    command = base64.b64encode(script.encode('utf-16-le')).decode()
    result = subprocess.run(['ssh', '-S', str(control), '-F', str(prep / 'ssh_config'),
                             '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                             'choice-executor', 'powershell.exe', '-NoProfile', '-NonInteractive',
                             '-EncodedCommand', command], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=40)
    if result.returncode:
        raise SystemExit('SSH file test failed: ' + result.stderr[:500])
    report = json.loads(result.stdout.strip().removeprefix('\ufeff'))
    if report['sha256'] != expected or report.get('orders_enabled') is not False:
        raise SystemExit('Unexpected transfer receipt')
    report['local_at'] = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()
    (prep / 'file_channel_receipt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
