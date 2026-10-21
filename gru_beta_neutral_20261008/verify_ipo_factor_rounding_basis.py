"""Independent raw-source verification of the unapplied canonical IPO proposal.

No producer or shared normalization helper is imported. Provider table rows are
reparsed with a different parser, and each suffix product is evaluated separately
at 60-digit precision. Official Chrome table observations are checked as rendered
observations, not misrepresented as archived HTTP responses or issuer PDFs.
"""
import bisect
import csv
import hashlib
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
from functools import reduce
from operator import mul
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
from pypdf import PdfReader

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
SOURCES = BASE / 'public_source_probes'
OUT = SOURCES / 'ipo_factor_rounding_method_20261009'
GRID = [*range(571, 691), *range(781, 901)]


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def product(values):
    return reduce(mul, values, Decimal(1))


def state(events, day):
    dates = [d for d, v in events]
    index = bisect.bisect_right(dates, day) - 1
    return None if index < 0 else events[index][1]


def main():
    output = OUT / 'independent_canonical_basis_prefix_verification.json'
    assert not output.exists()
    ap = OUT / 'canonical_basis_prefix_audit_receipt.json'
    sp = OUT / 'source_acquisition_receipt.json'
    op = OUT / 'official_cash_tables_chrome_observation.json'
    audit, acquisition, official = [json.loads(p.read_text()) for p in [ap, sp, op]]
    assert audit['passed'] and audit['candidate_changed'] is False
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_ipo_factor_rounding_basis.py') == audit['source_code_sha256']
    assert sha(sp) == audit['source_acquisition_receipt_sha256']
    assert sha(audit['proposed_event_path']) == audit['proposed_event_sha256']
    events = pq.read_table(audit['proposed_event_path']).to_pylist()
    with Path(audit['minute_inventory_path']).open(newline='') as stream:
        inventory = {r['symbol']: r for r in csv.DictReader(stream)}
    assert sha(audit['minute_inventory_path']) == audit['minute_inventory_sha256']
    calendar_path = ROOT / 'gru_research/artifacts/minute_calendar.json'
    calendar = json.loads(calendar_path.read_text())
    dataset = BASE / 'backfilled_panel_candidate_ipo_prefix_v5/dataset'
    meta = json.loads((dataset / 'meta.json').read_text())
    assert sha(dataset / 'hour_complete.npy') == meta['files_sha256']['hour_complete.npy']
    mask = np.load(dataset / 'hour_complete.npy', mmap_mode='r')
    results = []
    canonical_count = sina_count = 0
    greatest_error = Decimal(0)
    with localcontext() as context:
        context.prec = 60
        for prior in audit['records']:
            symbol, ipo = prior['symbol'], prior['observed_IPO_seed_date']
            sources = [r for r in acquisition['records'] if r['symbol'] == symbol and r['kind'] in ['IPO', 'provider_action_table']]
            assert len(sources) == 2
            for source in sources:
                assert sha(source['path']) == source['sha256']
                assert sha(source['extracted_text_path']) == source['extracted_text_sha256']
            listing = next(r for r in sources if r['kind'] == 'IPO')
            raw = Path(listing['path']).read_bytes()
            if raw.startswith(b'%PDF'):
                listing_text = ''.join(p.extract_text() or '' for p in PdfReader(listing['path']).pages[:10])
            else:
                listing_text = html.unescape(re.sub(r'<[^>]+>', '', raw.decode('utf-8')))
            listing_text = re.sub(r'\s+', '', listing_text)
            yy, mm, dd = map(int, ipo.split('-'))
            assert symbol[:6] in listing_text and f'{yy}年{mm}月{dd}日' in listing_text
            provider = next(r for r in sources if r['kind'] == 'provider_action_table')
            raw = Path(provider['path']).read_bytes().decode('gb18030')
            parsed = []
            for row in re.findall(r'<tr\b[^>]*>(.*?)</tr\s*>', raw, re.I | re.S):
                cells = [re.sub(r'\s+', '', html.unescape(re.sub(r'<[^>]+>', '', cell)))
                         for cell in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]\s*>', row, re.I | re.S)]
                if len(cells) >= 8 and re.fullmatch(r'\d{4}-\d{2}-\d{2}', cells[0]) and cells[4] == '实施':
                    assert Decimal(cells[1]) == Decimal(cells[2]) == 0
                    parsed.append((cells[5], Decimal(cells[3]) / 10, cells[6]))
            assert parsed and len({r[0] for r in parsed}) == len(parsed)
            cash = {d: v for d, v, record in parsed}
            official_record = next(r for r in official['records'] if r['symbol'] == symbol)
            assert official_record['observed_listing_date'] == ipo
            assert ''.join(''.join(r) for r in official_record['rows']) == official_record['table_text']
            official_cash = {r[2]: Decimal(r[3].split('(')[0]) for r in official_record['rows'][1:]}
            assert official_cash == cash
            assert {d: rd for d, v, rd in parsed} == {r[2]: r[0] for r in official_record['rows'][1:]}
            pp, js = Path(prior['primary_daily_factor_path']), Path(prior['sina_factor_path'])
            assert sha(pp) == prior['primary_daily_factor_sha256']
            assert sha(js) == prior['sina_factor_sha256']
            primary = json.loads(pp.read_text())
            rows = {r[0]: dict(zip(primary['fields'], r)) for r in primary['rows']}
            days = sorted(rows)
            canonical = sorted((r[1], Decimal(r[2])) for r in primary['factors'])
            match = re.search(r'\{.*\}', js.read_text(), re.S)
            sina = sorted((r['d'], Decimal(r['f'])) for r in json.loads(match.group())['data'] if r['d'] != '1900-01-01')
            assert sina[0][0] == ipo < canonical[0][0]
            assert canonical[-1][1] == sina[-1][1] == 1
            assert set(cash) == {d for d, f in canonical[1:]} == {d for d, f in sina[1:]}
            rounded, exact = {}, {}
            for date in cash:
                previous = days[bisect.bisect_left(days, date) - 1]
                close = Decimal(rows[previous]['close'])
                reference = Decimal(rows[date]['preclose'])
                assert (close-cash[date]).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) == reference
                rounded[date] = reference / close
                exact[date] = (close-cash[date]) / close
            for date, observed in canonical:
                calculated = product(rounded[d] for d in sorted(rounded) if d > date)
                assert calculated.quantize(Decimal('.000001'), rounding=ROUND_HALF_UP) == observed
                canonical_count += 1
            for date, observed in sina:
                calculated = 1 / product(exact[d] for d in sorted(exact) if d > date)
                assert calculated.quantize(Decimal('1e-13'), rounding=ROUND_HALF_UP) == observed.quantize(Decimal('1e-13'), rounding=ROUND_HALF_UP)
                greatest_error = max(greatest_error, abs(calculated-observed))
                sina_count += 1
            calculated_prefix = product(rounded.values()).quantize(Decimal('.000001'), rounding=ROUND_HALF_UP)
            assert calculated_prefix == canonical[0][1] == Decimal(prior['prefix_factor_in_existing_canonical_basis'])
            first = canonical[0][0]
            gap_days = [d for d in days if ipo <= d < first and rows[d]['tradestatus'] == '1' and Decimal(rows[d]['volume']) > 0]
            assert gap_days == [ipo] and not any(ipo <= d <= first for d in cash)
            proposed_rows = [r for r in events if r['symbol'] == symbol]
            proposed = sorted((r['effective_date'], Decimal(str(r['effective_fore_factor']))) for r in proposed_rows)
            assert proposed == [(ipo, calculated_prefix), *canonical]
            assert [r for r in proposed_rows if r['effective_date'] == ipo][0]['source_kind'] == 'calculated_canonical_basis_on_observed_IPO_seed_date'
            assert state(proposed, (datetime.fromisoformat(ipo)-timedelta(days=1)).date().isoformat()) is None
            for day in days:
                if day >= first:
                    assert state(proposed, day) == state(canonical, day)
                assert state(proposed + [('2099-01-01', Decimal('.5'))], day) == state(proposed, day)
            mp = Path(prior['minute_source_path'])
            assert sha(mp) == prior['minute_source_sha256'] == inventory[symbol]['expected_lfs_sha256']
            start = datetime.fromisoformat(ipo)
            minute = pq.read_table(mp, filters=[('timestamp', '>=', start), ('timestamp', '<', start+timedelta(days=1))]).to_pylist()
            minute.sort(key=lambda r: r['timestamp'])
            assert len({r['timestamp'] for r in minute}) == len(minute)
            assert all(str(r['symbol']) == symbol[:6] and r['exchange'] == 'SH' and r['timestamp'].second == 0 for r in minute)
            regular, auction = [], []
            for row in minute:
                clock = row['timestamp'].hour*60 + row['timestamp'].minute
                q = {k: Decimal(str(row[k])).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) for k in ['open','high','low','close']}
                q.update(clock=clock, volume=Decimal(str(row['volume'])))
                if clock in GRID:
                    regular.append(q)
                elif clock == 570 and q['volume'] > 0:
                    auction.append(q)
                elif clock == 780:
                    assert q['volume'] == 0
            assert len(auction) <= 1 and [r['clock'] for r in regular] == GRID
            if auction:
                a, r = auction[0], regular[0]
                r['open'], r['high'], r['low'] = a['open'], max(r['high'], a['high']), min(r['low'], a['low'])
                r['volume'] += a['volume']
            p = rows[ipo]
            assert all(all(r[k].is_finite() and r[k] > 0 for k in ['open','high','low','close']) and
                       r['low'] <= min(r['open'],r['close']) <= max(r['open'],r['close']) <= r['high'] and
                       r['volume'].is_finite() and r['volume'] >= 0 for r in regular)
            assert regular[0]['open'] == Decimal(p['open']) and regular[-1]['close'] == Decimal(p['close'])
            assert max(r['high'] for r in regular) <= Decimal(p['high']) and min(r['low'] for r in regular) >= Decimal(p['low'])
            day_volume = sum(r['volume'] for r in regular)
            assert abs(day_volume - Decimal(p['volume'])) <= 100
            assert all(sum(r['volume'] for r in regular[s*60:(s+1)*60]) > 0 for s in range(4))
            index, column = calendar['dates'].index(ipo)*4, calendar['union_symbols'].index(symbol)
            assert not mask[index:index+4,column].any()
            results.append({'symbol':symbol,'IPO_date':ipo,'first_canonical_date':first,
                            'calculated_prefix_factor':str(calculated_prefix),'prefix_is_calculated_not_observed_value':True,
                            'official_rendered_cash_rows_confirmed':len(cash),'known_canonical_factors_preserved':len(canonical),
                            'native_IPO_grid_minutes':240,'qualified_unapplied_hours':4,
                            'native_day_volume':str(day_volume),'current_V5_hours_remain_incomplete':True,
                            'before_IPO_no_state':True,'later_canonical_states_unchanged':True,'future_append_states_unchanged':True})
    result = {'checked_utc':datetime.now(timezone.utc).isoformat(),'passed':True,'source_code_sha256':sha(__file__),
              'audited_receipt_path':str(ap),'audited_receipt_sha256':sha(ap),
              'official_table_observation_path':str(op),'official_table_observation_sha256':sha(op),
              'source_acquisition_receipt_sha256':sha(sp),'proposed_events_sha256':sha(audit['proposed_event_path']),
              'canonical_values_reproduced_exactly_at_six_decimals':canonical_count,
              'Sina_values_reproduced_exactly_at_thirteen_decimals':sina_count,
              'maximum_Sina_absolute_error':str(greatest_error),'official_cash_rows_confirmed':sum(r['official_rendered_cash_rows_confirmed'] for r in results),
              'results':results,'qualified_unapplied_hours':8,'candidate_changed':False,'candidate_hours_admitted':0,
              'candidate_meta_sha256':sha(dataset/'meta.json'),'candidate_fingerprint':meta['data_fingerprint'],
              'first_dividend_issuer_PDF_acquired':False,'complete_company_action_history_certified':False,
              'method':'Independent regex raw-table parsing, 60-digit suffix products, raw daily/factor/minute rechecks; official rendered cash-table cells corroborated. No producer or normalization helper imported.',
              'normalization_basis':'Existing retrospective canonical basis; not a claim of factor values published at IPO time',
              'production_ready':False}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
