"""Independent retained-range ZIP, native-grid and raw-wire checks for 2024-26."""
import io
import json
import zipfile
import zlib
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from verify_wind17_all_2023_gap_findings import ROOT, BASE, SOURCE, sha, read_wire, normalized_day, FIELDS, PRICE
import pandas as pd
import numpy as np

OUT = SOURCE / 'remaining_late_years'


class RetainedRangeReader(io.RawIOBase):
    def __init__(self, item):
        self.size = item['source_file']['size']
        self.position = 0
        self.fragments = []
        for r in item['range_receipts']:
            assert r['http_status'] == 206 and sha(r['path']) == r['sha256']
            assert r['content_range'] == 'bytes %d-%d/%d' % (r['range_start'], r['range_end'], self.size)
            self.fragments.append((r['range_start'], r['range_end'], Path(r['path']).read_bytes()))

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        p = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        assert 0 <= p <= self.size
        self.position = p
        return p

    def read(self, size=-1):
        n = self.size - self.position if size < 0 else min(size, self.size - self.position)
        if not n:
            return b''
        for start, end, raw in self.fragments:
            if start <= self.position and self.position + n - 1 <= end:
                offset = self.position - start
                self.position += n
                return raw[offset:offset + n]
        raise ValueError('Verification requires unretained source bytes')


def main():
    ap = OUT / 'acquisition_and_daily_diagnostics_receipt.json'
    hp = OUT / 'late_year_hour_diagnostics_receipt.json'
    output = OUT / 'independent_late_year_findings_verification.json'
    assert not output.exists()
    audit, hours = [json.loads(p.read_text()) for p in [ap, hp]]
    assert hours['status'] == 'complete_late_year_minimum_gap_hour_diagnostics'
    assert hours['acquisition_daily_receipt_sha256'] == sha(ap)
    assert sha(ROOT / 'gru_beta_neutral_20261008/fetch_wind17_remaining_late_years.py') == audit['source_code_sha256']
    assert sha(ROOT / 'gru_beta_neutral_20261008/probe_wind17_late_year_hours.py') == hours['source_code_sha256']
    assert sha(audit['metadata_path']) == audit['metadata_sha256']
    assert sha(audit['gap_path']) == audit['gap_sha256']
    targets = pd.read_csv(audit['gap_path'])
    targets = targets[targets.date.str[:4].isin(['2024', '2025', '2026'])]
    dataset = BASE / 'backfilled_panel_candidate_ipo_prefix_v5/dataset'
    meta = json.loads((dataset / 'meta.json').read_text())
    assert sha(dataset / 'hour_complete.npy') == meta['files_sha256']['hour_complete.npy']
    complete = np.load(dataset / 'hour_complete.npy', mmap_mode='r')
    calendar = json.loads((ROOT / 'gru_research/artifacts/minute_calendar.json').read_text())
    coarse = {q['wire_path']: read_wire(q) for q in hours['queries']}
    diagnostic = {(r['symbol'], r['date']): r for r in hours['results']}
    counts = Counter()
    strict = []
    missing, passed_hours, native_pass, stockdays = 0, 0, 0, 0
    observed_dates = []
    for item in audit['results']:
        assert item['status'] == 'exact_CSV_acquired_and_daily_diagnostics_complete'
        assert sha(item['raw_path']) == item['raw_sha256']
        raw = Path(item['raw_path']).read_bytes()
        assert zlib.crc32(raw) & 0xffffffff == item['CRC32']
        with zipfile.ZipFile(RetainedRangeReader(item)) as archive:
            assert archive.read(item['zip_member']) == raw
        frame = pd.read_csv(io.BytesIO(raw), encoding='gbk', dtype=str).rename(columns=FIELDS)
        frame.index = pd.to_datetime(frame['k线结束时间'])
        assert not frame.index.duplicated().any() and (frame.index.year == int(item['year'])).all()
        assert frame['股票代码'].eq(item['observed_source_symbol']).all()
        assert len(frame) == item['source_rows'] and str(frame.index.min()) == item['first_source_timestamp'] and str(frame.index.max()) == item['last_source_timestamp']
        assert sha(item['primary_daily_path']) == item['primary_daily_sha256']
        primary = json.loads(Path(item['primary_daily_path']).read_text())
        daily = {r[0]: dict(zip(primary['fields'], r)) for r in primary['rows']}
        symbol = item['canonical_symbol']
        column = calendar['union_symbols'].index(symbol)
        for expected in item['target_stockday_checks']:
            date = expected['date']
            index = calendar['dates'].index(date) * 4
            current_missing = int((~complete[index:index + 4, column]).sum())
            row = targets[targets.symbol.eq(symbol) & targets.date.eq(date)]
            assert len(row) == 1 and current_missing == int(row.iloc[0].missing_hours) == expected['existing_missing_hours'] == 4
            missing += current_missing
            stockdays += 1
            native, reg, auction = normalized_day(frame, date)
            p = daily[date]
            vd = sum(reg.volume) - Decimal(p['volume'])
            valid = all(r.open > 0 and r.high >= max(r.open, r.close) and r.low > 0 and
                        r.low <= min(r.open, r.close) and r.volume >= 0 and
                        all(x == x.quantize(Decimal('0.01')) for x in [r.open, r.high, r.low, r.close]) for r in reg.itertuples())
            flags = {'source_rows_valid': valid, 'grid_valid': True,
                     'primary_day_trading': p['tradestatus'] == '1' and Decimal(p['volume']) > 0,
                     'open_agrees': reg.open.iloc[0] == Decimal(p['open']),
                     'close_agrees': reg.close.iloc[-1] == Decimal(p['close']),
                     'volume_difference_within_100_shares': abs(vd) <= 100,
                     'quotes_inside_primary_range': max(reg.high) <= Decimal(p['high']) and min(reg.low) >= Decimal(p['low']),
                     'all_four_hours_positive_volume': all(sum(reg.iloc[s*60:(s+1)*60].volume) > 0 for s in range(4))}
            assert all(expected[k] == v for k, v in flags.items()), (symbol, date)
            assert float(vd) == expected['volume_difference_shares'] and auction == expected['positive_0930_source_bar_merged']
            money_good = [r.low * r.volume - 1 <= r.amount <= r.high * r.volume + 1 for r in reg.itertuples()]
            assert money_good.count(False) == expected['minute_amount_vs_reported_price_times_lot_volume_conflicts']
            passed = all(flags.values())
            assert passed == expected['existing_daily_and_hour_gates_passed']
            h = diagnostic[(symbol, date)]
            counts[h['status']] += 1
            if not passed:
                assert h['status'] == 'failed_prior_daily_gate' and not h['hours']
                continue
            passed_hours += current_missing
            eligible = [s for s in range(4) if all(money_good[s*60:(s+1)*60])]
            native_pass += len(eligible)
            assert eligible == h['native_amount_pass_slots']
            if not eligible:
                assert h['status'] == 'all_hours_failed_native_amount_bounds' and not h['hours']
                continue
            rows = coarse[h['coarse_wire_path']]
            assert len(rows) == 48
            ordered = sorted(rows, key=lambda r: r['time'])
            compatible = []
            for bucket, reference in enumerate(ordered):
                block = reg.iloc[bucket*5:(bucket+1)*5]
                actual = {'open': block.open.iloc[0], 'high': max(block.high), 'low': min(block.low), 'close': block.close.iloc[-1]}
                assert block.index[-1] == pd.to_datetime(reference['time'], format='%Y%m%d%H%M%S%f')
                compatible.append(all(actual[f] == Decimal(reference[f]) for f in PRICE) and abs(sum(block.volume) - Decimal(reference['volume'])) <= 100)
            assert sum(compatible) == h['exact_5m_OHLC_volume_buckets']
            survived = [s for s in eligible if all(compatible[s*12:(s+1)*12])]
            assert survived == h['hours']
            strict.extend({'symbol': symbol, 'date': date, 'slot_zero_based': s} for s in survived)
        observed_dates.append({'symbol': symbol, 'year': item['year'], 'last_source_timestamp': item['last_source_timestamp']})
    assert len(diagnostic) == stockdays == len(targets)
    assert missing == int(targets.missing_hours.sum()) == audit['target_missing_hours']
    assert passed_hours == audit['daily_gate_pass_hours']
    assert len(strict) == hours['strict_diagnostic_hours'] == 0
    receipt = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
               'source_code_sha256': sha(__file__),
               'independent_helper_path': str(ROOT / 'gru_beta_neutral_20261008/verify_wind17_all_2023_gap_findings.py'),
               'independent_helper_sha256': sha(ROOT / 'gru_beta_neutral_20261008/verify_wind17_all_2023_gap_findings.py'),
               'acquisition_daily_receipt_sha256': sha(ap), 'hour_receipt_sha256': sha(hp),
               'candidate_meta_sha256': sha(dataset / 'meta.json'), 'candidate_fingerprint': meta['data_fingerprint'],
               'stockyears_verified': len(audit['results']), 'stockdays_verified': stockdays,
               'target_missing_hours': missing, 'daily_gate_pass_hours': passed_hours,
               'native_amount_pass_hours_among_daily_pass_days': native_pass,
               'raw_coarse_rows_independently_decoded': sum(map(len, coarse.values())),
               'stockday_status_counts': dict(counts), 'strict_diagnostic_hours': len(strict),
               'observed_last_dates': observed_dates,
               'method': 'Fresh retained range hashes and local ZIP re-decompression, CRC and native exact Decimals, current missing mask, raw coarse-wire decompression, independent sequential 5-minute blocks; no producer code imported',
               'whole_LFS_archives_verified': False, 'candidate_changed': False, 'candidate_hours_admitted': 0,
               'minute_quantity_precision_certified': False, 'production_ready': False}
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k != 'observed_last_dates'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
