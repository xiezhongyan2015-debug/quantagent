"""Audit saved run files only; no broker imports, HTTP, or trading writes."""
import json, hashlib, os, threading, datetime
from pathlib import Path
from decimal import Decimal as D, ROUND_CEILING

def review(r):
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8-sig'))
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    digest = lambda x: hashlib.sha256(json.dumps(x, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    s=read(r/'private_state.json'); c=read(r/'config.json'); a=read(r/'authorization.json')
    seed=read(r/'seed.json'); seal=read(r/'source_seal.json'); source=read(seal['path'])
    rc=c['rebalance_after_pilot']; t=read(rc['target_path']); m=read(rc['membership_path'])
    audit=s['last_audit']; attempts=s['attempts']; errors=[]
    def check(ok, label):
        if not ok: errors.append(label)
    check(a['action']=='User pressed Start in the simulation console', 'authorization')
    check(digest(c)==a['config_sha256'] and digest(seed)==a['seed_sha256'], 'authorization seal')
    check(digest(source)==seal['sha256'] and source['attempts']==seed['attempts'], 'source seal')
    check(all(all(x.get(k)==y.get(k) for k in ('oid','order','phase','state','filled','terminal')) for x,y in zip(seed['attempts'],attempts)) and len(attempts)>=len(seed['attempts']), 'preserved source orders')
    check(sha(rc['target_path'])==rc['target_sha256']=='6034463bec5a7dcb09d8acbed45041a832cbda602b0880a1d04de5e05fa6eee8', 'final target seal')
    check(sha(rc['membership_path'])==rc['membership_sha256'], 'membership seal')
    check(s['rebalance_workflow']['phase']=='formal' and s['rebalance_workflow']['strategy_layer']=='V5_tilt', 'final strategy phase')
    expected={x['code']:x['shares'] for x in t['targets']}; targets={k:x['quantity'] for k,x in s['targets'].items()}
    check(all(targets.get(k)==n for k,n in expected.items()) and all(n==expected.get(k,0) for k,n in targets.items()), 'executed target shares')
    excluded={'SZ300223','SZ300395','SH600663','SH688578'}
    held=dict(s['baseline']['positions']); sold={}; ids=[]; maxima=[]; counts={}; direction=[]; unit=[]; cap=[]; price=[]; t1=[]; terminal=[]
    rows=list(s.get('prior',{}).values())+attempts
    for x in rows:
        o=x['order']; code=o['code']; qty=o['quantity']; filled=x['filled']; side=o['side']; ids.append(x['oid'])
        state=str(x['state']); counts[state]=counts.get(state,0)+1
        check(type(filled) is int and 0<=filled<=qty, 'invalid fill '+str(x['oid']))
        check(state!='4' or filled==qty, 'incomplete full fill '+str(x['oid']))
        if not x.get('terminal') or state not in ('4','8'): terminal.append(x['oid'])
        if x.get('phase')=='formal':
            gap=targets.get(code,0)-held.get(code,0)
            if side!=(1 if gap>0 else 2) or qty>abs(gap): direction.append(x['oid'])
            reserve=(D(str(o['price']))*qty*D('1.001')+10).quantize(D('.01'),rounding=ROUND_CEILING); maxima.append(reserve)
            if reserve>D('50000') or D(str(x['reserve']))!=reserve: cap.append(x['oid'])
            if (side==1 and (qty%100 or code not in set(m['allowed']) or code.startswith('SH688') and qty<200)) or (side==2 and code.startswith('SH688') and qty<200 and not (targets.get(code,0)==0 and qty==held.get(code,0))): unit.append(x['oid'])
            ref=D(str(o['reference_price'])); p=D(str(o['price']))
            if (side==1 and p>ref*D('1.03')) or (side==2 and p<ref*D('.97')) or p!=p.quantize(D('.01')): price.append(x['oid'])
        if side==2:
            sold[code]=sold.get(code,0)+filled
            if sold[code]>s['baseline']['positions'].get(code,0): t1.append(x['oid'])
        held[code]=held.get(code,0)+(filled if side==1 else -filled)
        check(held[code]>=0, 'negative position '+code)
    positive=lambda x:{k:v for k,v in x.items() if v}
    check(len(ids)==len(set(ids)), 'duplicate order id')
    check(positive(held)==positive(audit['positions']), 'reconstructed holdings')
    gaps={k:targets.get(k,0)-audit['positions'].get(k,0) for k in set(targets)|set(audit['positions']) if targets.get(k,0)!=audit['positions'].get(k,0)}
    other={k:v for k,v in gaps.items() if k not in excluded}
    for values,label in [(direction,'direction/overshoot'),(unit,'units/universe'),(cap,'fee-inclusive cap'),(price,'price protection'),(t1,'T+1'),(terminal,'unresolved/partial/rejected')]:
        check(not values,label+':'+','.join(map(str,values)))
    check(not other,'nonexcluded target differences')
    pilots=[x for x in attempts if x.get('phase')!='formal']
    check(len(pilots)==1 and pilots[0]['oid']=='262846200000035118' and pilots[0]['filled']==100,'single pilot')
    check(not audit['pending'] and D(audit['frozen'])==0 and audit.get('isolated_order_count',0)==0,'pending/frozen/isolated')
    nav=D(str(audit['total_assets'])); marks=audit['position_mark_values']; csi=sum(D(str(v)) for k,v in marks.items() if k in set(m['csi500']))/nav
    stock=D(str(audit['market_value']))/nav
    check(D('.90')<=stock<=D('.95') and csi>=D('.80'), 'portfolio ratios')
    check(all(D(str(v))<=D('500000') for v in marks.values()),'current single-stock value')
    cash=D(str(audit['account_cash_before_quarantine'])); book=D(str(s['baseline']['cash']))-D(str(audit['frozen']))-D(str(audit['daily_paid']))
    check(abs(cash-book-D(str(audit['cash_drift_cny'])))<=D('.03'),'cash book and recorded drift')
    return dict(read_at=datetime.datetime.now().astimezone().isoformat(),run=r.name,audit_at=audit['checked_at'],final_target_sha=rc['target_sha256'],target_count=len(expected),matched_final_targets_excluding_four=sum(k not in excluded and audit['positions'].get(k,0)==v for k,v in expected.items()),source_orders=len(seed['attempts']),orders=len(attempts),states=counts,filled_shares=sum(x['filled'] for x in attempts),pilot_count=len(pilots),max_formal_reserved_cny=str(max(maxima)),other_gaps=other,excluded_gaps=gaps,stock_fraction=str(stock),csi500_fraction=str(csi),nav=str(nav),cash=str(cash),fees=str(audit['fees']),cash_drift=str(audit['cash_drift_cny']),errors=errors,independent_deal_fee_recalculation=False)

if __name__=='__main__':
    import win32job, win32api
    job=win32job.CreateJobObject(None,'')
    limit=win32job.QueryInformationJobObject(job,win32job.JobObjectExtendedLimitInformation)
    limit['BasicLimitInformation']['LimitFlags']=win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY
    limit['ProcessMemoryLimit']=256*1024*1024
    win32job.SetInformationJobObject(job,win32job.JobObjectExtendedLimitInformation,limit)
    win32job.AssignProcessToJobObject(job,win32api.GetCurrentProcess())
    threading.Timer(45,lambda:os._exit(124)).start()
    r=Path('C:/ChoiceBridge/executor/research/choice_pcf_automation/console/runs/2026-10-12/105511_611ab86a')
    report=review(r)
    p=Path('C:/ChoiceBridge/staging/rebalance_audit_20261012.txt')
    content=json.dumps(report,indent=2)
    if len(content.encode())>65536: raise RuntimeError('Audit output exceeds 64 KiB')
    p.write_text(content,encoding='utf8'); os.startfile(str(p)); os._exit(0)
