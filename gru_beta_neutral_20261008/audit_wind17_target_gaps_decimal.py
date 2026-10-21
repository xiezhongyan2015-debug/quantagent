"""Correct amount-bound arithmetic at exact one-currency-unit boundaries.

The prior float audit and the coarse query's original basis remain immutable.
No admission tolerance, quote, or quantity is changed.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from audit_wind17_target_gaps import day_check as float_day_check, OUT, SOURCE, END, sha


def day_check(day, primary):
    result = float_day_check(day, primary)
    if not result.get('grid_valid'):
        return result
    day = day.sort_values('time')
    clock = day.time.dt.hour * 60 + day.time.dt.minute
    regular = day[clock.isin(END)]
    auction = day[(clock == 570) & day.volume_lots.gt(0)]
    conflicts = 0
    for index, row in enumerate(regular.itertuples()):
        low, high = Decimal(str(row.low)), Decimal(str(row.high))
        volume, amount = Decimal(str(row.volume_lots)) * 100, Decimal(str(row.amount))
        if index == 0 and len(auction):
            a = auction.iloc[0]
            low, high = min(low, Decimal(str(a.low))), max(high, Decimal(str(a.high)))
            volume += Decimal(str(a.volume_lots)) * 100
            amount += Decimal(str(a.amount))
        conflicts += not low * volume - 1 <= amount <= high * volume + 1
    result['minute_amount_vs_reported_price_times_lot_volume_conflicts'] = conflicts
    result['amount_arithmetic'] = 'Exact Decimal arithmetic; unchanged tolerance of one currency unit'
    return result


def main():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gru_research/.deps'))
    import pyarrow
    import pandas as pd
    from audit_wind17_target_gaps import FIELDS
    original_path = OUT / 'target_gap_audit_receipt.json'
    original = json.loads(original_path.read_text())
    result = json.loads(original_path.read_text())
    result.update(checked_utc=datetime.now(timezone.utc).isoformat(), source_code_sha256=sha(__file__),
                  base_daily_helper_path=str(Path(__file__).parent / 'audit_wind17_target_gaps.py'),
                  base_daily_helper_sha256=sha(Path(__file__).parent / 'audit_wind17_target_gaps.py'),
                  prior_float_audit_path=str(original_path), prior_float_audit_sha256=sha(original_path))
    changes = []
    for stock in result['results']:
        frame = pd.read_csv(stock['path'], encoding='gbk').rename(columns=FIELDS)
        frame['time'] = pd.to_datetime(frame['k线结束时间'])
        primary = json.loads(Path(stock['primary_daily_path']).read_text())
        daily = {row[0]: dict(zip(primary['fields'], row)) for row in primary['rows']}
        for old in stock['target_stockday_checks']:
            checked = day_check(frame[frame.time.dt.strftime('%Y-%m-%d').eq(old['date'])], daily[old['date']])
            before = old['minute_amount_vs_reported_price_times_lot_volume_conflicts']
            after = checked['minute_amount_vs_reported_price_times_lot_volume_conflicts']
            if before != after:
                changes.append({'symbol': stock['source_symbol'], 'year': stock['source_year'], 'date': old['date'],
                                'float_conflicts': before, 'Decimal_conflicts': after})
            old['minute_amount_vs_reported_price_times_lot_volume_conflicts'] = after
            old['amount_arithmetic'] = checked['amount_arithmetic']
    result['exact_amount_arithmetic_corrections'] = changes
    result['prior_daily_price_volume_gates_changed'] = False
    path = OUT / 'target_gap_audit_decimal_receipt.json'
    assert not path.exists()
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'amount_boundary_counts_corrected': len(changes), 'changes': changes}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
