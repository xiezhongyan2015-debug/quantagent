"""Controller-side decimal-to-digit projection with every source row retained.

No data is acquired and no rounding recovers lost precision. A saved trace
proves this transformation, not that the provider's original unit/tick is true.
"""
from decimal import Decimal,InvalidOperation
import re

from .ledger import need,digest


def derive(records,*,name,source_unit,minimum_increment,base=10,place=0,width=1):
    need(type(records) is list and 1<=len(records)<=8192,'bounded raw-text coordinates')
    need(type(name) is str and re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,39}',name),'derived field name')
    need(type(source_unit) is str and 0<len(source_unit)<=100,'source unit required')
    need(type(minimum_increment) is str and len(minimum_increment)<=40,'increment must preserve decimal text')
    increment=Decimal(minimum_increment);need(increment.is_finite() and increment>0,'positive exact increment')
    need(abs(increment.as_tuple().exponent)<=100 and abs(increment.adjusted())<=60,'increment magnitude bound')
    need(type(base) is int and 2<=base<=16 and type(place) is int and 0<=place<=6 and type(width) is int and 1<=width<=3,'bounded digit coordinates')
    seen=set();trace=[]
    for row in records:
        need(type(row) is dict and set(row)=={'session','symbol','value_text','unit','effective_at','available_at','source_sha256'},'exact raw-text source fields')
        key=(row['session'],row['symbol']);need(key not in seen,'duplicate digit coordinate');seen.add(key)
        need(type(row['source_sha256']) is str and re.fullmatch('[a-f0-9]{64}',row['source_sha256']),'pinned raw source identity')
        text=row['value_text'];value=None;units=None;reason=None
        if row['unit']!=source_unit:reason='source_unit_mismatch'
        elif text is None:reason='source_value_missing'
        elif type(text) is not str or len(text)>100:reason='exact_raw_text_required_no_float_reconstruction'
        else:
            try:
                number=Decimal(text)
                if not number.is_finite() or number<0:reason='nonfinite_or_negative_source_value'
                elif abs(number.as_tuple().exponent)>100 or abs(number.adjusted())>60:reason='source_decimal_magnitude_exceeds_bound'
                else:
                    # Integer ratio uses decimal tuples, avoiding Decimal's
                    # default28-digit context rounding of division.
                    numerator,denominator=number.as_integer_ratio();a,b=increment.as_integer_ratio()
                    quotient,remainder=divmod(numerator*b,denominator*a)
                    if remainder:reason='source_value_not_exact_multiple_no_rounding'
                    else:units=quotient;value=(units//base**place)%base**width
            except InvalidOperation:reason='invalid_decimal_text'
        trace.append({'source':row,'integer_units':str(units) if units is not None else None,'digit':value,'reason':reason})
    specification={'name':name,'source_unit':source_unit,'minimum_increment':minimum_increment,'base':base,'place':place,'width':width}
    evidence={'kind':'exact_discrete_field_derivation_v1','specification':specification,'trace':trace,
        'retained_rows':len(trace),'usable_rows':sum(r['digit'] is not None for r in trace),'source_veracity_certified':False,
        'interpretation':'Exact arithmetic on admitted original strings; no inferred precision, filled gaps, source timing or market alpha proof.'}
    proof=digest(evidence)
    field_rows=[{'session':t['source']['session'],'symbol':t['source']['symbol'],'field':name,'value':t['digit'],
        'effective_at':t['source']['effective_at'],'available_at':t['source']['available_at'],'source_evidence_id':proof} for t in trace]
    unit=f'digit_base{base}_place{place}_width{width}_of_{source_unit}'
    semantics={'unit':unit,'raw_precision':'original decimal text retained; nonintegral conversion masked without rounding',
        'digit_derivation':{'source_unit':source_unit,'minimum_increment':minimum_increment,'integer_conversion':'exact_decimal_no_rounding',
            'source_evidence_sha256':proof,'base':base,'place':place,'width':width}}
    return {'field':{'name':name,'unit':unit},'field_rows':field_rows,'evidence':evidence,'field_semantics':semantics}
