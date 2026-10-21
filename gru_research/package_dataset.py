"""Package only audited deliverables into small Jupyter-uploadable parts."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tarfile


class PartWriter:
    def __init__(self, output, size):
        self.output, self.size = output, size
        self.number, self.written = 0, 0
        self.file, self.digest = None, None
        self.parts = []
        self.archive_digest = hashlib.sha256()

    def write(self, data):
        self.archive_digest.update(data)
        original = len(data)
        while data:
            if self.file is None:
                self.path = self.output / f'gru_dataset.part{self.number:03d}'
                self.file = self.path.open('wb')
                self.digest = hashlib.sha256()
                self.written = 0
            n = min(len(data), self.size - self.written)
            self.file.write(data[:n])
            self.digest.update(data[:n])
            self.written += n
            data = data[n:]
            if self.written == self.size:
                self.finish_part()
        return original

    def flush(self):
        if self.file:
            self.file.flush()

    def finish_part(self):
        if self.file:
            self.file.close()
            self.parts.append({'name': self.path.name, 'bytes': self.written, 'sha256': self.digest.hexdigest()})
            self.number += 1
            self.file = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    meta = json.loads((args.dataset / 'meta.json').read_text())
    if meta.get('production_ready') is not True:
        raise ValueError('Assemble the audited production dataset first')
    args.output.mkdir(parents=True, exist_ok=True)
    writer = PartWriter(args.output, 64 * 1024**2)
    with gzip.GzipFile(fileobj=writer, mode='wb', compresslevel=1, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w|') as archive:
            for name in sorted(meta['files_sha256']) + ['meta.json']:
                archive.add(args.dataset / name, arcname='dataset/' + name)
    writer.finish_part()
    manifest = {'data_fingerprint': meta['data_fingerprint'], 'compression': 'gzip',
                'parts': writer.parts, 'archive_sha256': writer.archive_digest.hexdigest(),
                'bytes': sum(p['bytes'] for p in writer.parts)}
    (args.output / 'gru_dataset_transfer.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest), flush=True)


if __name__ == '__main__':
    main()
