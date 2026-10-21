"""Exact corporate-action accounting, separate from strategy and risk policy.

Never adopts unknown orders, rebases unexplained cash, changes targets or fees,
or submits/cancels orders. Booking requires two fresh full-history observations.
"""
import copy
import datetime as dt
import hashlib
import itertools
import json
from decimal import Decimal,ROUND_HALF_UP

class CorporateActionError(ValueError):pass
class CorporateActionPending(ValueError):pass

def digest(x):return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def dec(x):
 try:v=Decimal(str(x))
 except Exception:raise CorporateActionError('公司行动数值格式错误') from None
 if not v.is_finite():raise CorporateActionError('公司行动数值不是有限值')
 return v
def cents(x):return dec(x).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
def moment(x):
 try:t=dt.datetime.fromisoformat(x)
 except (ValueError,TypeError):raise CorporateActionError('公司行动证据时间无效') from None
 if t.tzinfo is None:raise CorporateActionError('公司行动证据缺少时区')
 return t
def code(s):
 s=str(s)
 if s.startswith(('SH','SZ')) and len(s)==8 and s[2:].isdigit():return s
 if len(s)==6 and s.isdigit():return ('SH' if s[0] in '569' else 'SZ')+s
 raise CorporateActionError('持仓证券代码无效')
def component_id(event,kind):return '|'.join((event['code'],event['record_date'],event['ex_date'],kind))
def terms(event):return {k:event[k] for k in ('code','record_date','ex_date','pay_date','cash_per_share','transfer_per_share','bonus_per_share','diluted_cash_per_share')}

def stable(raw):
 return dict(holds=sorted((code(x['secCode']),int(x['count']),int(x['availCount'])) for x in raw['holds']),orders=sorted(raw['orders'],key=lambda x:str(x['orderId'])),deals=sorted(raw['deals'],key=lambda x:(str(x['orderId']),digest(x))),cash=str(dec(raw['balance']['availBalance'])),frozen=str(dec(raw['balance']['frozenMoney'])))

def known_inventory_and_cash(raw,state):
 expected=dict(state.get('prior',{}))
 for a in state['attempts']:
  if not a.get('oid'):raise CorporateActionError('存在回执不明委托，禁止自动公司行动入账')
  if a['oid'] in expected:raise CorporateActionError('重复委托ID')
  expected[a['oid']]=a['order']
 rows={str(x['orderId']):x for x in raw['orders']}
 if len(rows)!=len(raw['orders']):raise CorporateActionError('服务端委托ID重复')
 if not set(expected)<=set(rows):raise CorporateActionPending('已知委托尚未完整返回')
 if not set(state.get('historical',{}))<=set(rows):raise CorporateActionError('完整历史中缺少已知委托')
 for x in raw['orders']:
  if str(x['orderDate']).replace('-','')==state['day'].replace('-','') and str(x['orderId']) not in expected:raise CorporateActionError('发现外部委托，禁止自动公司行动入账')
 inventory=copy.deepcopy(state['baseline']['positions']);net=Decimal(0)
 for oid,order in expected.items():
  r=rows[oid]
  if str(r['status']) not in {'4','7','8','9'}:raise CorporateActionPending('有已知挂单，先等待原委托核验结束')
  filled=int(r['tradeCount']);inventory[order['code']]=inventory.get(order['code'],0)+(filled if order.get('side',1)==1 else -filled)
  linked=[x for x in raw['deals'] if str(x['orderId'])==oid]
  if sum(int(x['tradeCount']) for x in linked)!=filled:raise CorporateActionPending('关联成交尚未完整返回')
  for x in linked:
   fee=sum((dec(x[k]) for k in ('qsYj','ghCost','yhs')),Decimal(0));amount=dec(x['price'])*int(x['tradeCount'])
   net+=(amount if order.get('side',1)==1 else -amount)+fee
 for oid,order in state.get('historical',{}).items():
  if sum(int(x['tradeCount']) for x in raw['deals'] if str(x['orderId'])==oid)!=order['filled']:raise CorporateActionError('已知历史关联成交缺失或数量变化')
 if dec(raw['balance']['frozenMoney'])!=0:raise CorporateActionPending('冻结资金尚未释放')
 return {c:n for c,n in inventory.items() if n},dec(state['baseline']['cash'])-net

def validate_evidence(state,observations,cache,closes,now):
 if state['day']!=now.date().isoformat():raise CorporateActionError('不允许跨日或倒退日期入账')
 if len(observations)!=2:raise CorporateActionError('必须两轮账户证据')
 t=[moment(x['at']) for x in observations]
 if not 5<=(t[1]-t[0]).total_seconds()<=120 or not t[1]<=now or not 0<=(now-t[0]).total_seconds()<=120 or any(x.date()!=now.date() for x in t):raise CorporateActionError('账户证据过期或间隔无效')
 if not all(x['raw'].get('history_complete') for x in observations):raise CorporateActionError('账户历史不完整')
 if stable(observations[0]['raw'])!=stable(observations[1]['raw']):raise CorporateActionPending('两轮账户持仓、现金或成交不一致')
 if cache.get('schema')!=1 or cache.get('complete') is not True or cache.get('day')!=state['day'] or not 0<=(now-moment(cache['as_of'])).total_seconds()<=86400:raise CorporateActionError('开盘前公告缓存缺失、不完整或过期')
 for date,snapshot in closes.items():
  if snapshot.get('schema')!=1 or snapshot.get('day')!=date or snapshot.get('verified') is not True or snapshot.get('positions_sha256')!=digest(snapshot['positions']):raise CorporateActionError('登记日收盘持仓证据无效')
  stamp=moment(snapshot['at'])
  if stamp.date().isoformat()!=date or stamp.hour<15 or stamp>now:raise CorporateActionError('登记日持仓不是收盘核验快照')
 origin=state.get('origin',{}).get('account_sha256')
 if not origin or cache.get('account_sha256')!=origin or any(x.get('account_sha256')!=origin for x in closes.values()):raise CorporateActionError('公司行动证据与账户身份不一致')

def propose(state,observations,cache,closes,now,reconcile,config):
 validate_evidence(state,observations,cache,closes,now)
 raw=observations[-1]['raw'];book_positions,book_cash=known_inventory_and_cash(raw,state)
 observed={}
 for x in raw['holds']:
  c=code(x['secCode']);n=int(x['count'])
  if c in observed or n<0 or not 0<=int(x['availCount'])<=n:raise CorporateActionError('持仓数量、可卖数量或重复项异常')
  observed[c]=n
 observed={c:n for c,n in observed.items() if n}
 differences={c:observed.get(c,0)-book_positions.get(c,0) for c in set(observed)|set(book_positions) if observed.get(c,0)!=book_positions.get(c,0)}
 variance=state.get('cash_variance',{});confirmed=dec(variance.get('amount_cny',0)) if variance and variance.get('day')==state['day'] else Decimal(0)
 cash_delta=dec(raw['balance']['availBalance'])-dec(config['excluded_cash_cny'])-book_cash-confirmed
 if not differences and cash_delta==0:raise CorporateActionPending('账户已一致，不需要公司行动入账')
 if cash_delta<0:raise CorporateActionError('现金减少无法由公开现金分红解释；可能扣税或外部资金变动')
 if any(x<0 for x in differences.values()):raise CorporateActionError('持仓减少不能由分红送转解释')
 applied=copy.deepcopy(state.get('corporate_action_book',{}).get('applied',{}));stock_entries=[];cash_candidates=[];seen=set()
 for event in cache['events']:
  if event.get('evidence_verified') is not True or len(event.get('announcement_sha256',''))!=64:raise CorporateActionError('缺少已核验的公司实施公告')
  if event['code'] not in cache['scope']:raise CorporateActionError('公司行动证券超出公告查询范围')
  logical=(event['code'],event['record_date'],event['ex_date'])
  if logical in seen:raise CorporateActionError('同一公司行动存在重复方案')
  seen.add(logical)
  if event['record_date']>=event['ex_date'] or event['record_date']>=event['pay_date']:raise CorporateActionError('公司行动日期顺序无效')
  for kind in ('shares','cash'):
   old=applied.get(component_id(event,kind))
   if old and old['terms_sha256']!=digest(terms(event)):raise CorporateActionError('已入账方案发生修订，需人工核验')
  if event['record_date'] not in closes:continue
  held=closes[event['record_date']]['positions'].get(event['code'],0)
  if type(held) is not int or held<0:raise CorporateActionError('登记日股数不是非负整数')
  if not held:continue
  stock_id=component_id(event,'shares');cash_id=component_id(event,'cash')
  ratio=dec(event['transfer_per_share'])+dec(event['bonus_per_share'])
  if ratio<0:raise CorporateActionError('送转股比例无效')
  if ratio and event['ex_date']<=state['day'] and stock_id not in applied:
   addition=held*ratio
   if addition!=addition.to_integral_value():raise CorporateActionError(f'{event["code"]} 存在零股分配，不能猜测登记结算结果')
   stock_entries.append(dict(id=stock_id,code=event['code'],quantity=int(addition),held=held,event=event,kind='shares'))
  cash=dec(event['cash_per_share'])
  if cash<0:raise CorporateActionError('现金分红比例无效')
  if cash and event['pay_date']<=state['day'] and cash_id not in applied:
   gross=cents(held*cash);options={gross:'published_gross'}
   for value in event.get('diluted_cash_per_share',[]):
    rate=dec(value)
    if not 0<rate<cash:raise CorporateActionError('摊薄除息参考值无效')
    options.setdefault(cents(held*rate),'disclosed_diluted_simulation_reference')
   cash_candidates.append(dict(id=cash_id,code=event['code'],held=held,event=event,kind='cash',options=options))
 selected=[];explained={}
 for entry in stock_entries:
  c=entry['code']
  if differences.get(c)==entry['quantity']:
   if c in explained:raise CorporateActionError('持仓变化存在多种公司行动解释')
   explained[c]=entry['quantity'];selected.append(entry)
 if explained!=differences:raise CorporateActionError('持仓差异与登记日应收送转股数不完全一致，或缺少登记日证据')
 if cash_delta:
  if len(cash_candidates)>12:raise CorporateActionError('待分红方案过多，禁止模糊匹配')
  solutions=[]
  choices=[[(Decimal(0),None)]+[(v,mode) for v,mode in sorted(x['options'].items()) if v] for x in cash_candidates]
  combinations=1
  for options in choices:combinations*=len(options)
  if combinations>65536:raise CorporateActionError('现金分红匹配超过审查上限')
  for combination in itertools.product(*choices):
   if abs(sum((x[0] for x in combination),Decimal(0))-cash_delta)<Decimal('.01'):   # [2026-10-16] ledger cash carries sub-cent fee fractions (e.g. .853); the platform credits whole cents
    solutions.append(combination)
    if len(solutions)>1:break
  if len(solutions)!=1:raise CorporateActionError('现金变化不能由唯一、精确的公告分红组合解释')
  for entry,(amount,mode) in zip(cash_candidates,solutions[0]):
   if amount:selected.append(dict(entry,amount=str(amount),credit_basis=mode,published_gross_cny=str(cents(entry['held']*dec(entry['event']['cash_per_share'])))))
 if not selected:raise CorporateActionError('没有可核验的公司行动')
 proposal=copy.deepcopy(state);book=proposal.setdefault('corporate_action_book',{});book.setdefault('receipts',[])
 for entry in selected:
  c=entry['code']
  if entry['kind']=='shares':proposal['baseline']['positions'][c]=proposal['baseline']['positions'].get(c,0)+entry['quantity']
  else:proposal['baseline']['cash']=str(dec(proposal['baseline']['cash'])+dec(entry['amount']))
  applied[entry['id']]=dict(kind=entry['kind'],code=c,at=now.isoformat(),held_on_record_date=entry['held'],quantity=entry.get('quantity'),cash_cny=entry.get('amount'),credit_basis=entry.get('credit_basis'),published_gross_cny=entry.get('published_gross_cny'),terms_sha256=digest(terms(entry['event'])),announcement_sha256=entry['event']['announcement_sha256'],announcement_url=entry['event']['announcement_url'],record_date=entry['event']['record_date'],effective_date=entry['event']['ex_date'] if entry['kind']=='shares' else entry['event']['pay_date'])
 audits=[]
 for observation in observations:
  # The unchanged original reconciliation checks identities, fees, fills,
  # cash tolerance, external activity and sellable quantities one more time.
  a=reconcile(observation['raw'],proposal,config['excluded_cash_cny'],config['cash_drift_tolerance_cny'],False)
  if a['pending'] or dec(a['frozen']) or abs(dec(a['cash_drift_cny'])-confirmed)>=Decimal('.01'):raise CorporateActionError('公司行动后严格核账未通过')   # [2026-10-16] sub-cent ledger fraction is not a discrepancy
  balance=observation['raw']['balance']
  if abs(dec(balance['totalPosValue'])+dec(balance['availBalance'])-dec(balance['totalAssets']))>Decimal('.03'):raise CorporateActionError('总资产与现金/持仓市值不守恒')
  audits.append(dict(a,checked_at=observation['at']))
 book['applied']=applied
 receipt=dict(at=now.isoformat(),before_sha256=digest(state),applied_ids=[x['id'] for x in selected],snapshots_sha256=[digest(x) for x in observations],close_snapshots_sha256={d:digest(closes[d]) for d in {x['event']['record_date'] for x in selected}},cache_sha256=digest(cache),cash_credit_cny=str(cash_delta),shares_credited=explained,original_risk_config_sha256=digest(config),targets_unchanged=proposal.get('targets')==state.get('targets'),new_orders=0,cancellations=0,two_audits_passed=True)
 book['receipts'].append(receipt);proposal['last_audit']=audits[-1];proposal['last_snapshot_mismatch']=None
 return proposal,receipt
