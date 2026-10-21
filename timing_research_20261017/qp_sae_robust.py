"""SAE 复刻的稳健性（2026-10-18）：
1 种子分布：40 个 seed 单独 / 每 5 个一组的集成 → 2020–2025 研报口径多空年化、5 日标签秩 IC
2 消融：无小波；纯预测 MLP（重构、稀疏权重 0）；线性 Ridge（同特征同折）；朴素反转 −ret5 / 动量 ret20
3 发布后样本：2026-02-03 至 2026-10-16（研报 2026-02-02 发布，之后才算真样本外）
4 score 与简单量价的相关（看它学到了什么）
输出 outputs/timing_p2_20261017/qp/sae_robust_*.csv
"""
import sys, time, warnings
from pathlib import Path
import numpy as np
import pandas as pd
import torch
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings('ignore')
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
import qp_sae_reimpl as M  # noqa: E402
from qp_sae_eval import report_bt  # noqa: E402

OUT = M.OUT


def prepare(wavelet=True):
    q = pd.read_parquet(M.ROOT / 'outputs/timing_p2_20261017/index_000905.parquet').sort_index().loc[:M.TEST_END].copy()
    q['preclose'] = q['close'].shift(1); q['turn'] = q['volume'] / q['volume'].rolling(20).mean()
    f = M.build_features(q).iloc[max(M.WINDOWS):]; f = f.iloc[f.notna().all(axis=1).values.argmax():]
    x = M.causal_wavelet(f)[0] if wavelet else f.copy()
    x = x.iloc[255:]  # same rows in both variants
    lab = (q.close.shift(-6) / q.close.shift(-1) - 1).reindex(x.index)
    yrs = x.index.year.to_numpy(); folds = []
    for ty in [y for y in np.unique(yrs) if y >= 2020]:
        te = np.flatnonzero(yrs == ty); tr = np.flatnonzero(yrs < ty); tr = tr[tr < te[0] - 6]
        pre = make_pipeline(SimpleImputer(strategy='median'), StandardScaler()).fit(x.iloc[tr])
        folds.append((pre.transform(x.iloc[tr]), lab.iloc[tr].to_numpy(), pre.transform(x.iloc[te]), x.index[te]))
    return q, x, lab, folds


def run(folds, seeds, **kw):
    out = []
    for xtr, ytr, xte, d in folds:
        fr = pd.DataFrame(index=d)
        for s in seeds: fr[s] = M.fit_predict(xtr, ytr, xte, s, **kw)[0]
        out.append(fr)
    return pd.concat(out)


def metrics(score, close, lab, a='2020-01-01', b='2025-12-31'):
    c = close[a:b]; sig = M.threshold_signal(score, M.THRESH).reindex(c.index).fillna(0).astype(int)
    ls = report_bt(c, sig, 'long_short'); lo = report_bt(c, sig, 'long_only')
    j = pd.DataFrame({'s': score, 'l': lab}).loc[a:b].dropna()
    return dict(ls_ann=ls['ann'], ls_mdd=ls['mdd'], lo_ann=lo['ann'], trades=ls['trades'], ic=j.s.corr(j.l, method='spearman'))


def main():
    torch.set_num_threads(4); t0 = time.time()
    q, x, lab, folds = prepare(True); close = q.close
    f40 = OUT / 'sae_robust_seeds40.csv'
    P = pd.read_csv(f40, index_col=0, parse_dates=True).rename(columns=int) if f40.exists() else run(folds, range(40)); P.to_csv(OUT / 'sae_robust_seeds40.csv', float_format='%.6g'); print('40 seeds', f'{time.time() - t0:.0f}s', flush=True)
    rows = [dict(kind='single', seeds=str(s), **metrics(P[s], close, lab)) for s in range(40)]
    rows += [dict(kind='ens5', seeds=f'{g}-{g + 4}', **metrics(P[list(range(g, g + 5))].mean(axis=1), close, lab)) for g in range(0, 40, 5)]
    rows += [dict(kind='ens10', seeds=f'{g}-{g + 9}', **metrics(P[list(range(g, g + 10))].mean(axis=1), close, lab)) for g in range(0, 40, 10)]
    rows.append(dict(kind='ens40', seeds='0-39', **metrics(P.mean(axis=1), close, lab)))
    # post-publication window
    for kind, sc in [('ens5_0-4', P[list(range(5))].mean(axis=1)), ('ens40', P.mean(axis=1))]:
        rows.append(dict(kind='post_pub_' + kind, seeds='', **metrics(sc, close, lab, '2026-02-03', '2026-10-16'),
                         bh_ann=float(np.expm1(np.log1p(close['2026-02-03':'2026-10-16'].pct_change().dropna()).mean() * 252))))
    # ablations (10 seeds)
    _, x0, _, folds0 = prepare(False)
    P0 = run(folds0, range(10)).mean(axis=1); rows.append(dict(kind='ablate_no_wavelet', seeds='0-9', **metrics(P0, close, lab)))
    P1 = run(folds, range(10), wr=0.0, ws=0.0).mean(axis=1); rows.append(dict(kind='ablate_plain_mlp(no recon/sparsity)', seeds='0-9', **metrics(P1, close, lab)))
    rid = pd.concat([pd.Series(Ridge(alpha=100.0).fit(xtr, ytr).predict(xte), index=d) for xtr, ytr, xte, d in folds])
    rows.append(dict(kind='ridge_alpha100_same_features', seeds='', **metrics(rid, close, lab)))
    r5 = -(close / close.shift(5) - 1); r20 = close / close.shift(20) - 1
    rows.append(dict(kind='naive_reversal_-ret5', seeds='', **metrics(r5.reindex(P.index), close, lab)))
    rows.append(dict(kind='naive_momentum_ret20', seeds='', **metrics(r20.reindex(P.index), close, lab)))
    R = pd.DataFrame(rows); R.to_csv(OUT / 'sae_robust_summary.csv', index=False)
    pd.set_option('display.width', 250); print(R.round(4).to_string(index=False))
    sg = R[R.kind == 'single']; e5 = R[R.kind == 'ens5']
    print('single seed ls_ann: min/med/max', sg.ls_ann.min().round(3), sg.ls_ann.median().round(3), sg.ls_ann.max().round(3), ' ic med', sg.ic.median().round(3))
    print('ens5 ls_ann: min/med/max', e5.ls_ann.min().round(3), e5.ls_ann.median().round(3), e5.ls_ann.max().round(3), ' ic min', e5.ic.min().round(3))
    # what does the score load on
    sc = P.mean(axis=1)
    raw = pd.DataFrame({k: v.reindex(sc.index) for k, v in dict(
        ret5=close / close.shift(5) - 1, ret20=close / close.shift(20) - 1, ret60=close / close.shift(60) - 1, ret250=close / close.shift(250) - 1,
        close_ma20=close / close.rolling(20).mean(), close_ma250=close / close.rolling(250).mean(), vol20=close.pct_change().rolling(20).std(),
        volratio20=q.volume / q.volume.rolling(20).mean(), vol_20_120=q.volume.rolling(20).mean() / q.volume.rolling(120).mean()).items()})
    cor = raw.corrwith(sc, method='spearman'); cor.to_csv(OUT / 'sae_robust_score_loadings.csv'); print(cor.round(3).to_string())
    print(f'total {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
