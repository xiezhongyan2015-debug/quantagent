"""Verify the uploaded production panel and preserve a launch receipt."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import platform
import subprocess
import time


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    meta = json.loads((args.dataset / 'meta.json').read_text())
    if meta.get('production_ready') is not True or meta['causality_audit'].get('passed') is not True:
        raise ValueError('Production data audit is incomplete')
    for filename, expected in meta['files_sha256'].items():
        if sha256(args.dataset / filename) != expected:
            raise ValueError('Uploaded dataset file changed: ' + filename)
    fingerprint = hashlib.sha256(json.dumps(meta['files_sha256'], sort_keys=True).encode()).hexdigest()
    if fingerprint != meta['data_fingerprint']:
        raise ValueError('Dataset fingerprint is inconsistent')
    tests = json.loads((args.dataset / 'dataset_audit.json').read_text())['source_tests_receipt']
    root = Path(__file__).resolve().parent
    for filename in ['model.py', 'train.py', 'evaluate.py']:
        if sha256(root / filename) != tests['source_code_sha256'][filename]:
            raise ValueError('Training code changed after correctness tests: ' + filename)
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('Allocated compute node does not expose CUDA')
    receipt = {'verified_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
               'slurm_job_id': os.environ.get('SLURM_JOB_ID'), 'host': platform.node(),
               'dataset_fingerprint': fingerprint, 'verified_files': len(meta['files_sha256']),
               'source_code_sha256': {n: sha256(root / n) for n in ['model.py', 'train.py', 'evaluate.py', 'config.json']},
               'torch': torch.__version__, 'cuda': torch.version.cuda,
               'gpu_name': torch.cuda.get_device_name(0),
               'gpu_total_bytes': torch.cuda.get_device_properties(0).total_memory,
               'nvidia_smi': subprocess.check_output(['nvidia-smi', '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader'], text=True).strip(),
               'passed': True}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'remote_launch_verification.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'event': 'remote_launch_verified', **receipt}), flush=True)


if __name__ == '__main__':
    main()
