"""Reconcile the original3042-hour scope with the fully audited latest raw mask."""
import csv
import io
from collections import Counter, defaultdict
from . import bounded_tail_pipeline as p


def main():
    destination=p.OUT/'historical_remaining_after_v14';destination.mkdir(exist_ok=True)
    receipt_path=destination/'receipt.json';assert not receipt_path.exists()
    raw=p.BASE/'backfilled_panel_candidate_historical_partial_reported_v14_raw'
    audit_path=raw/'raw_full_history_audit.json';audit=p.json.loads(audit_path.read_text());assert audit['passed'] and audit['stocks_checked']==1178
    meta_path=raw/'dataset/meta.json';meta=p.json.loads(meta_path.read_text());assert audit['audited_raw_meta_sha256']==p.sha(meta_path)
    identities={r['name']:r['verified_identity'] for r in audit['arrays']};assert p.identity(raw/'dataset/hour_complete.npy')==identities['hour_complete.npy']
    mask=p.np.load(raw/'dataset/hour_complete.npy',mmap_mode='r');calendar=p.json.loads((p.ROOT/'gru_research/artifacts/minute_calendar.json').read_text())
    date_index={d:i for i,d in enumerate(calendar['dates'])};symbol_index={s:i for i,s in enumerate(calendar['union_symbols'])}
    source=p.OUT/'historical_quote_candidate_recheck/all_3042_hour_priority.csv';data=source.read_bytes()
    reader=csv.DictReader(io.StringIO(data.decode('utf-8')));fields=reader.fieldnames;rows=list(reader);assert len(rows)==3042
    additions=['latest_raw_hour_complete','latest_raw_candidate','current_remaining_reason']
    all_rows=[];remaining=[];admitted=[];strict=[];keys=set();counts=Counter();stockdays=defaultdict(list)
    for original in rows:
        symbol,date,slot=original['symbol'],original['date'],int(original['slot']);key=symbol,date,slot;assert key not in keys;keys.add(key)
        complete=bool(mask[date_index[date]*4+slot,symbol_index[symbol]])
        row=dict(original,latest_raw_hour_complete=str(complete),latest_raw_candidate=raw.name,
            current_remaining_reason='admitted_observed_reported_minutes_original_contract' if complete else original['new_remaining_reason'])
        all_rows.append(row)
        if complete:admitted.append(row)
        else:
            remaining.append(row);counts[row['current_remaining_reason']]+=1;stockdays[symbol,date].append(slot)
            if original['new_strict_coarse_pass']=='True':strict.append(row)
    assert len(admitted)==meta['historical_admitted_raw_hours']==464 and len(remaining)==meta['historical_minimum_missing_hours']==2578
    artifacts=[]
    for name,items in [('all_original_scope_status.csv',all_rows),('remaining_hours.csv',remaining),('remaining_strict_coarse_priority.csv',strict)]:
        path=destination/name
        with path.open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields+additions);writer.writeheader();writer.writerows(items)
        artifacts.append(dict(path=str(path),sha256=p.sha(path),rows=len(items)))
    assert p.identity(raw/'dataset/hour_complete.npy')==identities['hour_complete.npy']
    p.atomic_json(receipt_path,dict(checked_utc=p.utc(),source_code_sha256=p.sha(__file__),original_scope_sha256=p.hashlib.sha256(data).hexdigest(),
        raw_full_history_audit_sha256=p.sha(audit_path),raw_meta_sha256=p.sha(meta_path),original_hours=3042,admitted_historical_hours=len(admitted),
        remaining_historical_hours=len(remaining),remaining_stockdays=len(stockdays),remaining_strict_coarse_diagnostic_hours=len(strict),
        remaining_reason_counts=dict(counts),remaining_stockday_slots=[dict(symbol=s,date=d,slots=sorted(v)) for (s,d),v in sorted(stockdays.items())],
        artifacts=artifacts,new_data_admission_from_this_priority_step=0,training_admission_ready=False))


if __name__=='__main__':main()
