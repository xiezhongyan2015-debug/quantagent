"""Run one predeclared fixed/random producer in its registered process tree."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from quanta_agents.meta_v3 import registered_producer as producer

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['run','status'])
    p.add_argument('--root',required=True)
    args=p.parse_args()
    result=producer.dispatch(args.root) if args.command=='run' else producer.status(args.root)
    print(json.dumps(result,ensure_ascii=True))
    if result.get('exit_code',0)!=0:raise SystemExit(1)

if __name__=='__main__':main()
