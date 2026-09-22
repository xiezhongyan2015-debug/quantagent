"""Exact price ticks and fresh quote validation. No account access."""
import datetime as dt
from decimal import Decimal
from quanta_agents.factor_lab_a.choice_pcf import TradeError, number

def stable_price_at_ask(ask,padding_ticks=0):
    """Avoid the observed float-times-100 truncation; never disguise quote values."""
    scaled=Decimal(str(ask))*100
    if scaled!=scaled.to_integral_value() or padding_ticks not in (0,1):
        raise TradeError('Invalid exact A-share tick/padding permission')
    for offset in range(padding_ticks+1):
        cents=int(scaled)+offset
        value=float(Decimal(cents)/100)
        if int(value*100)==cents:return value
    return None


def validate_quote(q,cfg,now):
    stamp=dt.datetime.fromisoformat(q['timestamp'])
    if stamp.tzinfo is None or not 0<=(now-stamp).total_seconds()<=cfg['quote_max_age_seconds']:
        raise TradeError('Missing, future or stale batch quote')
    if q['tradable']!=1 or q['is_st']!=0:raise TradeError('Halted or ST batch security')
    for k in ('bid','ask','last','lower_limit','upper_limit'):
        if number(q[k])<=0:raise TradeError('Invalid batch quote price')
    if not q['lower_limit']<=q['bid']<=q['ask']<=q['upper_limit']:
        raise TradeError('Invalid batch quote band')
