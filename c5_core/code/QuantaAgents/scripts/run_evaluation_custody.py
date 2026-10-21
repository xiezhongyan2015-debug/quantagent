"""Freeze, consume once, or inspect the fixed exposed-account engineering audit.

This entry starts no research, source acquisition, service or formal evaluation.
Inspect and repeated run commands return existing custody without redispatch.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quanta_agents.meta_v3 import evaluation_custody as book


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('freeze', 'run', 'inspect'))
    parser.add_argument('identity')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    # Refuse a colliding output before freezing/consuming; stdout is always a
    # compact pointer. Large saved receipts are never dumped into the terminal.
    if args.output and args.output.exists():
        parser.error('output already exists; inspect the canonical record')
    result = getattr(book, args.action)(args.identity)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    print(json.dumps({key: result[key] for key in
                      ('evaluation_id', 'state', 'manifest_hash', 'event_head', 'formal_target_success')},
                     ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
