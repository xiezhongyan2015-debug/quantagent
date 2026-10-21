"""Evidence attribution and preregistered revision, without choosing a cause.

All statistics describe saved full-capital paths. Fee add-backs are arithmetic
on that fixed path, not reruns or causal estimates. New data/scope permissions
remain with the controller, outside model-generated programs.
"""
from collections import Counter,defaultdict
from decimal import Decimal
import re
from .ledger import need,digest

NEW_ACTIONS=('diagnose_execution','register_experiment','request_research_extension','register_batch','execute_batch','inspect_batch')


def action_limits(case,defaults):
    policy=case.get('research_policy')
    if policy is None:return {**defaults,**dict.fromkeys(NEW_ACTIONS,0)}
    need(type(policy) is dict and set(policy)=={'version','action_limits'},'exact frozen research-policy fields')
    need(policy['version']=='structural_research_v1','unknown research-policy version')
    proposed=policy['action_limits'];known=set(defaults)|set(NEW_ACTIONS)
    need(type(proposed) is dict and set(proposed)<=known,'unknown action allocation')
    need(all(type(n) is int and 0<=n<=32 for n in proposed.values()),'action allocation must be0..32')
    return {**defaults,**dict.fromkeys(NEW_ACTIONS,0),**proposed}


def _decimal(v):
    value=Decimal(str(v));need(value.is_finite(),'nonfinite saved accounting value');return value


def executable_program_hash(program):
    """Exact executable syntax identity; not general semantic equivalence."""
    return digest({key:program[key] for key in ('version','factors','target_weight_expression')})


def account_attribution(evidence_id,artifact,calendar=None):
    raw=artifact.get('raw') or {};complete=raw.get('result');body=complete or raw.get('partial') or {}
    days=body.get('daily',[]);initial=body.get('initial_cash')
    snap=body.get('final_snapshot')
    if initial is None and snap:initial=snap.get('external_cash_flow')
    capital=_decimal(initial) if initial is not None else None
    need(capital is None or capital>0,'positive full-capital denominator required')
    need(not days or capital is not None,'saved cash-day series lacks initial capital')
    daily=[];months=defaultdict(lambda:Decimal(0));high=capital;max_dd=Decimal(0);prior=capital;exposure=[]
    for row in days:
        v=row.get('valuation',{})
        nav=_decimal(row.get('simulated_net_asset_value',v.get('simulated_net_asset_value')))
        cash=_decimal(row.get('cash_available',row.get('cash')))
        gross=_decimal(row.get('gross_asset_value',v.get('gross_asset_value')))
        receivable=_decimal(row.get('cash_receivable_gross',v.get('raw_ledger_valuation',{}).get('cash_receivable_gross',0)))
        marked_shares=gross-cash-receivable
        delta=nav-prior;high=max(high,nav);dd=1-nav/high;max_dd=max(max_dd,dd)
        exposure.append(marked_shares/gross if gross>0 else None)
        daily.append({'date':row['date'],'nav':str(nav),'net_change':str(delta),'cash':str(cash),
            'marked_share_value':str(marked_shares),'gross_receivable':str(receivable)})
        months[row['date'][:7]]+=delta;prior=nav
    trades=[]
    for row in body.get('trades',[]):
        if 'receipt' in row:
            r=row['receipt'];o=row['order']
            if not r['filled_quantity']:continue
            trades.append({'date':row['date'],'symbol':row['symbol'],'side':o['side'],'quantity':r['filled_quantity'],
                'notional':_decimal(r['notional']),'fees':_decimal(r['fees']['total']),'slippage':None})
        else:
            trades.append({'date':row['date'],'symbol':row['symbol'],'side':row['side'],'quantity':row['quantity'],
                'notional':_decimal(row['raw_price'])*row['quantity'],'fees':_decimal(row['fees']['total']),
                'slippage':_decimal(row['slippage_amount']) if 'slippage_amount' in row else None})
    fees=sum((t['fees'] for t in trades),Decimal(0));turnover=sum((t['notional'] for t in trades),Decimal(0))
    last_sale={};reentries=[];unresolved_reentries=[]
    # Never derive a trading calendar by dropping the unvalued sessions.
    day_index={d:i for i,d in enumerate(calendar or [r['date'] for r in daily])}
    for trade in trades:
        code=trade['symbol'];i=day_index.get(trade['date'])
        if trade['side']=='sell':last_sale[code]=trade['date']
        elif code in last_sale:
            sale_index=day_index.get(last_sale[code]);gap=None if i is None or sale_index is None else i-sale_index
            item={'symbol':code,'sale_date':last_sale[code],'buy_date':trade['date'],
                'session_gap':gap,'buy_quantity':trade['quantity'],'meaning':'sale then buy, including partial reduction and replenishment; not proof of a flat-position reentry'}
            if gap is None:unresolved_reentries.append(item)
            elif 0<=gap<=1:reentries.append(item)
            last_sale.pop(code)
    returns=[_decimal(r['net_change']) for r in daily];top=sorted(daily,key=lambda r:_decimal(r['net_change']),reverse=True)[:5]
    statuses=Counter(o['status'] for o in body.get('orders',[]))
    if not statuses:statuses=Counter(r['receipt']['status'] for r in body.get('trades',[]) if 'receipt' in r)
    finished=complete is not None and bool(daily)
    positions=defaultdict(int)
    for lot in (snap or {}).get('lots',{}).values():positions[lot['symbol']]+=lot['quantity']
    rights=[{'action_id':key,**value} for key,value in (snap or {}).get('actions',{}).items()]
    valued_dates={r['date'] for r in daily}
    unvalued=[{k:str(v) if isinstance(v,Decimal) else v for k,v in t.items()} for t in trades if t['date'] not in valued_dates]
    result={'evidence_id':evidence_id,'program_hash':digest(artifact['program']),
        'executable_program_hash':executable_program_hash(artifact['program']),'status':raw.get('status'),
        'complete_account':finished,'initial_cash':str(capital) if capital is not None else None,
        'statistics_scope':'complete saved calendar' if finished else 'valued prefix only; trades and snapshot can extend beyond it',
        'last_complete_date':daily[-1]['date'] if daily else None,'last_valued_nav':str(prior) if daily else None,
        'valued_prefix_net_pnl':str(prior-capital) if daily else None,
        'final_nav':str(prior) if finished else None,'net_pnl':str(prior-capital) if finished else None,
        'return_on_full_initial_cash':str(prior/capital-1) if finished else None,
        'maximum_drawdown':str(max_dd) if finished else None,'valued_prefix_maximum_drawdown':str(max_dd) if daily else None,
        'all_cash_days':sum(_decimal(r['marked_share_value'])==0 for r in daily),
        'saved_cash_days':len(daily),'trade_count':len(trades),'turnover_multiple_of_initial_cash':str(turnover/capital) if capital else None,
        'fees_on_recorded_trades':str(fees),'fixed_path_fee_drag_on_initial_cash':str(fees/capital) if capital else None,
        'recorded_slippage_in_prices':None if any(t['slippage'] is None for t in trades) else str(sum((t['slippage'] for t in trades),Decimal(0))),
        'mean_marked_share_fraction_of_gross_assets':None if not exposure or any(x is None for x in exposure) else str(sum(exposure)/len(exposure)),
        'positive_cash_day_changes':str(sum((x for x in returns if x>0),Decimal(0))),
        'negative_cash_day_changes':str(sum((x for x in returns if x<0),Decimal(0))),
        'top_positive_day_changes':[r for r in top if _decimal(r['net_change'])>0],
        'next_session_or_same_session_reentry_count':len(reentries),'reentries':reentries,
        'reentry_count_definition':'sale then buy including reductions/replenishments; not necessarily flat-position reentry',
        'unresolved_reentries':unresolved_reentries,'unvalued_trades':unvalued,'unvalued_trade_count':len(unvalued),
        'saved_account_state':{'snapshot_hash':digest(snap) if snap else None,'cash':(snap or {}).get('cash'),
            'fees_paid':(snap or {}).get('fees_paid'),'positions':dict(positions),'rights_count':len(rights),
            'last_effective_at':(snap or {}).get('last_effective_at'),'pending_orders':(snap or {}).get('pending_orders')},
        'rights':rights,'lots':[{'lot_id':k,**v} for k,v in (snap or {}).get('lots',{}).items()],
        'order_status_counts':dict(statuses),'rejection_count':len(body.get('rejections',[])),
        'monthly':[{'month':m,'net_change':str(value)} for m,value in sorted(months.items())],
        'daily':daily,'error':raw.get('error'),
        'unknowns':['Exposure is a marked asset fraction, not beta or matched benchmark risk.',
            'Net daily changes include market movements, rights, taxes and costs; they are not isolated trading alpha.',
            'Reentry counts do not prove the cause of a loss. A registered distinguishing experiment is needed.',
            'Actual broker costs, market fills and independent OOS are not certified by this attribution.',
            'Partial daily statistics cover only the valued prefix. Later fills, costs and rights remain saved; final NAV and return are unknown.']}
    return result


def diagnose(items,calendar=None):
    accounts=[account_attribution(eid,a,calendar) for eid,a in items]
    reference=accounts[0];pairs=[]
    for other in accounts[1:]:
        aligned=bool(reference['complete_account'] and other['complete_account'] and reference['daily'] and other['daily'] and reference.get('initial_cash')==other.get('initial_cash') and
            [r['date'] for r in reference['daily']]==[r['date'] for r in other['daily']])
        pair={'reference_evidence_id':reference['evidence_id'],'comparison_evidence_id':other['evidence_id'],'same_cash_calendar_and_initial_capital':aligned}
        if aligned:
            pair.update(comparison_minus_reference_net_pnl=str(_decimal(other['net_pnl'])-_decimal(reference['net_pnl'])),
                comparison_minus_reference_fees=str(_decimal(other['fees_on_recorded_trades'])-_decimal(reference['fees_on_recorded_trades'])),
                comparison_minus_reference_maximum_drawdown=str(_decimal(other['maximum_drawdown'])-_decimal(reference['maximum_drawdown'])),
                daily=[{'date':a['date'],'comparison_minus_reference_nav':str(_decimal(b['nav'])-_decimal(a['nav']))} for a,b in zip(reference['daily'],other['daily'])])
        else:pair['reason']='No final comparison across incomplete accounts, different dates or capital; originals retained'
        pairs.append(pair)
    return {'kind':'saved_execution_attribution_v1','input_evidence_ids':[i[0] for i in items],
        'accounts':accounts,'pairs':pairs,'causal_root_cause_identified':False,'formal_target_success':False,
        'interpretation':'Computed facts for competing mechanism judgments; no automatic root cause, retuning, rerun, significance or OOS claim.'}


def public_diagnosis(value):
    return {**{k:v for k,v in value.items() if k not in ('accounts','pairs')},
        'accounts':[{k:v for k,v in a.items() if k not in ('daily','reentries','top_positive_day_changes','monthly','unknowns','unvalued_trades','unresolved_reentries','rights','lots')} for a in value['accounts']],
        'pairs':[{k:v for k,v in p.items() if k!='daily'} for p in value['pairs']],
        'detail_query':'read_evidence tables attribution_accounts|attribution_monthly|attribution_pairs|attribution_unvalued_trades|attribution_rights|attribution_lots|attribution_unresolved_reentries'}


def register_experiment(args,baseline,diagnosis,validate,fields):
    required={'baseline_evidence_id','diagnosis_evidence_id','mechanism_hypotheses','distinguishing_prediction',
        'structural_change','revision_program','success_rule','failure_rule'}
    need(set(args) in (required,required|{'contrast_checks'}),'exact experiment fields')
    need(args['baseline_evidence_id'] in diagnosis['input_evidence_ids'],'diagnosis does not include baseline')
    need(type(args['mechanism_hypotheses']) is list and 1<=len(args['mechanism_hypotheses'])<=6 and
        all(type(s) is str and 0<len(s)<=1500 for s in args['mechanism_hypotheses']),'bounded competing mechanism hypotheses')
    if 'contrast_checks' in args:
        from .experiment_contrast import validate as validate_contrast
        validate_contrast(args['contrast_checks'],len(args['mechanism_hypotheses']))
    for key in ('distinguishing_prediction','structural_change','success_rule','failure_rule'):
        need(type(args[key]) is str and 0<len(args[key])<=2000,'bounded experiment declaration: '+key)
    compiled=validate(args['revision_program'],public_field_contract=fields)
    need(digest(args['revision_program'])!=digest(baseline['program']),'revision must differ from frozen baseline')
    need(executable_program_hash(args['revision_program'])!=executable_program_hash(baseline['program']),
        'narrative-only revision: identical executable rules; reference the original evidence instead of executing again')
    return {'kind':'preregistered_revision_v1','declaration':args,'baseline_program_hash':digest(baseline['program']),
        'revision_program_hash':digest(args['revision_program']),'compiled_program_hash':compiled['program_hash'],
        'baseline_executable_program_hash':executable_program_hash(baseline['program']),
        'revision_executable_program_hash':executable_program_hash(args['revision_program']),
        'diagnosis_hash':digest(diagnosis),'executed':False,'success_not_evaluated':True,
        'scope_rule':'same currently frozen inputs; universe/data/asset changes need a controller-admitted new scope',
        'formal_target_success':False}


def extension_request(args,known_ids,current_case_hash):
    required={'evidence_ids','problem','request_kind','specification','expected_information_gain','requested_resource_bounds'}
    need(set(args) in (required,required|{'requested_fields'}),'exact extension request fields')
    need(type(args['evidence_ids']) is list and 1<=len(args['evidence_ids'])<=8 and all(x in known_ids for x in args['evidence_ids']),'extension requires local settled evidence')
    need(args['request_kind'] in ('data','universe','benchmark','tool','asset_class'),'unknown extension kind')
    if args['request_kind']=='data':
        fields=args.get('requested_fields')
        need(type(fields) is list and 1<=len(fields)<=8 and len(set(fields))==len(fields) and
             all(type(f) is str and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,39}',f) for f in fields),
             'data request requires explicit requested_fields deliverables')
    else:need('requested_fields' not in args,'requested_fields applies only to data extension')
    for key in ('problem','specification','expected_information_gain'):
        need(type(args[key]) is str and 0<len(args[key])<=4000,'bounded extension explanation')
    bounds=args['requested_resource_bounds']
    need(type(bounds) is dict and set(bounds)=={'symbols','sessions','model_calls','download_bytes','wall_seconds'},'explicit extension resource dimensions')
    need(all(type(v) is int and 0<=v<=10**10 for v in bounds.values()),'nonnegative requested limits; request is not authority')
    return {'kind':'research_extension_request_v1','request':args,'current_case_hash':current_case_hash,
        'status':'pending_controller_admission','budget_granted':False,'new_data_loaded':False,'current_scope_changed':False,
        'formal_target_success':False,'next':'Controller must register a separate bounded scope and source/asset contract before execution. Unsupported futures/index semantics must remain explicit.'}
