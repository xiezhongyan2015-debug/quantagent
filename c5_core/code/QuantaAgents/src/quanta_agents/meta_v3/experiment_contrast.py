"""Evaluate declared scalar contrasts without selecting a cause or strategy.

All deltas are revision minus baseline on the same complete funded calendar.
Matching a predicted pattern is not a causal identification or an OOS result.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation, localcontext
import operator
import re

from .ledger import digest,need

VERSION='registered_revision_contrast_v1'
METRICS={
    'net_pnl':'account currency on the full initial capital',
    'fees_on_recorded_trades':'account currency; recorded transaction fees',
    'recorded_slippage_in_prices':'account currency; already embedded in prices, never a second expense',
    'turnover_multiple_of_initial_cash':'turnover divided by full initial capital',
    'maximum_drawdown':'fraction of the account high-water mark',
    'mean_marked_share_fraction_of_gross_assets':'mean marked equity fraction, not beta or matched risk',
    'all_cash_days':'number of saved days with zero marked equity value',
    'trade_count':'number of recorded filled trades',
    'next_session_or_same_session_reentry_count':'sale-then-buy count, including partial changes; not proof of flat-position reentry',
}
OPERATORS={'lt':operator.lt,'lte':operator.le,'eq':operator.eq,'gte':operator.ge,'gt':operator.gt}
NUMBER=re.compile(r'-?(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,12})?\Z')


def validate(checks,hypothesis_count):
    need(type(checks) is list and 1<=len(checks)<=6,'contrast_checks must contain 1..6 declared checks')
    ids=set()
    for c in checks:
        need(type(c) is dict and set(c)=={'id','hypothesis_index','metric','operator','threshold'},'exact contrast check fields')
        need(type(c['id']) is str and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,31}',c['id']) is not None and c['id'] not in ids,
             'unique bounded contrast check id')
        ids.add(c['id'])
        need(type(c['hypothesis_index']) is int and 0<=c['hypothesis_index']<hypothesis_count,'contrast hypothesis index outside declaration')
        need(type(c['metric']) is str and c['metric'] in METRICS,'unsupported contrast metric')
        need(type(c['operator']) is str and c['operator'] in OPERATORS,'unsupported contrast operator')
        need(type(c['threshold']) is str and NUMBER.fullmatch(c['threshold']) is not None,'contrast threshold must be a bounded decimal string')


def _value(value):
    if value is None or isinstance(value,bool):return None
    try:result=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):return None
    if not result.is_finite():return None
    parts=result.as_tuple()
    return result if len(parts.digits)<=128 and abs(parts.exponent)<=128 and abs(result.adjusted())<=128 else None


def evaluate(registration,attribution,calendar,initial_cash):
    """Use frozen checks and saved attribution; never read or execute a program."""
    declaration=registration['declaration'];checks=declaration['contrast_checks']
    validate(checks,len(declaration['mechanism_hypotheses']))
    accounts=attribution.get('accounts',[]);reason=None
    if len(accounts)!=2:
        reason='expected exactly the baseline and registered revision accounts'
        baseline=revision={}
    else:
        baseline,revision=accounts
        if (baseline.get('program_hash')!=registration['baseline_program_hash'] or
            revision.get('program_hash')!=registration['revision_program_hash'] or
            baseline.get('evidence_id')!=declaration['baseline_evidence_id'] or
            revision.get('evidence_id')!='registered_revision'):
            reason='saved program or evidence identity differs from the frozen registration'
        elif baseline.get('complete_account') is not True or revision.get('complete_account') is not True:
            reason='an account is incomplete; partial observations cannot satisfy the prediction'
        elif (not calendar or len(set(calendar))!=len(calendar) or
              any([row.get('date') for row in a.get('daily',[])]!=list(calendar) for a in accounts)):
            reason='both accounts must retain every session of the same frozen calendar in order'
        else:
            left,right=_value(baseline.get('initial_cash')),_value(revision.get('initial_cash'))
            expected=_value(initial_cash)
            if expected is None or expected<=0 or left!=expected or right!=expected:
                reason='both accounts must retain the full positive initial capital of the frozen case'
    rows=[]
    for check in checks:
        left=_value(baseline.get(check['metric']));right=_value(revision.get(check['metric']))
        unknown=reason or ('required saved metric is unknown or nonfinite' if left is None or right is None else None)
        with localcontext() as ctx:
            ctx.prec=260
            delta=None if unknown else right-left
        state='unevaluable' if unknown else ('matched' if OPERATORS[check['operator']](delta,Decimal(check['threshold'])) else 'contradicted')
        rows.append({**check,'unit':METRICS[check['metric']],
            'baseline_value':str(left) if left is not None else None,'revision_value':str(right) if right is not None else None,
            'revision_minus_baseline':str(delta) if delta is not None else None,'status':state,'reason':unknown})
    counts=Counter(r['status'] for r in rows)
    groups=[]
    for i,hypothesis in enumerate(declaration['mechanism_hypotheses']):
        group=Counter(r['status'] for r in rows if r['hypothesis_index']==i)
        groups.append({'hypothesis_index':i,'hypothesis':hypothesis,'declared_checks':sum(group.values()),
            **{k:group[k] for k in ('matched','contradicted','unevaluable')}})
    return {'kind':VERSION,'checks_hash':digest(checks),'registration_hash':digest(registration),
        'account_evidence_ids':[a.get('evidence_id') for a in accounts],'frozen_initial_cash':str(initial_cash),
        'frozen_calendar_hash':digest(calendar),
        'account_calendar_hashes':[digest([row.get('date') for row in a.get('daily',[])]) for a in accounts],
        'comparison':'revision minus baseline; no outcome-dependent metric selection',
        'checks':rows,'counts':{k:counts[k] for k in ('matched','contradicted','unevaluable')},'hypotheses':groups,
        'causal_mechanism_identified':False,'formal_target_success':False,
        'interpretation':'These are observations against frozen predictions. A match does not identify the cause, validate the prose criteria, select a strategy, adjust exposure, or certify independent future profitability.'}
