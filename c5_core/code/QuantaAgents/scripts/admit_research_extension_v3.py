"""Apply a controller-written admission file; never launch a research worker."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from quanta_agents.meta_v3.closing import ClosingPolicy
from quanta_agents.meta_v3.ledger import need
from quanta_agents.meta_v3.research_extension import decide_extension


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent',required=True)
    parser.add_argument('--task',required=True)
    parser.add_argument('--request',required=True)
    parser.add_argument('--admission',required=True)
    args=parser.parse_args()
    path=Path(args.admission).resolve()
    need(path.stat().st_size<=128*1024**2,'controller admission input byte bound')
    config=json.loads(path.read_text(encoding='utf-8'))
    need(set(config)=={'decision','child_case','policy','deadline_epoch'},'exact admission input fields')
    if config['policy'] is not None:config['policy']=ClosingPolicy(**config['policy'])
    result=decide_extension(args.parent,args.task,args.request,**config)
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':main()
