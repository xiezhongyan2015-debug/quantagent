"""华源 SAE 择时（2026-02-02）——按 hugo2046/QuantsPlaybook 复现（v0.14.0 src/）逐函数重写的最小实现（2026-10-18）。

为什么重写而不直接 import：复现依赖 PyWavelets、TA-Lib、loguru、vectorbt、duckdb，本地研究 venv 都没有，且不装新包。
与复现源码的对应与差别（其余逐行照抄）：
  build_features      = factor_algo.build_features（92 维，rsi bounded）；MACD 用 pandas EMA 代替 TA-Lib（只在前 ~100 行预热期不同，预热 250 行后差异 < 1e-8）
  turn                = data_provider.prepare_quotes(turnover_policy="volume_proxy")：volume / 20 日均量（复现自带的显式偏离；本地没有指数换手率）
  preclose            = 前一日收盘
  causal_wavelet      = factor_algo.causal_wavelet（db4、J=4、256 日窗、reflect、只去 D1、取窗口末值）。
                        自写 PyWavelets 的 dwt/idwt（downsampling_convolution / upsampling_convolution_valid_sf + reflect 延拓），
                        用完全重构自检；整个操作对窗口是线性的，所以等价于一个 256 抽头的因果 FIR，直接卷积
  forward_label       = C[t+6]/C[t+1]-1（horizon 5, skip 1）
  AnnualExpandingSplit= 自然年扩展、测试年前清掉 gap=6 行
  预处理              = 每折训练行拟合 SimpleImputer(median)+StandardScaler
  SAERegressor        = model.py 原样（92→64→16 sigmoid 码；解码 16→64→92；预测器 16→32→1；Adam 1e-3，batch 256，100 epoch，
                        loss = MSE(pred) + 0.1·MSE(recon) + 0.1·KL(ρ=0.05)，标签除训练 std）
  threshold_signal    = ±0.2% 迟滞，首次触发前 0
样本外：2020–2026（2026 到 10-16）。行情 2010-01-04 起（比复现的 2007 晚），首个训练日约 2012-01。
输出 outputs/timing_p2_20261017/qp/sae_000905.csv：date, score5, score10, signal(5 seed, k=0.2%), signal10, signal_k0(5 seed, 符号)；
  score40/signal40 由 qp_sae_robust.py 的 40 seed 结果补入（seed 0–39 均值，k=0.2%）
日期 t 的值只用到 t 日收盘及以前的数据（特征、因果小波、训练集在测试年年初前且标签已实现）。
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from torch import nn

ROOT = Path('/Users/leslie/Downloads/中证500量化60min')
OUT = ROOT / 'outputs/timing_p2_20261017/qp'
WINDOWS = (5, 10, 20, 60, 120, 250)
MA_PAIRS = ((5, 10), (5, 20), (10, 20), (20, 60), (60, 120), (120, 250))
TEST_START, TEST_END = '2020-01-01', '2026-10-16'
HORIZON, SKIP, THRESH = 5, 1, 0.002
SEEDS = tuple(range(10))

# ---------------------------------------------------------------- features
def macd_hist(close):
    ema = lambda s, n: s.ewm(span=n, adjust=False).mean()
    dif = ema(close, 12) - ema(close, 26)
    return dif - ema(dif, 9)


def build_features(q):
    close, turn = q['close'], q['turn']
    prev = q['preclose'].where(q['preclose'] > 0)
    base = q[['open', 'high', 'low', 'close']].div(prev, axis=0)
    base.columns = [f'{c}_prev' for c in base]
    base['open_close'] = q['open'] / close
    base['turn'] = turn
    base['amplitude'] = (q['high'] - q['low']) / prev
    base['macd'] = macd_hist(close)
    ret = close.pct_change(fill_method=None)
    delta = close.diff()
    up, down = ret.clip(lower=0), -ret.clip(upper=0)
    signed_turn = np.sign(delta) * turn
    feats = dict(base.items()); ma = {}
    for w in WINDOWS:
        ma[w] = close.rolling(w).mean(); roller = turn.rolling(w)
        us, ds = up.rolling(w).sum(), down.rolling(w).sum(); tot = us + ds
        rsi = (us / tot.replace(0, np.nan)).where(~tot.eq(0), 0.5)
        d = {'close_ma': close / ma[w], 'return': close / close.shift(w) - 1, 'ret_std': ret.rolling(w).std(ddof=1),
             'turn_mean': roller.mean(), 'turn_std': roller.std(ddof=1), 'turn_q20': roller.quantile(0.2), 'turn_q80': roller.quantile(0.8),
             'amplitude_mean': base['amplitude'].rolling(w).mean(), 'open_prev_mean': base['open_prev'].rolling(w).mean(),
             'high_prev_mean': base['high_prev'].rolling(w).mean(), 'low_prev_mean': base['low_prev'].rolling(w).mean(),
             'rsi': rsi, 'obv': signed_turn.rolling(w).sum()}
        feats.update({f'{k}_{w}': v for k, v in d.items()})
    for a, b in MA_PAIRS:
        feats[f'ma_{a}_{b}'] = ma[a] / ma[b]
    return pd.DataFrame(feats, index=q.index).replace([np.inf, -np.inf], np.nan)

# ---------------------------------------------------------------- wavelet (PyWavelets-compatible db4, mode=reflect)
DEC_LO = np.array([-0.010597401785069032, 0.0328830116668852, 0.030841381835560764, -0.18703481171909309,
                   -0.027983769416859854, 0.6308807679298589, 0.7148465705529157, 0.2303778133088965])
REC_LO = DEC_LO[::-1].copy()
DEC_HI = np.array([(-1) ** (i + 1) * REC_LO[i] for i in range(8)])
REC_HI = DEC_HI[::-1].copy()


def _ext(x, idx):
    n = len(x); idx = np.asarray(idx)
    idx = np.where(idx < 0, -idx, idx)
    idx = np.where(idx > n - 1, 2 * (n - 1) - idx, idx)
    return x[idx]


def dwt(x, f):
    n, F = len(x), len(f)
    out = np.empty((n + F - 1) // 2)
    for o, i in enumerate(range(1, n + F - 1, 2)):
        out[o] = f @ _ext(x, i - np.arange(F))
    return out


def idwt(ca, cd):
    F = len(REC_LO); N = len(ca); out = np.zeros(2 * N - F + 2)
    for o, i in enumerate(range(F // 2 - 1, N)):
        a = ca[i - np.arange(F // 2)]; d = cd[i - np.arange(F // 2)]
        out[2 * o] = REC_LO[0::2] @ a + REC_HI[0::2] @ d
        out[2 * o + 1] = REC_LO[1::2] @ a + REC_HI[1::2] @ d
    return out


def wavedec(x, level):
    coeffs, a = [], x
    for _ in range(level):
        d = dwt(a, DEC_HI); a = dwt(a, DEC_LO); coeffs.append(d)
    return [a] + coeffs[::-1]


def waverec(coeffs):
    a = coeffs[0]
    for d in coeffs[1:]:
        if len(a) == len(d) + 1: a = a[:-1]
        a = idwt(a, d)
    return a


def denoise_last(x, level=4):
    c = wavedec(x, level); c[-1] = np.zeros_like(c[-1])
    return waverec(c)[len(x) - 1]


def wavelet_fir(window=256, level=4):
    rng = np.random.default_rng(0); x = rng.standard_normal(window)
    pr = waverec(wavedec(x, level))[:window]
    assert np.abs(pr - x).max() < 1e-9, f'perfect reconstruction failed {np.abs(pr - x).max()}'
    w = np.array([denoise_last(np.eye(window)[k], level) for k in range(window)])
    assert abs(w @ x - denoise_last(x, level)) < 1e-10
    return w


def causal_wavelet(features, window=256):
    w = wavelet_fir(window)
    v = features.to_numpy(float); assert np.isfinite(v).all()
    res = np.full_like(v, np.nan)
    win = np.lib.stride_tricks.sliding_window_view(v, window, axis=0)  # (n-window+1, p, window)
    res[window - 1:] = win @ w
    return pd.DataFrame(res, index=features.index, columns=features.columns), w

# ---------------------------------------------------------------- model (model.py verbatim in substance)
class SAE(nn.Module):
    def __init__(self, p, hidden=64, code=16):
        super().__init__()
        self.encoder = nn.Sequential(nn.Linear(p, hidden), nn.ReLU(), nn.Linear(hidden, code), nn.Sigmoid())
        self.decoder = nn.Sequential(nn.Linear(code, hidden), nn.ReLU(), nn.Linear(hidden, p))
        self.predictor = nn.Sequential(nn.Linear(code, 32), nn.ReLU(), nn.Linear(32, 1))

    def forward(self, x):
        c = self.encoder(x); return self.predictor(c).squeeze(-1), self.decoder(c), c


def sparse_kl(code, rho):
    a = code.mean(dim=0).clamp(1e-6, 1 - 1e-6)
    return (rho * torch.log(rho / a) + (1 - rho) * torch.log((1 - rho) / (1 - a))).sum()


def fit_predict(xtr, ytr, xte, seed, epochs=100, bs=256, lr=1e-3, wr=0.1, ws=0.1, rho=0.05):
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(seed); net = SAE(xtr.shape[1])
    scale = max(float(np.std(ytr, ddof=1)), 1e-6)
    x = torch.as_tensor(xtr, dtype=torch.float32); y = torch.as_tensor(ytr / scale, dtype=torch.float32)
    opt = torch.optim.Adam(net.parameters(), lr=lr); rng = np.random.default_rng(seed); net.train(); act = np.nan
    for _ in range(epochs):
        perm = rng.permutation(len(x)); s_act = 0.0
        for st in range(0, len(x), bs):
            ids = perm[st:st + bs]; pred, rec, code = net(x[ids])
            loss = nn.functional.mse_loss(pred, y[ids]) + wr * nn.functional.mse_loss(rec, x[ids]) + ws * sparse_kl(code, rho)
            opt.zero_grad(); loss.backward(); opt.step(); s_act += len(ids) * code.mean().item()
        act = s_act / len(x)
    net.eval()
    with torch.no_grad():
        p, _, _ = net(torch.as_tensor(xte, dtype=torch.float32))
    return p.numpy() * scale, act


def threshold_signal(score, k):
    ev = pd.Series(np.select([score > k, score < -k], [1.0, -1.0], default=np.nan), index=score.index)
    return ev.ffill().fillna(0).astype(int)

# ---------------------------------------------------------------- pipeline
def main():
    torch.set_num_threads(4)
    t0 = time.time()
    q = pd.read_parquet(ROOT / 'outputs/timing_p2_20261017/index_000905.parquet').sort_index()
    q = q.loc[:TEST_END].copy()
    q['preclose'] = q['close'].shift(1)
    q['turn'] = q['volume'] / q['volume'].rolling(20).mean()          # volume_proxy
    feats = build_features(q).iloc[max(WINDOWS):]
    feats = feats.iloc[feats.notna().all(axis=1).values.argmax():]       # proxy turn needs 19 extra rows (20d mean + 250d roll)
    assert feats.notna().all().all(), feats.columns[feats.isna().any()].tolist()
    assert feats.shape[1] == 92
    den, w = causal_wavelet(feats); den = den.iloc[255:]
    close = q['close']
    label = (close.shift(-(SKIP + HORIZON)) / close.shift(-SKIP) - 1).reindex(den.index)
    x = den
    years = x.index.year.to_numpy(); test_years = [y for y in np.unique(years) if y >= pd.Timestamp(TEST_START).year]
    gap = HORIZON + SKIP
    preds, folds = [], []
    for ty in test_years:
        test = np.flatnonzero(years == ty); train = np.flatnonzero(years < ty); train = train[train < test[0] - gap]
        ytr = label.iloc[train].to_numpy(); assert np.isfinite(ytr).all()
        pre = make_pipeline(SimpleImputer(strategy='median', keep_empty_features=True), StandardScaler()).fit(x.iloc[train])
        xtr, xte = pre.transform(x.iloc[train]), pre.transform(x.iloc[test])
        fr = pd.DataFrame(index=x.index[test]); acts = []
        for sd in SEEDS:
            fr[f'seed_{sd}'], a = fit_predict(xtr, ytr, xte, sd); acts.append(a)
        preds.append(fr)
        folds.append(dict(year=ty, train_start=x.index[train[0]].date(), train_end=x.index[train[-1]].date(),
                          label_end=x.index[train[-1] + gap].date(), test_start=x.index[test[0]].date(), test_end=x.index[test[-1]].date(),
                          n_train=len(train), n_test=len(test), mean_activation=float(np.mean(acts))))
        print(folds[-1], f'{time.time() - t0:.0f}s', flush=True)
    P = pd.concat(preds)
    P['score5'] = P[[f'seed_{s}' for s in range(5)]].mean(axis=1); P['score10'] = P[[f'seed_{s}' for s in SEEDS]].mean(axis=1)
    out = pd.DataFrame({'score5': P.score5, 'score10': P.score10, 'signal': threshold_signal(P.score5, THRESH),
                        'signal10': threshold_signal(P.score10, THRESH), 'signal_k0': np.sign(P.score5).astype(int)})
    out.index.name = 'date'
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT / 'sae_000905.csv', float_format='%.6g')
    P.to_csv(OUT / 'sae_000905_seeds.csv', float_format='%.6g')
    pd.DataFrame(folds).to_csv(OUT / 'sae_000905_folds.csv', index=False)
    np.savetxt(OUT / 'sae_wavelet_fir_db4_256.txt', w)
    seed_corr = P[[f'seed_{s}' for s in SEEDS]].corr().values[np.triu_indices(10, 1)]
    print('seed corr min/median', seed_corr.min().round(3), np.median(seed_corr).round(3))
    print('fir: sum', w.sum().round(6), 'last 8 taps', w[-8:].round(4), 'lag-weighted mean', (w * np.arange(255, -1, -1)).sum().round(3))
    print(out.tail(), f'total {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
