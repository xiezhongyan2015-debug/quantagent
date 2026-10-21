"""Verify downloaded footers and quantify scope limits without claiming bar QA."""
import sys,json,hashlib
from pathlib import Path
from public_hf_backfill import ROOT,OUT,REVISION,index
sys.path.insert(0,str(ROOT/'gru_research/.deps'))
import pyarrow.parquet as pq
import pandas as pd
import numpy as np


def main():
    symbols=json.loads((ROOT/'gru_research/artifacts/dataset/symbols.json').read_text())
    inventory=pd.read_csv(OUT/'snapshot/metadata/coverage_by_instrument.csv',dtype={'symbol':str})
    inventory['ticker']=inventory.symbol+'.'+inventory.exchange;inventory=inventory.set_index('ticker')
    rows=[]
    for symbol in symbols:
        relative=f'data/stock_1m/{symbol[-2:]}/{symbol[:6]}.parquet';path=OUT/'snapshot'/relative
        f=pq.ParquetFile(path);declared=inventory.loc[symbol]
        assert f.metadata.num_rows==declared.records and f.schema.names==['symbol','exchange','timestamp','open','high','low','close','volume','turnover']
        first=f.metadata.row_group(0).column(2).statistics.min
        last=f.metadata.row_group(f.metadata.num_row_groups-1).column(2).statistics.max
        assert pd.Timestamp(first)==pd.Timestamp(declared.first_timestamp) and pd.Timestamp(last)==pd.Timestamp(declared.last_timestamp)
        rows.append({'symbol':symbol,'file':relative,'file_bytes':path.stat().st_size,'expected_lfs_sha256':index()[relative]['lfs']['sha256'],
                     'actual_rows':f.metadata.num_rows,'actual_row_groups':f.metadata.num_row_groups,
                     'first_timestamp':str(first),'last_timestamp':str(last),'listing_date':declared.listing_date,
                     'delisting_date':declared.delisting_date,'metadata_verified_missing_days':declared.verified_missing_trading_days,
                     'metadata_unresolved_days':declared.unresolved_no_bar_days})
    frame=pd.DataFrame(rows);frame.to_csv(OUT/'verified_union_file_inventory.csv',index=False)
    requests=pd.read_csv(ROOT/'outputs/gru_beta_neutral_20261008/backfill_requests.csv')
    calendar=json.loads((ROOT/'gru_research/artifacts/minute_calendar.json').read_text())['dates']
    times=np.load(ROOT/'gru_research/artifacts/dataset/times.npy');members=np.load(ROOT/'gru_research/artifacts/dataset/members.npy')[::4]
    symbol_index={s:i for i,s in enumerate(symbols)};tail=[]
    # Aug 7 is a partial global snapshot day; complete published days end Aug 6.
    for item in requests.to_dict('records'):
        days=[d for d in calendar if max(item['requested_start'],'2026-08-07')<=d<=item['requested_end']]
        if not days:continue
        absent=[d for d in days if not members[calendar.index(d),symbol_index[item['symbol']]]]
        tail.append({'symbol':item['symbol'],'entry_date':item['entry_date'],'requested_end':item['requested_end'],
                     'tail_required_days':len(days),'tail_days_already_member_source_expected':len(days)-len(absent),
                     'tail_nonmember_days_not_in_either_source':len(absent)})
    pd.DataFrame(tail).to_csv(OUT/'minimum_request_tail_scope.csv',index=False)
    receipt={'status':'download_integrity_and_file_inventory_verified','revision':REVISION,
             'verified_files':len(frame),'verified_bytes':int(frame.file_bytes.sum()),'verified_rows':int(frame.actual_rows.sum()),
             'all_symbols_in_original_union_present':True,'footer_counts_and_ranges_match_inventory':True,
             'latest_complete_snapshot_day':'2026-08-06','minimum_requested_tail_interval_count':len(tail),
             'minimum_requested_tail_nonmember_stockdays':sum(r['tail_nonmember_days_not_in_either_source'] for r in tail),
             'full_bar_price_volume_and_status_audit_complete':False,'new_panel_built':False,
             'provider_missing_day_counts_are_not_independently_verified':True}
    (OUT/'verified_union_inventory_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print(json.dumps(receipt,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
