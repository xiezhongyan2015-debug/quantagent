"""Check uploaded parts, reconstruct the archive, and unpack the real panel."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    args.destination.mkdir(parents=True, exist_ok=True)
    archive = args.destination / 'dataset_received.tar.gz'
    whole = hashlib.sha256()
    with archive.open('wb') as output:
        for part in manifest['parts']:
            if Path(part['name']).name != part['name'] or not part['name'].startswith('gru_dataset.part'):
                raise ValueError('Invalid uploaded part name')
            digest, count = hashlib.sha256(), 0
            with args.manifest.parent.joinpath(part['name']).open('rb') as source:
                for block in iter(lambda: source.read(8 * 1024**2), b''):
                    output.write(block)
                    whole.update(block)
                    digest.update(block)
                    count += len(block)
            if count != part['bytes'] or digest.hexdigest() != part['sha256']:
                raise ValueError('Upload verification failed: ' + part['name'])
    if whole.hexdigest() != manifest['archive_sha256']:
        raise ValueError('Combined archive differs from the local audited data')
    with tarfile.open(archive, 'r:gz') as stream:
        for member in stream:
            pieces = Path(member.name).parts
            if len(pieces) != 2 or pieces[0] != 'dataset' or not member.isfile() or '..' in pieces:
                raise ValueError('Unexpected dataset archive member')
            target = args.destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with stream.extractfile(member) as source, target.open('wb') as output:
                for block in iter(lambda: source.read(8 * 1024**2), b''):
                    output.write(block)
    meta = json.loads((args.destination / 'dataset/meta.json').read_text())
    if meta['data_fingerprint'] != manifest['data_fingerprint']:
        raise ValueError('Reconstructed dataset differs from the selected local dataset')
    print(json.dumps({'event': 'dataset_received', 'verified_parts': len(manifest['parts']),
                      'data_fingerprint': meta['data_fingerprint'], 'bytes': manifest['bytes']}), flush=True)


if __name__ == '__main__':
    main()
