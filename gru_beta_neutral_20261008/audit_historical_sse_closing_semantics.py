"""Validate distinct pre-auction SSE daily and minute closing statistics.

The raw minute close remains the observed last-trade close. The independently
reported daily close is checked against the observed final minute's amount /
volume, with an unambiguous cent rounding interval. This is retrospective
source concordance, not tick-level certification of the precise rolling window.
No daily price is substituted into any minute or executable price.
"""
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

BASE = ROOT / 'outputs/gru_beta_neutral_20261008'
CAND = BASE / 'backfilled_panel_candidate_code_bridge_v3'
HF = BASE / 'public_source_probes/huggingface'
PRIMARY = BASE / 'public_source_probes/baostock/union_daily_factors/normalized'
OUT = BASE / 'public_source_probes/sse_historical_close_rule_20261009'
END_MINUTES = np.r_[np.arange(571, 691), np.arange(781, 901)]


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def eligible(frame):
    return ((frame.date >= '2018-01-01') & (frame.date < '2018-08-20') & frame.grid_valid & frame.code.notna()
            & frame.tradestatus.eq('1') & frame.volume.gt(0) & ((frame.raw_open - frame.open).abs() <= 1e-8)
            & ((frame.raw_close - frame.close).abs() > 1e-8) & ((frame.raw_volume - frame.volume).abs() <= 100)
            & (frame.raw_high <= frame.high + 1e-8) & (frame.raw_low >= frame.low - 1e-8) & frame.factor.gt(0))


def audit_symbol(symbol, file_entry):
    assert symbol.endswith('.SH')
    audit_path = CAND / 'donor_daily_audit' / (symbol + '.parquet')
    audit = pq.read_table(audit_path).to_pandas()
    selected = audit.loc[eligible(audit)].sort_values('date')
    assert len(selected)
    primary_path = PRIMARY / (symbol + '.json')
    primary = json.loads(primary_path.read_text())
    daily = pd.DataFrame(primary['rows'], columns=primary['fields']).set_index('date')
    path = HF / 'snapshot' / file_entry['rfilename']
    source_sha = sha(path)
    assert source_sha == file_entry['lfs']['sha256'] and path.stat().st_size == file_entry['size']
    quotes = pq.read_table(path, filters=[('timestamp', '>=', pd.Timestamp(selected.date.min())),
                                        ('timestamp', '<', pd.Timestamp(selected.date.max()) + pd.Timedelta(days=1))]).to_pandas()
    assert quotes.symbol.astype(str).eq(symbol[:6]).all() and quotes.exchange.eq('SH').all()
    quotes['date'] = quotes.timestamp.dt.strftime('%Y-%m-%d')
    groups = {day: frame.sort_values('timestamp') for day, frame in quotes.groupby('date')}
    records = []
    for row in selected.itertuples():
        table = groups[row.date]
        clocks = table.timestamp.dt.hour.to_numpy() * 60 + table.timestamp.dt.minute.to_numpy()
        regular = table.loc[np.isin(clocks, END_MINUTES)]
        grid = len(regular) == 240 and np.array_equal(regular.timestamp.dt.hour.to_numpy() * 60 + regular.timestamp.dt.minute.to_numpy(), END_MINUTES)
        boundary = table.loc[clocks == 570]
        assert len(boundary) <= 1
        active = pd.concat([regular, boundary.loc[boundary.volume > 0]]).sort_values('timestamp')
        volume = active.volume.to_numpy(float)
        amount = active.turnover.to_numpy(float)
        low, high = active.low.to_numpy().round(2), active.high.to_numpy().round(2)
        # Normalized amounts preserve the original float32 representation.
        # Bound only the precision of observed amount data, never quote prices.
        rounding = np.spacing(np.abs(amount).astype(np.float32)).astype(float) / 2
        bounds = rounding + 0.01
        valid_amount = (np.isfinite(amount).all() and (amount >= 0).all()
                        and (amount[volume == 0] == 0).all()
                        and (amount >= volume * low - bounds).all()
                        and (amount <= volume * high + bounds).all())
        total_amount = float(amount.sum())
        daily_amount = float(daily.loc[row.date, 'amount'])
        amount_tolerance = float(rounding.sum() + 0.01 * (len(amount) + 1))
        amount_reconciled = abs(total_amount - daily_amount) <= amount_tolerance
        source_volume_equal_audit = volume.sum() == row.raw_volume
        last = regular.iloc[-1]
        last_volume, last_amount = float(last.volume), float(last.turnover)
        positive_final = last_volume > 0 and last_amount > 0
        ref = last_amount / last_volume if positive_final else np.nan
        error = (float(np.spacing(np.float32(abs(last_amount)))) / 2 + 0.01) / last_volume if positive_final else np.nan
        lower_cent = int(np.floor((ref - error) * 100 + .5)) if positive_final else None
        upper_cent = int(np.floor((ref + error) * 100 + .5)) if positive_final else None
        unambiguous = positive_final and lower_cent == upper_cent
        reference = lower_cent / 100 if unambiguous else np.nan
        close_equal = unambiguous and abs(reference - row.close) <= 1e-8
        source_close_equal_audit = abs(round(float(last.close), 2) - row.raw_close) <= 1e-8
        good = bool(grid and valid_amount and amount_reconciled and source_volume_equal_audit and positive_final
                    and unambiguous and close_equal and source_close_equal_audit)
        records.append({'symbol': symbol, 'date': row.date, 'minute_grid_valid': bool(grid),
                        'source_last_trade_close': row.raw_close, 'independent_daily_close': row.close,
                        'observed_final_minute_turnover': last_amount, 'observed_final_minute_volume': last_volume,
                        'observed_final_minute_vwap': ref, 'amount_precision_price_bound': error,
                        'rounded_final_minute_vwap_reference': reference, 'cent_rounding_unambiguous': bool(unambiguous),
                        'all_minute_amounts_inside_observed_price_ranges': bool(valid_amount),
                        'observed_day_amount': total_amount, 'independent_day_amount': daily_amount,
                        'day_amount_discrepancy': total_amount - daily_amount, 'day_amount_precision_bound': amount_tolerance,
                        'day_amount_reconciled': bool(amount_reconciled), 'observed_volume_reproduces_prior_audit': bool(source_volume_equal_audit),
                        'source_last_close_reproduces_prior_audit': bool(source_close_equal_audit), 'closing_reference_matches_primary': bool(close_equal),
                        'historical_close_reference_corroborated': good, 'source_minute_file_sha256': source_sha,
                        'no_source_price_overwritten': True, 'tick_window_equivalence_certified': False})
    frame = pd.DataFrame(records)
    frame.to_parquet(OUT / 'symbol_audits' / (symbol + '.parquet'), index=False, compression='zstd')
    return {'symbol': symbol, 'potential_stockdays': len(frame), 'qualified_stockdays': int(frame.historical_close_reference_corroborated.sum()),
            'source_minute_file_sha256': source_sha, 'source_daily_factor_sha256': sha(primary_path),
            'prior_donor_audit_sha256': sha(audit_path), 'new_audit_sha256': sha(OUT / 'symbol_audits' / (symbol + '.parquet')),
            'failures_overlapping': {name: int((~frame[name]).sum()) for name in ['cent_rounding_unambiguous', 'all_minute_amounts_inside_observed_price_ranges',
                'day_amount_reconciled', 'closing_reference_matches_primary']}}


def main():
    started = time.monotonic()
    rules = json.loads((OUT / 'official_rule_receipt.json').read_text())
    assert rules['new_stock_closing_auction_effective_date'] == '2018-08-20'
    for rule in rules['official_rule_sources']:
        assert sha(OUT / (rule['name'] + '.html')) == rule['raw_html_sha256']
        assert sha(OUT / (rule['name'] + '.txt')) == rule['text_sha256']
    (OUT / 'symbol_audits').mkdir(exist_ok=True)
    potential = json.loads((OUT / 'potential_symbols.json').read_text())
    metadata = json.loads((HF / 'dataset_info_blobs.json').read_text())
    assert metadata['sha'] == 'ba589a11534825044fe5a6b84838f50ba8d8d188' and not metadata['gated'] and not metadata['private']
    files = {r['rfilename']: r for r in metadata['siblings']}
    records = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(audit_symbol, r['symbol'], files[f"data/stock_1m/SH/{r['symbol'][:6]}.parquet"]): r['symbol'] for r in potential}
        for job in as_completed(jobs):
            records.append(job.result())
            if len(records) % 25 == 0:
                print(json.dumps({'status': 'verifying_historical_closing_statistics', 'symbols_complete': len(records),
                                  'symbols_total': len(potential), 'qualified_stockdays': sum(r['qualified_stockdays'] for r in records)}), flush=True)
    qualified = pd.concat([pq.read_table(OUT / 'symbol_audits' / (r['symbol'] + '.parquet')).to_pandas().query('historical_close_reference_corroborated') for r in records], ignore_index=True)
    assert qualified.date.ge('2018-01-01').all() and qualified.date.lt('2018-08-20').all() and qualified.symbol.str.endswith('.SH').all()
    assert not qualified.duplicated(['symbol', 'date']).any()
    target = OUT / 'qualified_historical_closing_statistics.parquet'
    qualified.to_parquet(target, index=False, compression='zstd')
    receipt = {'status': 'historical_SSE_closing_statistics_corroborated', 'checked_utc': datetime.now(timezone.utc).isoformat(),
               'symbols_audited': len(records), 'potential_stockdays': sum(r['potential_stockdays'] for r in records),
               'qualified_stockdays': len(qualified), 'qualified_symbols': sorted(qualified.symbol.unique().tolist()),
               'source_code_sha256': sha(Path(__file__)), 'official_rule_receipt_sha256': sha(OUT / 'official_rule_receipt.json'),
               'qualified_parquet_sha256': sha(target), 'source_records': sorted(records, key=lambda r: r['symbol']),
               'prior_candidate_modified': False, 'raw_minute_close_replaced_by_daily_close': False,
               'synthetic_tradable_prices': False, 'period_scope': 'SH equities, 2018-01-01 <= date < 2018-08-20 only',
               'minute_price_basis': 'unchanged observed raw last-trade/quote OHLC, not daily VWAP',
               'closing_reference_basis': 'observed final clock-minute turnover / volume, unambiguously rounded to 0.01 CNY and agreeing with independent daily close',
               'limitations': 'Retrospective closing-statistic concordance. Minute buckets do not certify the exact trailing 60-second window relative to the last tick. No tick-level extremum or live source admission certification.',
               'production_ready': False, 'panel_applied': False, 'elapsed_seconds': round(time.monotonic() - started, 2)}
    (OUT / 'audit_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k not in ['source_records', 'qualified_symbols']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
