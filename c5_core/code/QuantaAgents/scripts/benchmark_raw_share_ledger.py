"""Bounded synthetic accounting load; no market reads or model calls."""
import argparse
import cProfile
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import pstats
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', default='experiment_traces/meta_ashare_revision5')
    parser.add_argument('--label', required=True)
    args = parser.parse_args()
    stage = (ROOT / args.stage).resolve()
    sys.path.insert(0, str(stage / 'src'))
    from quanta_agents.raw_share_ledger import RawShareLedger
    out = ROOT / 'experiment_traces/meta_raw_ledger_performance' / args.label
    out.mkdir(parents=True, exist_ok=False)
    assumption = {'scenarios': [100, 500, 1500], 'per_scenario_seconds': 20, 'quantity': 100,
                  'same_stock_many_purchase_lots_then_sales': True, 'fees': '0.00',
                  'no_profitability_interpretation': True}
    assumption_hash = hashlib.sha256(json.dumps(assumption, sort_keys=True).encode()).hexdigest()
    source = {'ref': 'synthetic://bounded-raw-ledger-load', 'sha256': assumption_hash,
              'verified': True, 'evidence_type': 'simulated', 'assumption_ref': str(out / 'plan.json'),
              'assumption_sha256': assumption_hash}
    (out / 'plan.json').write_text(json.dumps(assumption, indent=2), encoding='utf-8')
    profile = cProfile.Profile()
    results = []
    for n in assumption['scenarios']:
        ledger = RawShareLedger(['2019-06-24','2019-06-25','2019-06-26'], calendar_source=source,
                                calendar_available_at='2019-06-24T08:00:00+08:00')
        def event(i,kind,at,data):
            item={'event_id':i,'kind':kind,'effective_at':at,'available_at':at,'source':source,'data':data}
            return ledger.apply(item,as_of=at)
        event('cash','cash_deposit','2019-06-24T08:00:00+08:00',{'amount':'10000000.00'})
        start=time.perf_counter()
        buys=sells=0
        if n==500: profile.enable()
        for i in range(n):
            if time.perf_counter()-start>20: break
            event(f'b{i}','buy_fill','2019-06-24T09:30:00+08:00',
                  {'symbol':'sz000001','quantity':100,'raw_price':'10.00','fees':'0.00'})
            buys+=1
        if buys==n:
            for i in range(n):
                if time.perf_counter()-start>20: break
                event(f's{i}','sell_fill','2019-06-25T09:30:00+08:00',
                      {'symbol':'sz000001','quantity':100,'raw_price':'10.00','fees':'0.00','lot_allocations':{f'b{i}':100}})
                sells+=1
        if n==500: profile.disable()
        elapsed=time.perf_counter()-start
        snap=ledger.snapshot()
        results.append({'planned_buys':n,'planned_sells':n,'buys':buys,'sells':sells,'elapsed_seconds':elapsed,
                        'complete':buys==sells==n,'event_count':snap['event_count'],
                        'profiled':n==500,'state_json_bytes':len(json.dumps(snap).encode()),
                        'cash_reconciles':snap['cash']==f'{10000000-(buys-sells)*1000:.2f}'})
        assert results[-1]['cash_reconciles']
        print(json.dumps(results[-1]),flush=True)
    profile.dump_stats(str(out/'profile.pstats'))
    with (out/'profile.txt').open('w',encoding='utf-8') as stream:
        pstats.Stats(profile,stream=stream).sort_stats('cumtime').print_stats(22)
    result={'created_at':datetime.now(timezone.utc).isoformat(),'stage':str(stage),'scenarios':results,
            'source_sha256':hashlib.sha256((stage/'src/quanta_agents/raw_share_ledger.py').read_bytes()).hexdigest(),
            'benchmark_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'model_calls':0,'market_data_reads':0,'execution_valid':False}
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
