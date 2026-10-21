"""Predeclared Float32 price cash diagnostic; source numbers remain untouched."""
from decimal import Decimal as D
from collections import defaultdict,Counter
from . import bounded_tail_pipeline as p
from .stream_tail_minutes import labels


def main():
    lock=p.locked('sina_cash_precision')
    pilot_path=p.OUT/'sina_tail_minute_pilot/receipt.json';pilot=p.json.loads(pilot_path.read_text())
    groups=defaultdict(set);observed={};source_refs=[]
    for record in pilot['records']:
        if record['status']!='compared_without_admission':continue
        source=p.Path(record['path']);raw=source.read_bytes();assert p.identity(source)==record['verified_identity'] and p.hashlib.sha256(raw).hexdigest()==record['sha256']
        text=raw.decode();rows,_=p.json.JSONDecoder().raw_decode(text[text.index('['):])
        for r in rows:
            d=r['day'][:10];minute=int(r['day'][11:13])*60+int(r['day'][14:16])-1
            if d in ['2026-09-22','2026-09-23']:observed[d,record['symbol'][:6],minute]=r
        for date in ['2026-09-22','2026-09-23']:groups[date,record['symbol'][:2]].add(record['symbol'][:6])
    extraction=p.json.loads((p.OUT/'extraction.json').read_text());summaries={};inputs=[]
    for part in extraction['partitions']:
        key=part['date'],part['prefix'];wanted=groups.get(key)
        if not wanted:continue
        source=p.Path(part['selected_native_column_subset_path']);assert p.identity(source)==part['verified_identity']
        pf=p.pq.ParquetFile(source);col=pf.schema_arrow.get_field_index('ticker');rgs=[]
        for i in range(pf.metadata.num_row_groups):
            stats=pf.metadata.row_group(i).column(col).statistics
            assert stats and stats.has_min_max
            if any(stats.min<=ticker<=stats.max for ticker in wanted):rgs.append(i)
        input_rows=selected_rows=0
        for batch in pf.iter_batches(batch_size=p.BATCH_ROWS,row_groups=rgs,columns=p.CORE,use_threads=False):
            f=batch.to_pandas();input_rows+=len(f);f=f.loc[f.ticker.isin(wanted)].copy();selected_rows+=len(f)
            regular,minutes=labels(f.time_s.to_numpy(),'floor_left');f=f.loc[regular].copy();f['minute']=minutes[regular]
            if not len(f):continue
            f['exact_cash']=f.price_x10000*f.volume
            f['float32_price_cash']=(f.price_x10000.to_numpy()/10000).astype(p.np.float32).astype(p.np.float64)*f.volume.to_numpy()
            f['float64_price_cash']=f.price_x10000.to_numpy()/10000*f.volume.to_numpy()
            f['float32_product_cash']=((f.price_x10000.to_numpy()/10000).astype(p.np.float32)*f.volume.to_numpy().astype(p.np.float32)).astype(p.np.float64)
            aggregated=f.groupby(['ticker','minute']).agg(qty=('volume','sum'),exact_cash=('exact_cash','sum'),price32=('float32_price_cash','sum'),price64=('float64_price_cash','sum'),product32=('float32_product_cash','sum'))
            for (ticker,minute),row in aggregated.iterrows():
                target=summaries.setdefault((part['date'],ticker,int(minute)),dict(qty=0,exact_cash=0,price32=0.,price64=0.,product32=0.))
                for field in target:target[field]+=int(row[field]) if field in ['qty','exact_cash'] else float(row[field])
        assert p.identity(source)==part['verified_identity']
        inputs.append(dict(path=str(source),accepted_source_sha256=part['selected_native_column_subset_sha256'],verified_identity=part['verified_identity'],row_groups_read=rgs,input_batch_rows=input_rows,selected_stock_rows=selected_rows))
    counts=Counter();rows=[];unit=D('.0001')
    for key,actual in sorted(summaries.items()):
        ref=observed.get(key)
        if ref is None:continue
        if D(ref['volume'])!=actual['qty']:continue
        cash=D(ref['amount']);true=D(actual['exact_cash'])/10000
        checks={'exact_decimal_cash':cash==true,**{f:cash==D(str(actual[f])).quantize(unit) for f in ['price32','price64','product32']}}
        for field,value in checks.items():counts[field]+=value
        counts['quantity_exact_paired_minutes']+=1
        rows.append(dict(date=key[0],ticker=key[1],left_minute=key[2],source_amount=str(cash),exact_native_cash=str(true),rounded_predeclared_calculations={f:str(D(str(actual[f])).quantize(unit)) for f in ['price32','price64','product32']},checks=checks))
    dest=p.OUT/'sina_tail_minute_expanded';dest.mkdir(exist_ok=True)
    p.atomic_json(dest/'cash_precision_diagnostic.json',dict(checked_utc=p.utc(),source_code_sha256=p.sha(__file__),pilot_receipt_sha256=p.sha(pilot_path),predeclared_methods=['exactDecimal price_x10000*Qty','float32(price_x10000/10000) promoted toFloat64 times exactQty','float64Price timesQty','float32 price timesfloat32 Qty product promoted toFloat64'],source_values_modified=False,source_upstream_independence_inferred=False,cash_gate_relaxed=False,candidate_hours_admitted=0,counts=dict(counts),inputs=inputs,rows=rows,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    print(dict(counts));lock.close()


if __name__=='__main__':main()
