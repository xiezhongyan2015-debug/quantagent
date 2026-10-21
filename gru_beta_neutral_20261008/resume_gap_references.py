"""Fresh bounded public diagnostics for the 40 unresolved coarse references.

Use existing native candidates first, preserve wire responses and error stacks.
Unauthenticated public protocol responses remain corroboration, not admission.
"""
from decimal import Decimal as D
import importlib.util
import socket
import traceback
from . import bounded_tail_pipeline as p


def load(name):
    path = p.ROOT / 'gru_beta_neutral_20261008' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    p.sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    process_lock = p.locked('gap_references')
    public = load('public_baostock_probe')
    native = load('query_all_unchecked_minimum_coarse_references')
    source_lock = (p.ROOT / 'acquired_data/data_round2/baostock_session.lock').open('a')
    p.fcntl.flock(source_lock, p.fcntl.LOCK_EX | p.fcntl.LOCK_NB)
    source = p.BASE / 'public_source_probes/additional_public_archives_20261009/wind17/all_minimum_gap_consolidation'
    prior = source / 'all_unchecked_coarse_followup/all_unchecked_native_eligible_hour_coarse_acquisition.json'
    old = p.json.loads(prior.read_text())
    destination = p.OUT / 'historical_gap_references'
    destination.mkdir(exist_ok=True)
    op = destination / 'receipt.json'
    d = p.json.loads(op.read_text()) if op.exists() else dict(started_utc=p.utc(), source_code_sha256=p.sha(__file__),
        original_scope_receipt_sha256=p.sha(prior), requested_hours=40, requested_stockdays=31,
        maximum_new_sessions=2, queries=[], results=[], sessions=[], candidate_hours_admitted=0,
        public_protocol_responses_are_corroboration_only=True, status='querying')
    if d.get('status') == 'complete':
        process_lock.close()
        return
    done = {(r['symbol'],r['date']) for r in d['results']}
    requests = old['requested_stockdays']
    assert len(requests) == 31
    start = p.time.monotonic()
    for session in range(len(d['sessions']), 2):
        if len(done) == 31:
            break
        directory = destination / ('session_' + str(session + 1))
        directory.mkdir(exist_ok=True)
        record = dict(started_utc=p.utc(), stage='connect')
        d['sessions'].append(record)
        p.atomic_json(op,d)
        client = None
        try:
            client = public.PublicClient(directory)
            client.socket.settimeout(8)
            record['stage'] = 'anonymous_public_login'
            client.login()
            for request in requests:
                symbol, date = request['symbol'], request['date']
                if (symbol,date) in done:
                    continue
                if p.time.monotonic()-start > 360:
                    raise TimeoutError('Bounded 360s diagnostic session budget reached')
                path = p.Path(request['raw_path'])
                assert p.sha(path) == request['raw_sha256']
                bars = native.normalize(path,date)
                record.update(stage='query', symbol=symbol, date=date)
                q = client.query(symbol[-2:].lower()+'.'+symbol[:6],date,date,'5')
                d['queries'].append({k:v for k,v in q.items() if k!='data'})
                payload = q.get('data',[])
                wire_rows = payload.get('record',[]) if isinstance(payload,dict) else payload
                references = [dict(zip(q['fields'],row)) for row in wire_rows] if q['error_code']=='0' else []
                result = dict(symbol=symbol,date=date,slots=request['slots'],reference_rows=len(references),
                    wire_path=q['wire_path'],wire_sha256=q['sha256'],native_source_path=str(path),
                    native_source_sha256=request['raw_sha256'],strict_coarse_pass_slots=[])
                if len(references)==48:
                    assert [int(r['time'][8:10])*60+int(r['time'][10:12]) for r in references] == native.GRID[4::5]
                    comparisons=[]
                    for i,row in enumerate(references):
                        block=bars[i*5:(i+1)*5]
                        actual=dict(open=block[0]['open'],high=max(r['high'] for r in block),
                            low=min(r['low'] for r in block),close=block[-1]['close'],volume=sum(r['volume'] for r in block),
                            amount=sum(r['amount'] for r in block))
                        eq={f:actual[f]==D(row[f]) for f in (*native.PRICE,'volume','amount')}
                        passed=all(eq[f] for f in native.PRICE) and abs(actual['volume']-D(row['volume']))<=100
                        comparisons.append(dict(end_minute=native.GRID[i*5+4],exact=eq,strict_gate_pass=passed,
                            actual={f:str(v) for f,v in actual.items()},reference={f:row[f] for f in actual}))
                    result.update(status='complete_reference_diagnostic',comparisons=comparisons,
                        strict_coarse_pass_slots=[s for s in request['slots'] if all(r['strict_gate_pass'] for r in comparisons[s*12:(s+1)*12])])
                else:
                    result['status']='no_complete_reference'
                d['results'].append(result)
                done.add((symbol,date))
                d.update(updated_utc=p.utc(),completed_stockdays=len(done))
                p.atomic_json(op,d)
                p.time.sleep(0.5)
            record['status']='complete'
        except Exception as e:
            record.update(status='failed_preserved',error_type=type(e).__name__,error=str(e),traceback=traceback.format_exc())
        finally:
            if client is not None:
                try:
                    client.socket.settimeout(3)
                    client.close()
                except Exception as e:
                    record['logout_error']=str(e)
            record['finished_utc']=p.utc()
            p.atomic_json(op,d)
    d.update(status='complete' if len(done)==31 else 'bounded_attempts_incomplete_preserved',finished_utc=p.utc(),
        hours_with_complete_references=sum(len(r['slots']) for r in d['results'] if r['status']=='complete_reference_diagnostic'),
        strict_coarse_diagnostic_hours=sum(len(r['strict_coarse_pass_slots']) for r in d['results']),
        historical_missing_hours_remaining=3042,tail_missing_hours_remaining=84860)
    p.atomic_json(op,d)
    source_lock.close()
    process_lock.close()


if __name__=='__main__':
    main()
