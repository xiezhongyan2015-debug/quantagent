"""Recheck fixed clocks against immutable raw minutes and public 5m controls."""
from . import bounded_tail_pipeline as p
from . import stream_tail_minutes as m


def compare(actual, reference):
    common = actual.index.intersection(reference.index).sort_values()
    a, b = actual.loc[common], reference.loc[common]
    eq = p.np.isclose(a[list(m.PRICE)].to_numpy(float), b[list(m.PRICE)].to_numpy(float),rtol=0,atol=1e-8)
    qty = a.volume.to_numpy(p.np.int64)-b.volume.to_numpy(p.np.int64)
    strict = eq.all(axis=1) & (abs(qty)<=100)
    return dict(common_intervals=len(common), all_four_prices_exact_intervals=int(eq.all(axis=1).sum()),
        exact_by_price={k:int(eq[:,i].sum()) for i,k in enumerate(m.PRICE)}, exact_volume_intervals=int((qty==0).sum()),
        original_price_and_volume_gate_pass_intervals=int(strict.sum()), missing_actual_intervals=[int(i) for i in reference.index.difference(actual.index)],
        failed_intervals=[int(i) for i,flag in zip(common,strict) if not flag], all_reference_intervals_passed=bool(len(common)==len(reference) and strict.all()))


def main():
    report = dict(started_utc=p.utc(),source_code_sha256=p.sha(__file__), fixed_phase_end_grace_seconds=2,
                  controls=[],candidate_hours_admitted=0,clock_selection='none_until_all_predeclared_controls_pass')
    previous = p.json.loads((p.SOURCE/'actual_trade_sample_diagnostics.json').read_text())
    aggregates = {}
    for payload in previous['whole_payloads']:
        file = p.Path(payload['selection_path'])
        assert p.sha(file)==payload['selection_sha256']
        ag = m.Aggregate()
        pf = p.pq.ParquetFile(file)
        for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,columns=p.CORE,use_threads=False):
            ag.consume(batch)
        for code in ag.raw:
            aggregates[code] = ag
    for code in ['000006','000099','600004']:
        prefix = 'sh' if code=='600004' else 'sz'
        coarsepath = (p.BASE/'public_source_probes/baostock' if code=='600004' else p.SOURCE/'sample_references') / (prefix+'.'+code+'_2026-09-23_2026-09-23_5_p1.json')
        q=p.json.loads(coarsepath.read_text())
        assert p.sha(q['wire_path'])==q['sha256'] and q['error_code']=='0' and q['adjustflag']=='3'
        rows=q['data'].get('record',[]) if isinstance(q['data'],dict) else q['data']
        ref=p.pd.DataFrame(rows,columns=q['fields'])
        assert len(ref)==48
        clock=p.pd.to_datetime(ref.time,format='%Y%m%d%H%M%S%f')
        ref.index=clock.dt.hour.to_numpy()*60+clock.dt.minute.to_numpy()-5
        ref[list(m.PRICE)]=ref[list(m.PRICE)].astype(float)
        ref['volume']=ref.volume.astype(p.np.int64)
        original=None
        if code=='600004':
            file=p.BASE/'public_source_probes/original_recent_sina_control_20261009/600004_20260923.parquet'
            original=p.pq.ParquetFile(file).read().to_pandas()
            dt=p.pd.to_datetime(original.datetime)
            original.index=dt.dt.hour.to_numpy()*60+dt.dt.minute.to_numpy()
            assert original.index.tolist()==m.GRID
            report['original_minute_source']=dict(path=str(file),sha256=p.sha(file))
        result=dict(symbol=code+'.'+('SH' if prefix=='sh' else 'SZ'),date='2026-09-23',models={},reference_path=str(coarsepath),reference_sha256=p.sha(coarsepath))
        for model in m.MODELS:
            frame=aggregates[code].frame(model,'2026-09-23')
            frame=frame[frame.ticker.eq(code)].set_index('left_minute')
            frame[list(m.PRICE)]=frame[list(m.PRICE)]/10000
            minute=compare(frame,original) if original is not None else None
            work=frame.copy()
            work['bucket']=work.index//5*5
            five=work.groupby('bucket').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum'))
            result['models'][model]=dict(minute=minute,coarse=compare(five,ref),empty_minutes_retained_null=True)
        report['controls'].append(result)
    report.update(finished_utc=p.utc(),all_controls_passed_by_clock={model:all(r['models'][model]['coarse']['all_reference_intervals_passed'] and
        (r['models'][model]['minute'] is None or r['models'][model]['minute']['all_reference_intervals_passed']) for r in report['controls']) for model in m.MODELS},
        independent_reference_concordance_required_before_candidate_admission=True)
    p.atomic_json(p.OUT/'minute_control_verification.json',report)


if __name__=='__main__':
    main()
