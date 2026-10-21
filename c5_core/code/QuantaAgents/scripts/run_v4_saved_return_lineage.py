"""Run the fixed exposed-input lineage audit once, or read its saved status."""
import argparse
import json
from quanta_agents.meta_v3.saved_return_lineage_audit import run, status

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--run-output')
    group.add_argument('--status',action='store_true')
    args=parser.parse_args()
    result=run(args.run_output) if args.run_output else status()
    print(json.dumps({'subject':result['subject'],'state':result['state'],'event_count':result['event_count'],
                      'receipt':result['receipt'],'formal_target_success':False},ensure_ascii=False))
