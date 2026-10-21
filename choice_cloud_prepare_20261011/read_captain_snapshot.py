"""Capture a read-only captain snapshot over the existing authenticated connection."""
import datetime as dt,hashlib,json,os,uuid
from decimal import Decimal
from pathlib import Path
from ssh_small_files import run_ps
ROOT=Path(__file__).resolve().parent
ACCOUNT='000000000000000000'
EXPECTED_HASH='ec7079162b1f37ea67ba32c6554ab42599c7cfb405bdaa3a78acbde73763a019'
def main():
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
    remote='C:\\ChoiceBridge\\staging\\captain_snapshot_'+stamp+'.json'
    script=r"$h='C:\ChoiceBridge\staging\captain_account_readonly.py';if((Get-FileHash -LiteralPath $h -Algorithm SHA256).Hash.ToLowerInvariant() -ne '"+EXPECTED_HASH+r"'){throw 'Reviewed query helper changed'};& 'C:\ChoiceBridge\runtime\choice-venv\Scripts\python.exe' -X utf8 $h --account '"+ACCOUNT+"' --out '"+remote+"';if($LASTEXITCODE -ne 0){throw 'Captain readonly snapshot failed'}"
    raw=run_ps(script,timeout=120)
    data=json.loads(raw)
    assert data['account_sha256']==hashlib.sha256(ACCOUNT.encode()).hexdigest()
    assert data['new_orders_submitted']==0 and data['cancellations_submitted']==0 and data['automatic_trading_started'] is False
    audit=data['network_audit'];expected={'AguTrade/SpoBalInfo','AguTrade/SpoHold','AguTrade/SpoOrders','AguTrade/SpoDeal'}
    assert len(audit)==4 and {r['endpoint'] for r in audit}==expected and all(r['method']=='GET' and r['http_status']==200 for r in audit)
    nav=Decimal(data['balance']['totalAssets']);cash=Decimal(data['balance']['availBalance']);frozen=Decimal(data['balance']['frozenMoney'])
    assert nav>0 and cash>=0 and frozen>=0
    out=ROOT/'snapshots';out.mkdir(mode=0o700,exist_ok=True)
    file=out/('captain_snapshot_'+stamp+'.json')
    fd=os.open(file,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as target:json.dump(data,target,ensure_ascii=False,indent=2);target.write('\n')
    print(json.dumps({'local_snapshot':str(file),'account_tail':data['account_tail'],'balance':data['balance'],'holding_count':len(data['positions']),'order_count':len(data['orders']),'unfinished_order_count':data['unfinished_order_count'],'deals_count':data['deals_count'],'four_get_queries_passed':True,'new_orders_submitted':0},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
