"""Validate a non-executable one-lot transport probe. No network or order functions."""
import argparse,datetime as dt,hashlib,json
from decimal import Decimal
from pathlib import Path
ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--baseline',required=True);ap.add_argument('--input-sha256',required=True);ap.add_argument('--baseline-sha256',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
def checked(path,sha):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('File channel hash mismatch')
    return json.loads(raw)
p=checked(a.input,a.input_sha256);b=checked(a.baseline,a.baseline_sha256)
if p['kind']!='non_executable_one_lot_transport_probe' or not p['dry_run_only'] or p['orders_enabled'] is not False or p['executable_quote_present'] is not False or p['current_index_membership_verified'] is not False:raise ValueError('Only inert transport probes accepted')
if p['account_sha256']!=b['account_sha256']:raise ValueError('Captain account mismatch')
if type(p['quantity']) is not int or p['quantity']!=100 or p['max_new_orders']!=1 or p['quantity_semantics']!='incremental_buy' or Decimal(p['max_single_order_cny'])!=10000:raise ValueError('User pilot scope mismatch')
if not p['symbol'].startswith(('000','001','002','003','600','601','603','605')):raise ValueError('Ordinary exchange lot not established')
known=next(row for row in b['positions'] if row['code']==p['symbol'])
price=Decimal(p['test_mark_price']);fee=Decimal(p['test_fee_reserve_cny']);gross=price*100;reserve=gross+fee
if not price.is_finite() or not fee.is_finite() or price<=0 or fee<0 or reserve>10000 or reserve>Decimal(b['available_cash']):raise ValueError('Pilot arithmetic or cash cap rejected')
r={'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'kind':'non_executable_transport_probe_receipt','account_tail':b['account_tail'],'symbol':p['symbol'],'quantity':100,'quantity_semantics':'incremental_buy','existing_shares':known['shares'],'hypothetical_shares_after_one_lot':known['shares']+100,'test_gross_cny':str(gross),'test_reserve_cny':str(reserve),'file_hashes_match':True,'scope_checks_passed':True,'ready_for_execution':False,'execution_blockers':['No executable quote','Current membership not verified','Sunday preparation only'],'new_orders_submitted':0,'cancellations_submitted':0,'automatic_trading_started':False}
with Path(a.out).open('x',encoding='utf8') as f:json.dump(r,f,indent=2)
print(json.dumps(r,indent=2))
