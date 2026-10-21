"""Independent SQL check of the fixed price*quantity precision diagnostic."""
from decimal import Decimal as D
from . import bounded_tail_pipeline as p
import duckdb


def main():
    lock=p.locked('verify_sina_cash_precision');directory=p.OUT/'sina_tail_minute_expanded'
    path=directory/'cash_precision_diagnostic.json';receipt=p.json.loads(path.read_text())
    assert receipt['source_code_sha256']==p.sha(p.ROOT/'gru_beta_neutral_20261008/diagnose_sina_cash_precision_bounded.py')
    assert not receipt['cash_gate_relaxed'] and not receipt['source_values_modified'] and not receipt['candidate_hours_admitted']
    con=duckdb.connect();con.execute("SET memory_limit='128MB'");con.execute('SET threads=1')
    actual={};tickers=sorted({r['ticker'] for r in receipt['rows']})
    for source in receipt['inputs']:
        file=p.Path(source['path']);assert p.identity(file)==source['verified_identity']
        date=file.parent.parent.name.split('=',1)[1]
        rows=con.execute('''WITH selected AS (SELECT *,CASE WHEN time_s BETWEEN 33900 AND 34200 THEN 570 WHEN time_s BETWEEN 41400 AND 41402 THEN 689 WHEN time_s=46800 THEN 780 WHEN time_s BETWEEN 54000 AND 54002 THEN 899 ELSE time_s//60 END AS minute FROM read_parquet(?) WHERE ticker=ANY(?) AND (time_s BETWEEN 33900 AND 33902 OR time_s BETWEEN 34200 AND 41402 OR time_s BETWEEN 46800 AND 54002)) SELECT ticker,minute,SUM(volume),SUM(price_x10000*volume),SUM(CAST(CAST(CAST(price_x10000 AS DOUBLE)/10000 AS FLOAT)*CAST(volume AS FLOAT) AS DOUBLE)) FROM selected GROUP BY ticker,minute''',[str(file),tickers]).fetchall()
        for ticker,minute,qty,cash,product in rows:
            key=date,ticker,int(minute);old=actual.setdefault(key,[0,0,0.]);old[0]+=int(qty);old[1]+=int(cash);old[2]+=float(product)
        assert p.identity(file)==source['verified_identity']
    paired=exact=within=0;max_diff=D(0)
    for row in receipt['rows']:
        qty,cash,product=actual[row['date'],row['ticker'],row['left_minute']]
        assert D(cash)/10000==D(row['exact_native_cash'])
        estimate=D(str(product)).quantize(D('.0001'))
        assert str(estimate)==row['rounded_predeclared_calculations']['product32']
        observed=D(row['source_amount']);difference=abs(observed-estimate)
        assert row['checks']['product32']==(difference==0)
        paired+=1;exact+=difference==0;within+=difference<=D('.0001');max_diff=max(max_diff,difference)
    assert paired==receipt['counts']['quantity_exact_paired_minutes'] and exact==receipt['counts']['product32']
    p.atomic_json(directory/'cash_precision_independent_verification.json',dict(passed=True,checked_utc=p.utc(),source_code_sha256=p.sha(__file__),audited_diagnostic_receipt_sha256=p.sha(path),paired_minutes=paired,source_amount_reproduced_by_float32_product_sum=exact,remaining_within_one_reported_cash_decimal=within-exact,max_source_difference_from_predeclared_float32_calculation=str(max_diff),source_cash_values_modified=False,cash_gate_relaxed=False,candidate_hours_admitted=0,source_implementation_not_certified=True,peak_rss_bytes=p.resource.getrusage(p.resource.RUSAGE_SELF).ru_maxrss))
    con.close();lock.close()


if __name__=='__main__':main()
