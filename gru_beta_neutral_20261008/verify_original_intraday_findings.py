"""Independent pandas time-grouping check of observed close/volume findings."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gru_research/.deps'))
import pyarrow.parquet as pq
import numpy as np
import pandas as pd

OUT = ROOT / 'outputs/gru_beta_neutral_20261008/public_source_probes/original_intraday_concordance_20261009'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    path = OUT / 'concordance_audit_receipt.json'
    audit = json.loads(path.read_text())
    assert sha(ROOT / 'gru_beta_neutral_20261008/audit_original_intraday_concordance.py') == audit['source_code_sha256']
    checks = []
    for r in audit['results']:
        sources = {x['role']: x for x in r['sources']}
        for s in sources.values():
            assert sha(s['path']) == s['sha256']
        o = pq.read_table(sources['original_raw_minute_payload']['path']).to_pandas()
        o = o.assign(end=pd.to_datetime(o.datetime) + pd.Timedelta(minutes=1)).set_index('end').sort_index()
        day = pd.Timestamp(r['date'])
        h = pq.read_table(sources['external_raw_minute_file']['path'],
                          filters=[('timestamp', '>=', day), ('timestamp', '<', day + pd.Timedelta(days=1))]).to_pandas().set_index('timestamp').sort_index()
        h.close = h.close.round(2)
        h.volume = h.volume.astype('float64')
        auction = h.loc[day + pd.Timedelta(hours=9, minutes=30)] if day + pd.Timedelta(hours=9, minutes=30) in h.index else None
        h = h.reindex(o.index).copy()
        assert h[['close', 'volume']].notna().all().all()
        if auction is not None and auction.volume > 0:
            h.loc[h.index[0], 'volume'] += auction.volume
        sourcewire = Path(sources['public_raw_5m_wire']['path'])
        query = json.loads(sourcewire.with_suffix('.json').read_text())
        assert query['sha256'] == sources['public_raw_5m_wire']['sha256']
        rawrows = query['data']['record']
        coarse = pd.DataFrame(rawrows, columns=query['fields'])
        coarse.index = pd.to_datetime(coarse.time, format='%Y%m%d%H%M%S%f')
        cclose, cvolume = coarse.close.astype(float), coarse.volume.astype(float)
        stat = {}
        for name, frame in [('original', o), ('external', h)]:
            grouped = frame.groupby(pd.Grouper(freq='5min', label='right', closed='right'))
            observed_close = grouped.close.last().reindex(coarse.index)
            observed_volume = grouped.volume.sum().reindex(coarse.index)
            assert observed_close.notna().all() and observed_volume.notna().all()
            metrics = r[name + '_vs_public_5m']
            close_count = int(np.isclose(observed_close, cclose, rtol=0, atol=1e-8).sum())
            volume_count = int((observed_volume == cvolume).sum())
            assert close_count == metrics['exact_price_intervals_by_field']['close']
            assert volume_count == metrics['volume_exact_intervals']
            stat[name + '_close_exact_5m'] = close_count
            stat[name + '_volume_exact_5m'] = volume_count
        minute_close = int(np.isclose(o.close, h.close, rtol=0, atol=1e-8).sum())
        minute_volume = int((o.volume == h.volume).sum())
        assert minute_close == r['original_vs_external_1m']['exact_price_intervals_by_field']['close']
        assert minute_volume == r['original_vs_external_1m']['volume_exact_intervals']
        stat.update({'symbol': r['symbol'], 'date': r['date'], 'minute_close_matches': minute_close,
                     'minute_volume_matches': minute_volume})
        checks.append(stat)
    result = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
              'method': 'independent pandas clock grouping; original audit module not imported',
              'source_code_sha256': sha(Path(__file__)), 'audited_receipt_sha256': sha(path),
              'fresh_source_hash_checks_passed': True, 'checks': checks,
              'panel_changed': False, 'production_ready': False}
    (OUT / 'independent_findings_verification.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
