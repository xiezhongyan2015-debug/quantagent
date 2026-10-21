"""Pure position-floor planning and deadline rules; no account or network access."""
import datetime as dt
from decimal import Decimal, ROUND_CEILING

POLICY_DEFAULTS = dict(enabled=False, buffer_fraction=.005, effective_date='2026-09-23',
    first_deadline='2026-09-23', recovery_days=3, recovery_unit='trading_days')


def D(value):
    number=Decimal(str(value))
    if not number.is_finite():raise ValueError('仓位规则数值必须有限')
    return number


def checked_policy(value,target):
    if not isinstance(value,dict) or set(value)-set(POLICY_DEFAULTS):raise ValueError('最低仓位规则格式错误')
    p=dict(POLICY_DEFAULTS,**value)
    if type(p['enabled']) is not bool:raise ValueError('最低仓位开关必须为布尔值')
    if not 0<=D(p['buffer_fraction'])<=D('.02'):raise ValueError('仓位缓冲必须在0至2个百分点内')
    if p['enabled'] and D(target)+D(p['buffer_fraction'])>=1:raise ValueError('最低仓位和缓冲需给手续费保留现金')
    for key in ('effective_date','first_deadline'):
        if dt.date.fromisoformat(p[key]).isoformat()!=p[key]:raise ValueError('最低仓位日期无效')
    if p['first_deadline']<p['effective_date']:raise ValueError('首次达标日早于生效日')
    if type(p['recovery_days']) is not int or p['recovery_days']!=3:raise ValueError('当前规则要求3天内恢复')
    if p['recovery_unit'] not in ('trading_days','calendar_days'):raise ValueError('恢复期限单位无效')
    return p


def active(config,day):
    p=config.get('position_floor',POLICY_DEFAULTS)
    return p['enabled'] and day>=p['effective_date']


def plan_fraction(config):
    return D(config['target_fraction'])+D(config['position_floor']['buffer_fraction'])


def allocate(weights,prices,holdings,nav,config):
    """Proportional lots plus largest relative deficits, rounding UP to the budget.

    Existing STAR positions require an executable >=200-share adjustment. Targets
    never exceed the security or total capital cap to complete the floor.
    """
    money=D(nav)*plan_fraction(config);single=D(config['max_security_value']);cap=D(config['capital_limit'])
    weights={c:D(w) for c,w in weights.items() if D(w)>0}
    if not weights or money>cap:raise ValueError('资金上限不足以支持最低仓位及缓冲')
    prices={c:D(p) for c,p in prices.items()}
    if any(prices[c]<=0 for c in set(weights)|set(holdings)):raise ValueError('仓位分配参考价无效')
    buy_only=not config['allow_sells']
    retained=sum(holdings[c]*prices[c] for c in holdings if c not in weights) if buy_only else D(0)
    total=sum(weights.values());ideal={c:max(D(0),money-retained)*w/total for c,w in weights.items()}
    if max(ideal.values())>single:raise ValueError('PCF相对权重超过单股限额，不能自动放宽')
    target={c:holdings.get(c,0) if buy_only else int(ideal[c]/prices[c]//100)*100 for c in weights}
    for c,n in list(target.items()):
        old=holdings.get(c,0)
        if c.startswith('SH688'):
            if n<200 and not buy_only:n=0
            delta=n-old
            if 0<delta<200:n=old+200
            elif -200<delta<0 and n!=0:n=old
        target[c]=n
    value=retained+sum(n*prices[c] for c,n in target.items())
    if value>cap or any(n*prices[c]>single for c,n in target.items()):raise ValueError('可执行整手调整超过限额')
    while value<money:
        choices=[]
        for c,n in target.items():
            lot=100
            if c.startswith('SH688') and (n==0 or n==holdings.get(c,0)):lot=200
            while c.startswith('SH688') and 0<abs(n+lot-holdings.get(c,0))<200:
                lot+=100
            extra=lot*prices[c]
            if (n+lot)*prices[c]>single or value+extra>cap:continue
            choices.append(((ideal[c]-n*prices[c])/extra,-extra,c,lot))
        if not choices:raise ValueError('现有限额与整数手约束无法补足最低仓位')
        _,_,c,lot=max(choices);target[c]+=lot;value+=lot*prices[c]
    return target,value


def recovery_deadline(start,calendar,policy):
    days=policy['recovery_days']
    if policy['recovery_unit']=='trading_days':
        eligible=[day for day in calendar if day>=start]
        if len(eligible)<days:raise ValueError('交易日历不足以计算仓位恢复期限')
        return eligible[days-1]  # Detection day counts as day 1: the conservative deadline.
    due=(dt.date.fromisoformat(start)+dt.timedelta(days=days-1)).isoformat()
    if not calendar or calendar[-1]<due:raise ValueError('交易日历不足以计算自然日恢复期限')
    eligible=[day for day in calendar if start<=day<=due]
    if not eligible:raise ValueError('三天期限内无可执行交易日，需人工核对规则')
    return eligible[-1]


def observe(previous,audit,day,config,calendar):
    if not active(config,day):return dict(enabled=False)
    minimum=D(config['target_fraction']);nav=D(audit['total_assets']);market=D(audit['market_value'])
    if nav<=0:raise ValueError('账户净资产无效，不能判定仓位达标')
    met=market>=minimum*nav
    result=dict(enabled=True,minimum_fraction=float(minimum),planning_fraction=float(plan_fraction(config)),
        actual_fraction=float(market/nav),met=met,gap_cny=str(max(D(0),minimum*nav-market)),
        checked_day=day,checked_at=audit.get('checked_at'),recovery_unit=config['position_floor']['recovery_unit'])
    if met:
        result.update(breach_since=None,deadline=None,last_met_day=day)
        return result
    previous=previous or {};policy=config['position_floor']
    since=previous.get('breach_since') or day
    deadline=recovery_deadline(since,calendar,policy)
    if since<=policy['first_deadline']:
        deadline=min(deadline,policy['first_deadline'])
    result.update(breach_since=since,deadline=deadline,last_met_day=previous.get('last_met_day'),
                  due_today=day>=deadline)
    return result


def topup_quantity(code,price,mark,held,gap_value,cash_room,config):
    """Whole-lot capacity after conservative fees; never relax order/security caps."""
    price=D(price);mark=max(D(mark),price);room=D(cash_room)
    quantity=int((D(gap_value)/price/100).to_integral_value(rounding=ROUND_CEILING))*100
    quantity=min(quantity,int(D(config['max_order_value'])/price//100)*100,
        int(max(D(0),D(config['max_security_value'])/mark-held)//100)*100,
        int(max(D(0),(room-10)/D('1.001')/price)//100)*100)
    minimum=200 if code.startswith('SH688') else 100
    return quantity if quantity>=minimum else 0
