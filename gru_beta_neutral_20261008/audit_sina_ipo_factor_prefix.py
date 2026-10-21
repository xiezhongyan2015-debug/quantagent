"""Compare observed public IPO factor seeds without replacing canonical factors."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes'
SOURCE = BASE / 'sina_ipo_factor_prefix_20261009'
PRIMARY = BASE / 'baostock/union_daily_factors/normalized'
LISTINGS = {
    '688220': ('2022-01-14', 'https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20220113_84075081.shtml'),
    '688234': ('2022-01-12', 'https://static.cninfo.com.cn/finalpage/2022-01-11/1212136661.PDF'),
    '688331': ('2022-03-31', 'https://www.remegen.com/index.php?cid=115&id=166&v=show'),
    '688582': ('2023-06-30', 'https://www.sse.com.cn/disclosure/announcement/listing/ipo/c/c_20230629_90144324.shtml'),
    '688709': ('2024-02-07', 'https://big5.sse.com.cn/site/cht/www.sse.com.cn/disclosure/listedinfo/announcement/c/new/2024-02-06/688709_20240206_JBMI.pdf'),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    receipt = json.loads((SOURCE / 'receipt.json').read_text())
    records = []
    for code, (listing, url) in LISTINGS.items():
        raw_path = SOURCE / (code+'.js')
        raw = raw_path.read_text()
        offset = raw.index('{')
        parsed, _ = json.JSONDecoder().raw_decode(raw[offset:])
        events = sorted((r['d'], Decimal(r['f'])) for r in parsed['data'] if r['d'] != '1900-01-01')
        assert events and events[0][0] == listing
        assert all(f.is_finite() and f > 0 for _, f in events)
        p = PRIMARY / (code+'.SH.json')
        primary = json.loads(p.read_text())
        known = sorted((r[1], Decimal(r[2])) for r in primary['factors'])
        first_date, first_value = known[0]
        assert listing < first_date
        reference = [event for event in events if event[0] <= first_date][-1]
        scale = reference[1] * first_value
        comparisons = []
        for date, canonical in known:
            source_date, divisor = [e for e in events if e[0] <= date][-1]
            converted = scale / divisor
            error = abs(converted - canonical)
            # Canonical factors have six decimal places. Preserve them exactly;
            # only compare against their published quantization interval.
            agrees = error <= Decimal('0.000000500000001')
            comparisons.append({'date': date, 'canonical_fore_multiplier': str(canonical),
                                'source_effective_date': source_date, 'sina_qfq_divisor': str(divisor),
                                'anchor_aligned_sina_multiplier': str(converted),
                                'absolute_difference': str(error), 'within_canonical_rounding_precision': agrees})
        prefix_days = [r[primary['fields'].index('date')] for r in primary['rows']
                       if listing <= r[primary['fields'].index('date')] < first_date
                       and r[primary['fields'].index('tradestatus')] == '1'
                       and r[primary['fields'].index('volume')] not in ['', None]
                       and Decimal(r[primary['fields'].index('volume')]) > 0]
        compatible = all(row['within_canonical_rounding_precision'] for row in comparisons)
        records.append({'symbol': code+'.SH', 'observed_ipo_event_date': events[0][0],
                        'listing_primary_reference': url, 'listing_primary_reference_read_with_web': True,
                        'listing_source_bytes_not_yet_pinned': True,
                        'source_factor_sha256': sha(raw_path), 'canonical_source_sha256': sha(p),
                        'first_canonical_factor_date': first_date, 'factorless_trading_dates': prefix_days,
                        'factorless_stockhours': 4*len(prefix_days), 'sina_source_direction': 'divisor',
                        'canonical_direction': 'multiplier', 'constant_anchor_scale': str(scale),
                        'source_events_excluding_1900_sentinel': [{'date': d, 'qfq_divisor': str(f)} for d,f in events],
                        'all_canonical_events_concordant_with_anchor_aligned_sina': compatible,
                        'comparisons': comparisons,
                        'next_action': 'Pin original listing-source bytes and independently verify observed IPO-prefix minutes before separate candidate application' if compatible
                                       else 'Resolve adjustment-method disagreement before deriving a prefix from this source',
                        'admitted_to_candidate': False})
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'source_code_sha256': sha(Path(__file__)),
              'source_receipt_sha256': sha(SOURCE / 'receipt.json'),
              'records': records, 'compatible_source_symbols': [r['symbol'] for r in records if r['all_canonical_events_concordant_with_anchor_aligned_sina']],
              'adjustment_method_conflict_symbols': [r['symbol'] for r in records if not r['all_canonical_events_concordant_with_anchor_aligned_sina']],
              'five_ipo_prefix_potential_hours': sum(r['factorless_stockhours'] for r in records),
              'source_1900_sentinel_not_used_as_listing_or_observed_event': True,
              'known_canonical_factors_not_replaced': True, 'applied_to_any_candidate': False,
              'live_or_historically_known_anchor_certified': False,
              'limitations': 'Comparisons use an explicitly retrospective constant level alignment. Observed event dates can support as-of relative changes, but latest adjusted-price levels are not historical publication evidence. Three no-action histories agreeing at one canonical seed are not a general corporate-action certification.'}
    (SOURCE / 'prefix_concordance_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k:v for k,v in result.items() if k != 'records'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
