"""Independent Decimal arithmetic audit of the fixed June 2019 integration case.

Reads the frozen cache, plan and one saved attempt. No market downloads, model
calls, engine execution or old artifact edits. This is deliberately case-specific.
The separate ledger replay verifies history; arithmetic below does not call its
accounting or fee helpers.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / 'experiment_traces/meta_raw_portfolio_pilot'
DATA = ROOT / 'experiment_traces/meta_raw_daily_data_pilot'
STAGE = ROOT / 'experiment_traces/meta_ashare_revision5'
CENT = D('.01')


def read(path): return json.loads(path.read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def money(value): return format(value.quantize(CENT), '.2f')


def audit(attempt):
    result, receipt, plan = read(attempt/'result.json'), read(attempt/'receipt.json'), read(PILOT/'plan.json')
    checks = []

    def check(name, condition):
        if not condition: raise AssertionError(name)
        checks.append(name)

    check('result identity', sha(attempt/'result.json') == receipt['result_sha256'])
    check('plan identity', sha(PILOT/'plan.json') == receipt['plan_sha256'] == read(PILOT/'plan_identity.json')['sha256'])
    for name, expected in receipt['code_sha256'].items():
        check('code identity: '+name, sha(ROOT/name) == expected)
    for name, expected in plan['input_hashes'].items():
        check('input identity: '+name, sha(DATA/name) == expected)
    check('case stays fixed', plan['initial_cash'] == '1000000.00' and len(plan['codes']) == 9
          and plan['start_date'] == '2019-06-21' and plan['end_date'] == '2019-06-28')
    check('cost repair recorded', result['manifest']['policy']['slippage_fraction'] == '0.001')
    rows = {(r['date'],r['code']):r for r in read(DATA/'raw_daily_rows.json')}
    check('all stock days retained', len(rows) == 63 and result['manifest']['input_stock_days'] == 63
          and result['manifest']['replay_stock_days'] == 54)
    expected_targets = [(code, signal, day, weight) for signal,day,weight in
        [('2019-06-21','2019-06-24','0.10'),('2019-06-25','2019-06-26','0')] for code in plan['codes']]
    check('targets unchanged', expected_targets == [(t['symbol'],t['signal_date'],t['trade_date'],t['target_weight']) for t in plan['targets']])
    orders = {(o['trade_date'],o['symbol']):o for o in result['orders']}
    check('no lost or extra orders', len(orders) == len(result['orders']) == 18 and set(orders) ==
          {(t['trade_date'],t['symbol']) for t in plan['targets']})
    trades = {(t['date'],t['symbol']):t for t in result['trades']}
    check('eight buys and exits', len(trades) == len(result['trades']) == 16)
    check('missing stock retained twice', len(result['rejections']) == 2 and
          all(r['symbol'] == 'sh603786' and r['filled_quantity'] == 0 for r in result['rejections']))

    cash, fees, slip, previous_nav = D(plan['initial_cash']), D(0), D(0), D(plan['initial_cash'])
    holdings, buys, expected_daily, expected_trade_events = {}, {}, [], {}
    gross_dividend, tax = D(0), D(0)
    for reported in result['daily']:
        day = reported['date']
        if day == '2019-06-27': cash += gross_dividend-tax
        for code in sorted(plan['codes']):
            if (day,code) not in orders: continue
            o = orders[day,code]
            check(day+code+' previous NAV budget', D(o['budget_previous_tax_net_nav']) == previous_nav)
            check(day+code+' no rejected-target renormalization', D(o['target_budget']) ==
                  (previous_nav * (D('.1') if day == '2019-06-24' else D(0))).quantize(CENT))
            if code == 'sh603786':
                check(day+code+' missing open evidenced', rows[day,code]['raw_open'] is None and o['status'] == 'rejected')
                continue
            side = 'buy' if day == '2019-06-24' else 'sell'
            raw = D(rows[day,code]['raw_price_text']['raw_open'])
            fill = (raw*(D('1.001') if side == 'buy' else D('.999'))).quantize(CENT,
                    rounding=ROUND_CEILING if side == 'buy' else ROUND_FLOOR)
            prior_day = plan['calendar'][plan['calendar'].index(day)-1]
            prior = rows[prior_day,code]
            capacity = int(D(str(prior['volume']))*D('.05'))
            quantity = int((D('100000')/fill/D(100)).to_integral_value(rounding=ROUND_FLOOR))*100 if side == 'buy' else holdings[code]
            check(day+code+' capacity nonbinding', quantity <= capacity)
            check(day+code+' integer round lot', type(quantity) is int and quantity > 0 and quantity % 100 == 0)
            reference = D(rows[day,code]['raw_price_text']['raw_prev_close'])
            check(day+code+' inside price band',
                  (reference*D('.9')).quantize(CENT,rounding=ROUND_HALF_UP) < fill <
                  (reference*D('1.1')).quantize(CENT,rounding=ROUND_HALF_UP))
            gross = fill*quantity
            commission = max(D(5),gross*D('.0003')).quantize(CENT,rounding=ROUND_HALF_UP)
            transfer = (gross*D('.00002')).quantize(CENT,rounding=ROUND_HALF_UP)
            stamp = (gross*D('.001')).quantize(CENT,rounding=ROUND_HALF_UP) if side == 'sell' else D(0)
            cost = commission+transfer+stamp
            t = trades[day,code]
            check(day+code+' independently reconstructed fill',
                  t['side'] == side and t['quantity'] == quantity and D(t['raw_price']) == fill and D(t['raw_open']) == raw)
            check(day+code+' independently reconstructed fees', all(D(t['fees'][k]) == v for k,v in
                  {'gross_amount':gross,'commission':commission,'transfer_fee':transfer,'stamp_duty':stamp,'total':cost}.items()))
            check(day+code+' lagged capacity and status evidence', o['lagged_volume_date'] == prior_day
                  and o['lagged_volume'] == int(prior['volume']) and o['capacity_estimate_quantity'] == capacity)
            expected_slip = abs(fill-raw)*quantity
            check(day+code+' slippage attribution', D(t['slippage_amount']) == expected_slip)
            if side == 'buy':
                check(day+code+' cash budget', gross+cost <= cash)
                cash -= gross+cost
                holdings[code] = quantity
                buys[code] = (day,quantity)
            else:
                check(day+code+' T+1 inventory', plan['calendar'].index(day) > plan['calendar'].index(buys[code][0]) and quantity == buys[code][1])
                cash += gross-cost
                del holdings[code]
            fees += cost
            slip += expected_slip
            expected_trade_events[t['event_id']] = {'symbol':code,'quantity':quantity,'raw_price':money(fill),'fees':money(cost)}
        if day == '2019-06-25': gross_dividend = D(holdings['sz000001'])*D('.145')
        if day == '2019-06-26': tax = gross_dividend*D('.20')
        receivable = gross_dividend if day == '2019-06-26' else D(0)
        payable = tax if day == '2019-06-26' else D(0)
        stocks = sum((quantity*D(rows[day,code]['raw_price_text']['raw_close']) for code,quantity in holdings.items()),D(0))
        nav = cash+stocks+receivable-payable
        expected = {'date':day,'cash_available':money(cash),'cash_receivable_gross':money(receivable),
           'realized_tax_unpaid':money(payable),'remaining_tax_reserve':'0.00',
           'gross_asset_value':money(cash+stocks+receivable),'simulated_net_asset_value':money(nav),
           'simulated_net_pnl':money(nav-D(plan['initial_cash'])),'fees_paid_cumulative':money(fees),
           'slippage_in_fill_prices_cumulative':money(slip),'holdings':dict(holdings),'stock_day_denominator':9}
        check(day+' independent daily cash/stock/tax/NAV', all(reported[k] == v for k,v in expected.items()))
        expected_daily.append(expected)
        previous_nav = nav
    check('all six valuation days', [r['date'] for r in expected_daily] == plan['calendar'][1:] == result['replay_dates'])
    check('no remaining inventory', not holdings)
    for entry in result['journal']:
        e = entry['event']
        if e['kind'] in {'buy_fill','sell_fill'}:
            expected = expected_trade_events.pop(e['event_id'])
            check('journal fill '+e['event_id'], all(e['data'][k] == v for k,v in expected.items()))
    check('journal includes every fill exactly once', not expected_trade_events)
    kinds = Counter(e['event']['kind'] for e in result['journal'])
    check('event denominator and action path', kinds == Counter(cash_deposit=1,buy_fill=8,sell_fill=8,
          record_entitlement=1,activate_entitlement=1,tax_assessed=1,cash_dividend_paid=1,tax_paid=1))
    # Full hash/event/state history check, separate from independent arithmetic.
    sys.path.insert(0,str(STAGE/'src'))
    from quanta_agents.raw_share_ledger import RawShareLedger
    replay = RawShareLedger.replay(result['genesis'],result['journal'])
    check('all 22 events independently replayed', replay.snapshot() == result['final_snapshot'])
    check('final state matches independent cash and fees', D(replay.snapshot()['cash']) == cash and D(replay.snapshot()['fees_paid']) == fees)
    check('dividend settlement no false income', expected_daily[3]['simulated_net_asset_value'] == expected_daily[4]['simulated_net_asset_value'])
    check('unqualified result never promoted', result['execution_valid'] is False and result['formal_target_success'] is False
          and result['company_action_coverage_complete'] is False and not result['strategy_metrics_for_promotion'])
    return {'created_at':datetime.now(timezone.utc).isoformat(),'status':'fixed_case_arithmetic_and_journal_replay_passed',
        'attempt':str(attempt.relative_to(ROOT)),'result_sha256':sha(attempt/'result.json'),'audit_script_sha256':sha(Path(__file__)),
        'plan_sha256':sha(PILOT/'plan.json'),'checks':checks,'checks_passed':len(checks),'daily':expected_daily,
        'gross_dividend':money(gross_dividend),'dividend_tax':money(tax),'total_fees':money(fees),
        'slippage_already_in_fills':money(slip),'final_cash':money(cash),'net_pnl':money(cash-D(plan['initial_cash'])),
        'model_calls':0,'new_market_data_reads':0,'execution_valid':False,'formal_target_success':False,
        'limits':['one verified corporate action; wider coverage incomplete','fee/fill/status/capacity/arrival assumptions remain simulated',
                  'six-day accounting case; no Sharpe or general strategy claim','original zero-slippage attempt remains preserved']}


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(); parser.add_argument('--attempt',type=Path)
    args=parser.parse_args()
    attempt=args.attempt or ROOT/read(PILOT/'latest_attempt.json')['attempt']
    if not attempt.is_absolute(): attempt=ROOT/attempt
    out=attempt/'independent_arithmetic_audit.json'
    if out.exists(): raise RuntimeError('Audit artifact already exists; do not overwrite')
    result=audit(attempt)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'checks','daily'}},ensure_ascii=False))
