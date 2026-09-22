"""Example plugin protocol. Generates a target file only; never logs in or trades.

Replace build_targets with your team's model. Do not select this demonstration
as a production strategy unless its intentionally simple portfolio is desired.
"""
import argparse
import json
from pathlib import Path


def build_targets(account):
    # Demonstration only: 3% of total NAV in one stock, all other targets zero.
    return [dict(code='SH600000',weight=.03)]


def main():
    ap=argparse.ArgumentParser()
    for flag in ('execution-date','signal-date','account-snapshot','output'):ap.add_argument('--'+flag,required=True)
    args=ap.parse_args();account=json.loads(Path(args.account_snapshot).read_text(encoding='utf-8-sig'))
    result=dict(strategy_id='example_only',execution_date=args.execution_date,signal_date=args.signal_date,
                mode='weights',targets=build_targets(account))
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
