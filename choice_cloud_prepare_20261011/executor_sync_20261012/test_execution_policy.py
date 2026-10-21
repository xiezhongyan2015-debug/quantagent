"""Offline directional protection and bounded, reconciled zero-fill retries."""
import copy, datetime as dt, unittest
from unittest.mock import Mock, patch
import test_workflow_rebalance as f
import workflow_rebalance as w
import console_runtime as rt

class Policy(f.Workflow):
 def cancelled_fixture(self,n=1):
  order=self.formal()
  current=self.state['attempts'].pop()
  for i in range(n):
   old=copy.deepcopy(current);old.update(oid='cancel'+str(i),state='8',filled=0,terminal=True,cancel_requested_at=(f.NOW-dt.timedelta(seconds=60)).isoformat(),terminal_at=(f.NOW-dt.timedelta(seconds=10)).isoformat())
   self.state['attempts'].append(old)
   self.audit['states'][old['oid']]={'state':'8','filled':0,'terminal':True}
  self.state['attempts'].append(current)
  self.state['last_audit']=copy.deepcopy(self.audit)
  audits=[dict(copy.deepcopy(self.audit),checked_at=(f.NOW-dt.timedelta(seconds=5)).isoformat()),copy.deepcopy(self.audit)]
  self.state['zero_cancel_retry_proofs']={a['oid']:w.make_zero_cancel_proof(a,audits,f.NOW) for a in self.state['attempts'][1:-1]}
  return order,audits
 def test_cheaper_buy_passes_directional_protection(self):self.assertTrue(w.directional_price_allowed(1,9,10,.03))
 def test_higher_sell_passes_directional_protection(self):self.assertTrue(w.directional_price_allowed(2,11,10,.03))
 def test_buy_adverse_boundary(self):
  self.assertTrue(w.directional_price_allowed(1,10.30,10,.03));self.assertFalse(w.directional_price_allowed(1,10.31,10,.03))
 def test_sell_adverse_boundary(self):
  self.assertTrue(w.directional_price_allowed(2,9.70,10,.03));self.assertFalse(w.directional_price_allowed(2,9.69,10,.03))
 def test_invalid_prices_and_scope_block(self):
  for args in [(0,10,10,.03),(1,0,10,.03),(2,10,0,.03),(1,10,10,.04),(1,'NaN',10,.03)]:
   with self.subTest(args=args),self.assertRaises(rt.TradeError):w.directional_price_allowed(*args)
 def test_cheaper_buy_passes_second_write_boundary(self):
  o=self.formal();o['price']=9.5;self.state['attempts'][-1]['reserve']=str(w.reserved(9.5,100));self.state['quotes'][o['code']]=f.quote(o['code'],9.5)
  w.guard_formal(self.c,self.state,o,f.NOW)
 def test_adverse_buy_blocks_second_write_boundary(self):
  o=self.formal();o['price']=10.5;self.state['attempts'][-1]['reserve']=str(w.reserved(10.5,100));self.state['quotes'][o['code']]=f.quote(o['code'],10.5)
  with self.assertRaises(rt.TradeError):w.guard_formal(self.c,self.state,o,f.NOW)
 def sell_fixture(self,price):
  o=self.formal(side=2);o['price']=price;self.state['attempts'][-1]['reserve']=str(w.reserved(price,100));self.state['quotes'][o['code']]=f.quote(o['code'],price)
  a=self.state['last_audit'];a['positions'][o['code']]=300;a['sellable'][o['code']]=300;a['position_mark_values'][o['code']]='3000';a['market_value']='145000';a['cash']='10000'
  return o
 def test_higher_sell_passes_second_write_boundary(self):
  o=self.sell_fixture(10.5);w.guard_formal(self.c,self.state,o,f.NOW)
 def test_lower_sell_still_blocks_second_write_boundary(self):
  o=self.sell_fixture(9.5)
  with self.assertRaises(rt.TradeError):w.guard_formal(self.c,self.state,o,f.NOW)
 def test_zero_cancel_two_audits_allow_fresh_order(self):
  o,_=self.cancelled_fixture();w.guard_formal(self.c,self.state,o,f.NOW)
 def test_missing_zero_cancel_proof_blocks_writer(self):
  o,_=self.cancelled_fixture();self.state.pop('zero_cancel_retry_proofs')
  with self.assertRaises(rt.TradeError):w.guard_formal(self.c,self.state,o,f.NOW)
 def test_unknown_partial_rejected_and_nonterminal_never_retry(self):
  self.cancelled_fixture();original=copy.deepcopy(self.state['attempts'][1])
  for status,filled,terminal,oid in [('UNKNOWN',0,False,None),('7',50,True,'cancel0'),('9',0,True,'cancel0'),('8',1,True,'cancel0'),('8',0,False,'cancel0'),('7',0,True,'cancel0')]:
   self.state['attempts'][1]=dict(copy.deepcopy(original),state=status,filled=filled,terminal=terminal,oid=oid)
   with self.subTest(status=status,filled=filled,terminal=terminal):self.assertFalse(w.prior_slices_allowed(self.state['attempts'][1:-1]))
 def test_cancel_without_own_cancel_request_never_retries(self):
  self.cancelled_fixture();self.state['attempts'][1].pop('cancel_requested_at');self.assertFalse(w.prior_slices_allowed(self.state['attempts'][1:-1]))
 def test_third_cancel_stops_further_retries(self):
  o,_=self.cancelled_fixture(3)
  with self.assertRaises(rt.TradeError):w.guard_formal(self.c,self.state,o,f.NOW)
 def test_second_cancel_allows_last_retry(self):
  o,_=self.cancelled_fixture(2);w.guard_formal(self.c,self.state,o,f.NOW)
 def test_expired_proof_blocks(self):
  o,_=self.cancelled_fixture();self.assertFalse(w.zero_cancel_proofs_valid(self.state,self.state['attempts'][1:-1],f.NOW+dt.timedelta(seconds=31)))
 def test_cash_or_inventory_change_invalidates_proof(self):
  self.cancelled_fixture();original=copy.deepcopy(self.state['last_audit'])
  for key,value in [('cash','12001'),('positions',{}),('sellable',{})]:
   self.state['last_audit']=dict(copy.deepcopy(original));self.state['last_audit'][key]=value
   with self.subTest(key=key):self.assertFalse(w.zero_cancel_proofs_valid(self.state,self.state['attempts'][1:-1],f.NOW))
 def test_pending_frozen_drift_isolation_block_proof(self):
  _,audits=self.cancelled_fixture()
  for key,value in [('pending',{'p':{}}),('frozen','1'),('cash_drift_requires_confirmation',True),('isolated_order_count',1)]:
   a=copy.deepcopy(audits);a[1][key]=value
   with self.subTest(key=key),self.assertRaises(rt.TradeError):w.make_zero_cancel_proof(self.state['attempts'][1],a,f.NOW)
 def test_only_one_audit_or_too_short_interval_blocks(self):
  _,audits=self.cancelled_fixture()
  for a in [audits[:1],[audits[1],audits[1]]]:
   with self.assertRaises(rt.TradeError):w.make_zero_cancel_proof(self.state['attempts'][1],a,f.NOW)
 def test_changed_server_fill_state_blocks_proof(self):
  self.cancelled_fixture();self.state['last_audit']['states']['cancel0']['filled']=1
  self.assertFalse(w.zero_cancel_proofs_valid(self.state,self.state['attempts'][1:-1],f.NOW))
 def test_changed_order_identity_blocks_proof(self):
  self.cancelled_fixture();self.state['attempts'][1]['order']['quantity']=200
  self.assertFalse(w.zero_cancel_proofs_valid(self.state,self.state['attempts'][1:-1],f.NOW))
 def test_disagreeing_cash_in_two_audits_keeps_readonly(self):
  _,audits=self.cancelled_fixture();audits[0]['cash']='12001'
  with self.assertRaises(rt.FillSnapshotPending):w.make_zero_cancel_proof(self.state['attempts'][1],audits,f.NOW)
 def test_retry_quantity_still_cannot_exceed_gap(self):
  o,_=self.cancelled_fixture();o['quantity']=200;self.state['attempts'][-1]['reserve']=str(w.reserved(10,200))
  with self.assertRaises(rt.TradeError):w.guard_formal(self.c,self.state,o,f.NOW)
 def test_worker_audits_then_requotes_without_duplicate_old_order(self):
  _,audits=self.cancelled_fixture();self.state['attempts'].pop();self.state.pop('zero_cancel_retry_proofs')
  worker=rt.Worker.__new__(rt.Worker);worker.state=self.state;worker.c=w.effective(self.c,self.state);worker.original_config=self.c
  worker.audit=Mock(return_value=self.state['last_audit']);worker.cancel=Mock();worker.check_stop=Mock(return_value=False);worker.two_audits=Mock(return_value=audits)
  worker.http=Mock();worker.flush=Mock();worker.public=Mock();worker.last_submit=0;worker.broker=Mock()
  def submit(o):w.guard_formal(self.c,self.state,o,f.NOW);return 'fresh_oid'
  worker.broker.submit.side_effect=submit
  before=copy.deepcopy(self.state['attempts'])
  with patch.object(rt,'now_cn',return_value=f.NOW),patch.object(rt.time,'monotonic',return_value=1000),patch.object(rt.floor,'active',return_value=False),patch.object(rt,'quote',side_effect=[f.quote(price=10),f.quote(price=9.5)]) as quotes:
   self.assertEqual(worker.step(),'watching');worker.broker.submit.assert_not_called();self.assertEqual(self.state['attempts'],before)
   self.assertEqual(worker.step(),'watching');self.assertEqual(quotes.call_count,2)
  self.assertEqual(worker.broker.submit.call_count,1);self.assertEqual(self.state['attempts'][:-1],before)
  self.assertEqual(self.state['attempts'][-1]['order']['quantity'],100);self.assertEqual(self.state['attempts'][-1]['order']['price'],9.5)
  self.assertEqual(self.state['attempts'][-1]['oid'],'fresh_oid')

def load_tests(loader,tests,pattern):
 return unittest.TestSuite(Policy(name) for name in Policy.__dict__ if name.startswith('test_'))
