"""A-share unit compatibility; no strategy, pricing, account or order writes.

Main-board/ChiNext odd balances must be sold in one piece. Buy orders still
use whole hundreds. STAR keeps the executor's existing conservative policy.
"""
import re


def regular_stock(code):
    return bool(re.fullmatch(r'(?:SZ(?:00|30)\d{4}|SH60\d{4})', code))


def compatible_target(code, rounded, held):
    if any(type(n) is not int or n < 0 for n in (rounded, held)):
        raise ValueError('目标或持仓股数无效')
    # A whole-lot buy cannot change an existing odd remainder. Round the
    # incremental buy DOWN; never spend more or add a forced liquidation.
    if regular_stock(code) and held % 100 and rounded > held:
        return held + (rounded - held) // 100 * 100
    return rounded


def odd_reference(holdings, signal_date, account_sha256):
    odd = {c: n for c, n in holdings.items() if type(n) is int and n > 0 and n % 100 and regular_stock(c)}
    return dict(schema=1, signal_date=signal_date, account_sha256=account_sha256, holdings=odd)


def reference_holding(obj, code):
    ref = obj.get('odd_lot_reference', {})
    if (ref.get('schema') != 1 or ref.get('signal_date') != obj.get('signal_date')
            or not re.fullmatch(r'[0-9a-f]{64}', str(ref.get('account_sha256', '')))):
        return 0
    n = ref.get('holdings', {}).get(code, 0)
    return n if type(n) is int and n > 0 else 0


def target_allowed(code, quantity, reference_held=0):
    if type(quantity) is not int or quantity < 0:
        return False
    if code.startswith('SH688'):
        return quantity % 100 == 0 and (quantity == 0 or quantity >= 200)
    if quantity % 100 == 0:
        return True
    return (regular_stock(code) and type(reference_held) is int and reference_held > 0
            and quantity >= reference_held and quantity % 100 == reference_held % 100)


def sell_allowed(code, quantity, held, available):
    if any(type(n) is not int for n in (quantity, held, available)):
        return False
    if not 0 < quantity <= available <= held:
        return False
    if quantity % 100 == 0:
        return True
    # Require the WHOLE actual odd balance to be sellable. No split odd
    # sale and no assumption that locked bonus/transfer shares are T+1-ready.
    return (regular_stock(code) and 0 < held % 100 == available % 100 == quantity % 100)


def sell_quantity(code, desired, held, available, cap):
    if any(type(n) is not int or n < 0 for n in (desired, held, available, cap)) or available > held:
        raise ValueError('卖出缺口、可卖股数或单笔限额无效')
    maximum = min(desired, available, cap)
    remainder = held % 100
    if (regular_stock(code) and remainder and desired % 100 == remainder
            and available % 100 == remainder and maximum >= remainder):
        return (maximum - remainder) // 100 * 100 + remainder
    return maximum // 100 * 100


def deferred_notice(day, code, desired, held, available):
    from corporate_action_runtime import queue_notice
    return queue_notice('odd_lot_deferred',
        f'⚠️ {day} {code} 零股暂不能执行：目标差额{desired}股，持仓{held}股，可卖{available}股。'
        '保留真实持仓和目标，不拆零股、不超卖；其他符合条件的调仓按原规则继续。',
        f'odd-lot:{day}:{code}:{held % 100}:{available % 100}')


if __name__ == '__main__':
    import sys
    if len(sys.argv) == 4 and sys.argv[1] == '--pipeline-alert':
        from corporate_action_runtime import queue_notice
        day, reason = sys.argv[2], sys.argv[3]
        queue_notice('pipeline_failed', f'⚠️ {day} 日更流水线失败：{reason[:300]}。明日目标发布/预约未全部完成，请检查流水线日志；保留原风控。', f'pipeline:{day}:{reason[:300]}')
    else:
        raise SystemExit('Unsupported diagnostic action')
