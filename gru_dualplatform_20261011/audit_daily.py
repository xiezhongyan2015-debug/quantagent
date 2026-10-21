"""Audit completed panel bytes, missingness and embargo boundaries before upload."""
from pathlib import Path
import hashlib, json, os
import numpy as np
import pandas as pd

P = Path(os.environ.get('DAILY_PANEL', Path(__file__).resolve().parents[1] / 'outputs/gru_dualplatform_20261011/daily_panel'))

def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(8 * 1024**2), b''): h.update(b)
    return h.hexdigest()

def audit():
    meta = json.loads((P/'meta.json').read_text())
    for name, expected in meta['files'].items():
        assert sha(P/name) == expected, name
    a = {n: np.load(P/(n+'.npy'), mmap_mode='r') for n in
         ['X','members','complete','labels','times','execution','label_ends','industry']}
    T,U,F = a['X'].shape
    assert [T,U,F] == meta['shape'] and F == 98
    assert a['members'].dtype == bool and a['complete'].dtype == bool
    assert np.all(np.diff(a['times']) > 0)
    valid_execution = a['execution'] > 0
    assert np.all(a['execution'][valid_execution] > a['times'][valid_execution])
    assert np.array_equal(a['execution'][:-1], a['times'][1:] - pd.Timedelta(hours=5,minutes=29).value)
    for j,h in enumerate(meta['horizons']):
        end = T-h-1
        assert np.array_equal(a['label_ends'][:end,j], a['times'][1+h:] - pd.Timedelta(hours=5,minutes=24).value)
        assert np.all(a['label_ends'][end:,j] == -1)
        assert np.isnan(a['labels'][end:,:,j]).all(), 'unmatured terminal targets must be missing'
    assert np.all(a['industry'] == 0), 'unverified industry must stay unknown'
    count = np.zeros(F, dtype='int64'); total = 0
    for t in range(T):
        x = a['X'][t, a['members'][t]]
        assert not np.isinf(x).any()
        assert np.all(np.abs(x[np.isfinite(x)]) <= 5.00001)
        count += np.isfinite(x).sum(0); total += len(x)
    dates = pd.to_datetime(a['times'], utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d').to_numpy()
    prefix = np.concatenate([np.zeros((1,U),dtype='int32'),np.cumsum(~a['complete'],axis=0,dtype='int32')])
    eligible = np.zeros(T,dtype='int64')
    for t in range(59,T):eligible[t] = (a['members'][t] & ((prefix[t+1]-prefix[t-59]) == 0)).sum()
    sections = {}
    for year in range(2021, 2027):
        sections[year] = {}
        for kind,start,end in [('train','2012-01-01',f'{year-1}-01-01'),
                               ('validation',f'{year-1}-01-01',f'{year}-01-01')]:
            ix = np.flatnonzero((dates >= start) & (dates < end))
            cut = np.unique(dates[dates < end])[-2]
            ns = pd.Timestamp(cut,tz='Asia/Shanghai').value
            ix = ix[(dates[ix] < cut) & np.all((a['label_ends'][ix] > a['execution'][ix,None]) &
                                               (a['label_ends'][ix] < ns), axis=1)]
            assert len(ix) and np.all(a['label_ends'][ix] < ns)
            usable = ix[eligible[ix] >= 100]
            if kind == 'train':
                kept = []
                for t in usable:
                    mask = a['members'][t] & ((prefix[t+1]-prefix[t-59]) == 0)
                    labels = np.asarray(a['labels'][t,mask])
                    if (np.isfinite(labels).sum(0) >= 100).all() and (np.nanstd(labels,0)>1e-12).all():
                        kept.append(t)
                training_label_exclusions = len(usable)-len(kept)
                usable = np.asarray(kept,dtype='int64')
            else:
                training_label_exclusions = 0
            assert len(usable), 'no eligible dates'
            sections[year][kind] = {'days_before_window_eligibility':len(ix),
                                    'eligible_days':len(usable),
                                    'training_label_exclusions':training_label_exclusions,
                                    'median_stocks':float(np.median(eligible[ix])), 'cutoff':cut,
                                    'last_signal':dates[usable[-1]]}
    names = json.loads((P/'feature_names.json').read_text())
    report = {'status':'passed','fingerprint':meta['data_fingerprint'],
              'normalized_member_coverage':dict(zip(names,(count/total).tolist())),
              'sections':sections,'limitations':meta['limitations']}
    (P.parent/'daily_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('AUDIT PASSED',meta['shape'],flush=True)

if __name__ == '__main__': audit()
