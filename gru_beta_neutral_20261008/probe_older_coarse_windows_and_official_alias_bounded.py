"""Bounded public coarse diagnostics for old-code and older frequency availability.

No package installation, authenticated entitlement, GUI, or minute synthesis.
Each reply/error stack is preserved. New coarse bars are only references.
"""
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
from .resume_gap_references import load


def main():
    lock=p.locked('probe_older_coarse_and_official_alias')
    destination=p.OUT/'older_coarse_frequency_and_alias_probe';destination.mkdir(exist_ok=True)
    output=destination/'receipt.json';assert not output.exists()
    source_path=p.OUT/'historical_quote_candidate_recheck/receipt.json';source=p.json.loads(source_path.read_text())
    proof_path=source_path.parent/'independent_verification.json';proof=p.json.loads(proof_path.read_text())
    assert proof['passed'] and proof['audited_receipt_sha256']==p.sha(source_path)
    by_day={(r['symbol'],r['date']):r for r in source['results']}
    bridge_path=p.BASE/'public_source_probes/baostock/old_code_factor_identity_20261009/bridge_validation_receipt.json'
    bridge=p.json.loads(bridge_path.read_text());alias=next(r for r in bridge['records'] if r['symbol']=='001914.SZ')
    assert alias['official_code_change_date']=='2019-12-16' and alias['historical_code']=='sz.000043'
    assert p.sha(alias['official_statement_path'])==alias['official_statement_sha256']
    requests=[('001914.SZ','2019-12-13','sz.000043','5'),
        ('600517.SH','2019-01-02','sh.600517','60'),('002595.SZ','2019-12-17','sz.002595','60'),
        ('002595.SZ','2019-12-17','sz.002595','15'),('600167.SH','2018-01-29','sh.600167','60'),
        ('601019.SH','2021-01-12','sh.601019','60')]
    receipt=dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__),historical_source_receipt_sha256=p.sha(source_path),
        official_alias_bridge_receipt_sha256=p.sha(bridge_path),official_old_code_statement_sha256=alias['official_statement_sha256'],
        official_code_change_date=alias['official_code_change_date'],anonymous_public_requests_only=True,authenticated_credentials_used=False,
        doc_urls=['https://baostock.com/baostock/index.php/A%E8%82%A1K%E7%BA%BF%E6%95%B0%E6%8D%AE','https://github.com/zxygithub/baostock/blob/master/docs/pythonAPI.md'],
        requested_queries=[dict(symbol=s,date=d,code=c,frequency=f) for s,d,c,f in requests],results=[],
        candidate_hours_admitted=0,coarse_references_never_converted_to_minutes=True,source_values_changed=False,status='querying')
    p.atomic_json(output,receipt);public=load('public_baostock_probe');client=None
    session_lock=(p.ROOT/'acquired_data/data_round2/baostock_session.lock').open('a')
    p.fcntl.flock(session_lock,p.fcntl.LOCK_EX|p.fcntl.LOCK_NB)
    try:
        client=public.PublicClient(destination/'wire');client.socket.settimeout(8);client.login();started=p.time.monotonic()
        for symbol,date,code,frequency in requests:
            assert p.time.monotonic()-started<120
            if frequency=='5':query=client.query(code,date,date,frequency)
            else:
                assert frequency in ['15','60'] and code.startswith(('sz.','sh.'))
                fields='date,time,code,open,high,low,close,volume,amount,adjustflag'
                body=public.SEP.join(['query_history_k_data_plus','anonymous','1','2000',code,fields,date,date,frequency,'3'])
                query=client.exchange('95',body,f'{code}_{date}_{date}_{frequency}_p1')
            payload=query.get('data',[]);payload=payload.get('record',[]) if isinstance(payload,dict) else payload
            result=dict(symbol=symbol,date=date,query_code=code,frequency=frequency,error_code=query['error_code'],error_msg=query['error_msg'],
                rows=len(payload),wire_path=query['wire_path'],wire_sha256=query['sha256'],complete_reference=False,strict_reference_slots=[])
            if payload:
                assert query['code']==code and query['start']==query['end']==date and query['adjustflag']=='3' and query['frequency']==frequency
                width=int(frequency);expected=240//width
                if len(payload)==expected:
                    original=by_day.get((symbol,date));selected_path=p.Path(original['native_selected_path']) if original else p.OUT/'historical_quote_candidate_recheck/native_selected_days'/(symbol+'_'+date+'.json')
                    if selected_path.exists():
                        data=selected_path.read_bytes();digest=p.hashlib.sha256(data).hexdigest()
                        if original:assert digest==original['native_selected_sha256']
                        selected=p.json.loads(data);bars=[{f:D(v) for f,v in row.items()} for row in selected['normalized_bars']]
                        end_grid=list(range(571,691))+list(range(781,901));comparisons=[]
                        for i,row in enumerate(payload):
                            ref=dict(zip(query['fields'],row));end=int(ref['time'][8:10])*60+int(ref['time'][10:12]);assert end==end_grid[(i+1)*width-1]
                            block=bars[i*width:(i+1)*width]
                            agg=dict(open=block[0]['open'],high=max(b['high'] for b in block),low=min(b['low'] for b in block),close=block[-1]['close'],volume=sum(b['volume'] for b in block),amount=sum(b['amount'] for b in block))
                            delta={f:agg[f]-D(ref[f]) for f in agg};strict=all(delta[f]==0 for f in ['open','high','low','close']) and abs(delta['volume'])<=100
                            comparisons.append(dict(end_minute=end,strict_price_volume_gate=strict,differences={f:str(v) for f,v in delta.items()}))
                        per_hour=60//width
                        result.update(complete_reference=True,native_selected_path=str(selected_path),native_selected_sha256=digest,comparisons=comparisons,
                            strict_reference_slots=[slot for slot in range(4) if all(r['strict_price_volume_gate'] for r in comparisons[slot*per_hour:(slot+1)*per_hour])])
            receipt['results'].append(result);receipt['updated_utc']=p.utc();p.atomic_json(output,receipt);p.time.sleep(.5)
        receipt['status']='complete_bounded_reference_diagnostic'
    except Exception as exc:
        receipt.update(status='bounded_probe_failed_preserved',error_type=type(exc).__name__,error=str(exc),traceback=p.traceback.format_exc())
    finally:
        if client is not None:
            try:client.socket.settimeout(3);client.close()
            except Exception as exc:receipt['logout_error']=dict(error_type=type(exc).__name__,error=str(exc),traceback=p.traceback.format_exc())
        session_lock.close();receipt.update(finished_utc=p.utc(),peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss)
        p.atomic_json(output,receipt);lock.close()


if __name__=='__main__':main()
