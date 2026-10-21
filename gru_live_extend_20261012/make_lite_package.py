"""Build the minimal package the cloud machine needs to run the GRU public-data chain: last TAIL days of the frozen dataset
(same file names, so the chain code is unchanged), per-symbol frozen hourly tails, checkpoints, context, risk inputs,
daily/PIT sources (2025+), code. Output: outputs/gru_live_extend_20261012/lite_package/<name>/ and a .tar.gz."""
import json, shutil, sys, tarfile, os
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]; TAIL_DAYS = 130
name = sys.argv[1] if len(sys.argv) > 1 else 'gru_lite_20261012'; OUT = ROOT / 'outputs/gru_live_extend_20261012/lite_package' / name
if OUT.exists(): shutil.rmtree(OUT)
D = ROOT / 'gru_research/artifacts/dataset'; DO = OUT / 'gru_research/artifacts/dataset'; DO.mkdir(parents=True)
times = np.load(D / 'times.npy'); T0 = len(times) - 4 * TAIL_DAYS
for key in ['X', 'hour_complete', 'members', 'labels', 'industry', 'times', 'label_end_times', 'earliest_execution_times', 'hour_ohlcv', 'execution_raw_prices', 'execution_complete', 'execution_tradable', 'execution_all_flat', 'execution_prices']:
    a = np.load(D / f'{key}.npy', mmap_mode='r'); np.save(DO / f'{key}.npy', np.ascontiguousarray(a[T0:]))
s = np.load(D / 'slow_raw_by_day.npy', mmap_mode='r'); np.save(DO / 'slow_raw_by_day.npy', np.ascontiguousarray(s[T0 // 4:]))
for f in ['meta.json', 'symbols.json', 'feature_names.json', 'dataset_audit.json']: shutil.copy(D / f, DO / f)
(DO / 'LITE_TAIL.json').write_text(json.dumps(dict(full_rows=int(len(times)), tail_rows=int(len(times) - T0), first_time_ns=int(times[T0]), note='rows [T0:] of the frozen dataset; array indices in test_scores.npz / risk_context.npz refer to the FULL dataset and must be mapped by time')))
A = ROOT / 'gru_research/artifacts'; AO = OUT / 'gru_research/artifacts'
for f in ['slow_factor_selection.json', 'slow_manifest.json', 'minute_calendar.json', 'minute_feature_metadata.json', 'minute_memberships.parquet']: shutil.copy(A / f, AO / f)
tail_dates = json.loads((A / 'minute_calendar.json').read_text())['dates'][-TAIL_DAYS:]
for d in sorted((A / 'minute_raw_by_symbol').glob('symbol=*')):
    f = pd.concat([pd.read_parquet(p) for p in d.glob('*.parquet')], ignore_index=True); f = f[f.date.isin(tail_dates)]
    if len(f): (AO / 'minute_raw_by_symbol' / d.name).mkdir(parents=True, exist_ok=True); f.to_parquet(AO / 'minute_raw_by_symbol' / d.name / 'data_0.parquet', index=False)
def copytree(src, dst, patterns=None):
    src, dst = ROOT / src, OUT / dst; dst.mkdir(parents=True, exist_ok=True)
    for p in src.rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts and (patterns is None or any(p.match(x) for x in patterns)):
            q = dst / p.relative_to(src); q.parent.mkdir(parents=True, exist_ok=True); shutil.copy(p, q)
copytree('outputs/gru_walkforward_20261010/T2026', 'outputs/gru_walkforward_20261010/T2026', ['*/best.pt', '*/test_scores.npz', '*/config.json', '*/result.json'])
copytree('gru_market_attention_20261007', 'gru_market_attention_20261007', ['*.py', 'context.npz', 'context_manifest.json'])
copytree('gru_research', 'gru_research', ['*.py']); copytree('gru_beta_neutral_20261008', 'gru_beta_neutral_20261008', ['*.py']); copytree('pcf_overlay_20261012', 'pcf_overlay_20261012', ['*.py'])
copytree('gru_live_extend_20261012', 'gru_live_extend_20261012', ['*.py', '*.sh']); copytree('实盘日更', '实盘日更', ['*.py', '*.sh', '*.ps1', '*.md', 'config/*', 'inputs/*/*', 'outputs/signals/*', 'outputs/daily_log.csv'])
copytree('quantagent/src', 'quantagent/src', ['*.py', '*.json'])
for f in ['risk_context.npz', 'benchmark_sources/000905_daily.csv', 'benchmark_sources/H00905_daily.csv']: (OUT / 'outputs/gru_beta_neutral_20261008' / f).parent.mkdir(parents=True, exist_ok=True); shutil.copy(ROOT / 'outputs/gru_beta_neutral_20261008' / f, OUT / 'outputs/gru_beta_neutral_20261008' / f)
I = ROOT / '中证500_历年成分股_日频_2010-2026_iFinD_后复权'; IO = OUT / I.name
for sub in ['bars/2024', 'bars/2025', 'bars/2026', 'memberships']: copytree(str(I.relative_to(ROOT) / sub), str(IO.relative_to(OUT) / sub))
shutil.copy(I / 'dataset.json', IO / 'dataset.json')
F = ROOT / '中证500_历年成分股_基本面_2010-2026_iFinD_PIT'; (OUT / F.name).mkdir(exist_ok=True)
for f in ['financial_verified_19_fields.parquet', 'weekly_market_and_industry.parquet']: shutil.copy(F / f, OUT / F.name / f)
copytree('给队友_c5状态_20260929_20261009', '给队友_c5状态_20260929_20261009', ['*.parquet'])
copytree('outputs/gru_live_extend_20261012/scores', 'outputs/gru_live_extend_20261012/scores', ['panel_ext.npz', 'scores_new.npz']); copytree('outputs/gru_live_extend_20261012/signals', 'outputs/gru_live_extend_20261012/signals')
tar = OUT.with_suffix('.tar.gz')
with tarfile.open(tar, 'w:gz', compresslevel=3) as t: t.add(OUT, arcname=name)
size = sum(p.stat().st_size for p in OUT.rglob('*') if p.is_file()); print(json.dumps(dict(package=str(OUT), files=sum(1 for _ in OUT.rglob('*') if _.is_file()), raw_mb=round(size / 1e6), tar_mb=round(tar.stat().st_size / 1e6), tar=str(tar))))
