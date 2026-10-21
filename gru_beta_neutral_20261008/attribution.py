"""Day-one attribution of frozen, costed replay results. No model selection."""
from pathlib import Path
import hashlib, json, os
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('GRU_DATA', ROOT / 'gru_research/artifacts/dataset'))
SOURCE = ROOT / '训练回测证据与复核/evidence/replay14_fee2_allin'
OUT = ROOT / 'outputs/gru_beta_neutral_20261008'
FACTOR_NAMES = ['market', 'low_volatility', 'low_turnover', 'small_size']

def sha(p):
    with open(p, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def hac_fit(x, y, lag=20):
    coef = np.linalg.lstsq(x, y, rcond=None)[0]
    residual = y - x @ coef
    bread = np.linalg.pinv(x.T @ x)
    z = x * residual[:, None]
    meat = z.T @ z
    for k in range(1, min(lag + 1, len(x))):
        cross = z[k:].T @ z[:-k]
        meat += (1 - k / (lag + 1)) * (cross + cross.T)
    covariance = bread @ meat @ bread * len(x) / (len(x) - x.shape[1])
    return coef, residual, np.sqrt(np.maximum(np.diag(covariance), 0))

def block_alpha(x, y, reps=1000):
    rng = np.random.default_rng(20261008)
    n = len(y)
    estimates = []
    for _ in range(reps):
        starts = rng.integers(0, max(n - 20 + 1, 1), size=int(np.ceil(n / 20)))
        ix = np.concatenate([np.arange(a, min(a + 20, n)) for a in starts])[:n]
        estimates.append(np.linalg.lstsq(x[ix], y[ix], rcond=None)[0][0] * 252)
    return np.quantile(estimates, [.025, .975])

def build_style_returns():
    times = np.load(DATA / 'times.npy', mmap_mode='r')
    dates = pd.to_datetime(times[3::4], utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d')
    close = np.asarray(np.load(DATA / 'hour_ohlcv.npy', mmap_mode='r')[3::4, :, 3], float)
    members = np.load(DATA / 'members.npy', mmap_mode='r')[3::4]
    raw = np.load(DATA / 'slow_raw_by_day.npy', mmap_mode='r')
    returns = np.full_like(close, np.nan)
    valid = np.isfinite(close[1:]) & np.isfinite(close[:-1]) & (close[1:] > 0) & (close[:-1] > 0)
    np.divide(close[1:], close[:-1], out=returns[1:], where=valid)
    returns[1:] -= 1
    rows = []
    for d in np.flatnonzero(dates >= '2025-01-01'):
        row = {'date': dates[d]}
        # These raw features already use the prior complete session / prior weekly snapshot.
        for name, k in zip(FACTOR_NAMES[1:], [5, 11, 15]):
            style = np.asarray(raw[d, :, k], float)
            eligible = members[d-1] & np.isfinite(style)
            ids = np.flatnonzero(eligible)
            order = ids[np.argsort(style[ids], kind='stable')]
            cut = max(1, int(np.ceil(len(order) / 5)))
            low, high = order[:cut], order[-cut:]
            row[name] = float(np.nanmean(returns[d, low]) - np.nanmean(returns[d, high]))
            row[name + '_eligible'] = len(order)
            row[name + '_missing_marks'] = int(np.isnan(returns[d, np.r_[low, high]]).sum())
            row[name + '_selected'] = len(low) + len(high)
        rows.append(row)
    return pd.DataFrame(rows)

def test():
    rng = np.random.default_rng(4)
    x = np.c_[np.ones(200), rng.normal(0, .01, (200, 4))]
    expected = np.array([.0002, .8, .2, -.1, .05])
    y = x @ expected
    coef, residual, se = hac_fit(x, y)
    assert np.allclose(coef, expected, atol=1e-10)
    assert np.max(np.abs(residual)) < 1e-12
    nav = np.cumprod(1 + y)
    previous = np.r_[1, nav[:-1]]
    linked = (previous[:, None] * (x * coef)).sum(axis=0)
    assert abs(linked.sum() - (nav[-1] - 1)) < 1e-12
    assert np.isfinite(se).all()
    return ['known multivariate coefficients recovered', 'wealth-linked contribution reconciliation', 'HAC finite on exact fit']

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    checks = test()
    factors = build_style_returns()
    factors.to_csv(OUT / 'style_factor_returns.csv', index=False)
    rows, contributions = [], []
    source_hashes = {}
    benchmark_path = OUT/'benchmark_sources/H00905_daily.csv'
    total_return_benchmark = pd.read_csv(benchmark_path).set_index('date').close if benchmark_path.exists() else None
    if total_return_benchmark is not None:
        source_hashes[str(benchmark_path.relative_to(ROOT))] = sha(benchmark_path)
    for path in sorted(SOURCE.glob('*_daily.csv')):
        tag = path.name.removesuffix('_daily.csv')
        year = int(tag[:4])
        d = pd.read_csv(path)
        f = d.merge(factors, on='date', how='left', validate='one_to_one')
        assert len(f) == len(d) and f[FACTOR_NAMES[1:]].notna().all().all()
        nav = f.nav.to_numpy()
        previous = np.r_[1, nav[:-1]]
        y = nav / previous - 1
        bnav = f.benchmark_nav.to_numpy()
        if total_return_benchmark is not None:
            base = total_return_benchmark.loc[total_return_benchmark.index < f.date.iloc[0]].iloc[-1]
            bnav = total_return_benchmark.loc[f.date].to_numpy()/base
        market = bnav / np.r_[1, bnav[:-1]] - 1
        x = np.c_[np.ones(len(f)), market, f[FACTOR_NAMES[1:]].to_numpy()]
        coef, residual, se = hac_fit(x, y)
        capm, _, capm_se = hac_fit(x[:, :2], y)
        parts = np.c_[x * coef, residual]
        linked = (previous[:, None] * parts).sum(axis=0)
        error = linked.sum() - (nav[-1] - 1)
        assert abs(error) < 1e-10, (tag, error)
        lo, hi = block_alpha(x, y)
        r2 = 1 - np.var(residual) / np.var(y)
        row = dict(case=tag, year=year, days=len(f), net_or_gross='gross' if '_gross_' in tag else 'net',
                   interval_return=nav[-1]-1, benchmark_return=bnav[-1]-1,
                   net_excess_pp=(nav[-1]-bnav[-1])*100, capm_beta=capm[1],
                   capm_alpha_annual=capm[0]*252, multivariate_market_beta=coef[1],
                   residual_alpha_annual=coef[0]*252, residual_alpha_hac_se=se[0]*252,
                   residual_alpha_block20_lower=lo, residual_alpha_block20_upper=hi,
                   multivariate_r2=r2, design_condition_number=np.linalg.cond(x),
                   linked_market_pp=linked[1]*100,
                   linked_volatility_pp=linked[2]*100, linked_turnover_pp=linked[3]*100,
                   linked_size_pp=linked[4]*100,
                   linked_residual_including_intercept_pp=(linked[0]+linked[5])*100,
                   reconciliation_error=error, retrospective_attribution_only=True)
        rows.append(row)
        for i, date in enumerate(f.date):
            contributions.append(dict(case=tag, date=date, realized_return=y[i],
                                      intercept=parts[i, 0], market=parts[i, 1],
                                      low_volatility=parts[i, 2], low_turnover=parts[i, 3],
                                      small_size=parts[i, 4], unexplained=parts[i, 5],
                                      prior_wealth=previous[i]))
        source_hashes[str(path.relative_to(ROOT))] = sha(path)
    table = pd.DataFrame(rows)
    table.to_csv(OUT / 'attribution_all_cases.csv', index=False)
    ensemble = table[table.case.str.contains('ensemble') & (table.net_or_gross == 'net')]
    ensemble.to_csv(OUT / 'attribution_ensemble.csv', index=False)
    pd.DataFrame(contributions).to_csv(OUT / 'attribution_daily.csv', index=False)
    protocol = {
        'phase': 'day1_attribution_complete', 'as_of': '2026-09-23',
        'benchmark': 'official H00905 total return' if total_return_benchmark is not None else 'original CSI500 price index',
        'strategy_source': str(SOURCE), 'source_hashes': source_hashes,
        'source_code_sha256': sha(__file__), 'tests': checks,
        'fit': 'Independent 2025 / 2026 OLS: daily net strategy returns = intercept + market + low-volatility + low-turnover + small-size factor returns + residual',
        'styles': 'Equal-weight low-minus-high quintile portfolios. Membership is from prior session; raw daily features already use prior complete session or strictly prior weekly snapshot. Selection never uses future return availability. Missing realized marks are excluded after fixed selection and counted.',
        'exact_linking': 'Multiply each fitted daily component by the actual prior strategy wealth, then sum. Components reconcile to compounded terminal return. Residual includes intercept for three-part decomposition.',
        'uncertainty': 'Newey-West 20 lags and 1,000 paired moving-block 20-session bootstrap refits. Annual arithmetic intercept, not compounded realized excess.',
        'limitations': ['Retrospective association, not proof of causal alpha or a forecast.', 'Correlated styles make individual attribution model dependent.', 'Daily style-factor horizon differs from 14:01 execution; overnight exposure, industry and illiquidity are not separately controlled.', '2025/2026 already exposed; no model or checkpoint selection on these results.'],
    }
    (OUT / 'attribution_protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2))
    print(ensemble[['case','capm_beta','residual_alpha_annual','residual_alpha_block20_lower','residual_alpha_block20_upper','linked_market_pp','linked_volatility_pp','linked_turnover_pp','linked_size_pp','linked_residual_including_intercept_pp']].round(5).to_string(index=False))

if __name__ == '__main__':
    main()
