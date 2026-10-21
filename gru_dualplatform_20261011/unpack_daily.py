"""Verify every uploaded part and archive before extracting the research panel."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import tarfile


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def main():
    root = Path.cwd().resolve()
    upload = Path(os.environ.get('DAILY_UPLOAD', root / 'upload'))
    receipt = json.loads((upload / 'upload_manifest.json').read_text())
    archive = upload / 'daily-panel.tar.gz'
    digest = hashlib.sha256()
    with archive.with_suffix('.tmp').open('wb') as output:
        for part in receipt['parts']:
            name = part['name']
            assert Path(name).name == name
            path = upload / name
            assert path.stat().st_size == part['size'] and sha(path) == part['sha256'], name
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(8 * 1024**2), b''):
                    output.write(block)
                    digest.update(block)
    assert digest.hexdigest() == receipt['archive_sha256']
    archive.with_suffix('.tmp').replace(archive)
    expected_parent = root / 'outputs/gru_dualplatform_20261011/daily_panel'
    destination = Path(os.environ.get('DAILY_PANEL', expected_parent)).resolve()
    assert not destination.exists(), 'Panel already exists; audit it instead of overwriting'
    with tarfile.open(archive, 'r:gz') as packed:
        for item in packed.getmembers():
            assert item.isfile() and (root / item.name).resolve().parent == expected_parent
        destination.mkdir(parents=True)
        for item in packed.getmembers():
            with packed.extractfile(item) as source, (destination/Path(item.name).name).open('wb') as output:
                shutil.copyfileobj(source,output,8*1024**2)
    from .audit_daily import audit
    audit()
    meta = json.loads((destination / 'meta.json').read_text())
    assert meta['data_fingerprint'] == receipt['data_fingerprint']
    print('REMOTE PANEL VERIFIED', receipt['data_fingerprint'], flush=True)


if __name__ == '__main__':
    main()
