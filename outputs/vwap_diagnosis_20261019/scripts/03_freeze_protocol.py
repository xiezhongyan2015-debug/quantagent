"""Freeze the holding-period replay protocol before any account return is computed."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import datetime
import common as C

path = C.OUT / 'PROTOCOL.json'
assert not path.exists(), 'protocol already frozen; do not overwrite'
inputs = {str(p.relative_to(C.ROOT)): C.sha(p) for p in [
    C.PILOT / 'P0_plans.npz', C.PILOT / 'P0_daily.csv', C.PILOT / 'VWAP_NULL_head4_daily.csv', C.PILOT / 'SUMMARY.json',
    C.PARQUET, C.RISK, C.NEUTRAL_PY, C.ZSCORE_PY,
    C.SNAP / 'HOME/gru_minute_vwap_20261014/account_reference_inputs_v1/candidate_replay.py',
    *[C.INFER / 'raw_predictions' / f'{a}_seed{s}.npz' for a in C.ARMS for s in C.SEEDS],
    *[C.INFER / f'{a}_{h}_ensemble_signals.npz' for a in C.ARMS for h in ('head3', 'head4')]]}
protocol = {
    'schema': 'vwap_holding_period_replay_protocol_v1',
    'frozen_at_local': datetime.datetime.now().isoformat(timespec='seconds'),
    'frozen_before_any_new_account_return': True,
    'purpose': 'Test the mentor full scheme (T+1 VWAP entry, hold N) versus the current daily EMA0.2 use, on frozen pilot3 signals.',
    'engine': {
        'source': 'pilot_v1 candidate_replay.py tilt_target/account/paired_summary arithmetic (sha d4e3b4ad...), reimplemented with a trade-day mask and per-sleeve capital; '
                  'must reproduce the extracted original bit-for-bit on daily runs before use',
        'skeleton': 'PCF reference = pilot_v1 P0_plans target (798 names, 416 execution rows 2025-01-02..2026-09-17, signal T-1 15:00)',
        'prices': 'pcf_overlay daily_prices.parquet hfq open/close/VWAP(amount/volume*factor); fill = prior-session open,close,amount>0 and isST!=1 (pilot_v1 rule)',
        'k': 0.5, 'clip': [-2, 2], 'unscored_multiplier': 1.0, 'stock_budget': 'reference sum (<=0.98)',
        'capital_cny': 10000000.0, 'min_trade_notional_cny': 1000.0,
    },
    'calibration': {
        'rebuild_base_and_reproduce': ['P0', 'VWAP_NULL_head4 (sealed ensemble score, daily EMA0.2, open execution, 2bp)'],
        'targets': 'pilot_v1 *_daily.csv NAV and SUMMARY continuous2025_2026 VWAP_NULL_head4_vs_P0 (rel CAGR -0.0013, 95% [-0.0587, 0.0607])',
        'accept_if': 'max |NAV diff| < 1e-6 (exact) OR |delta annualized relative CAGR| < 0.10pp and CI endpoints within 0.20pp; otherwise report and treat later numbers as approximate',
    },
    'signals': {
        'arms': list(C.ARMS), 'heads': ['head3', 'head4'],
        'score': 'per-seed pop-z mean -> four-exposure OLS neutralize (vol, turnover, logcap, beta60) -> EMA(alpha) across years from 2024-01-02 -> member pop-z; alpha=0.2 equals sealed ensemble export',
    },
    'usages': {
        'a_ema0.2': 'daily rebalance, EMA alpha 0.2 (current pilot_v1 usage)',
        'b_ema0.1': 'daily rebalance, EMA alpha 0.1',
        'b_ema0.05': 'daily rebalance, EMA alpha 0.05',
        'c1_tranche': 'PRIMARY (c): N sleeves of capital/N; sleeve j rebalances to tilt(reference_t, score_t) only on days t with (t-j) mod N == 0 and holds units otherwise; all sleeves build on day 0. score = neutralized, no EMA (alpha=1). N=5 for head3, N=20 for head4',
        'c1_tranche_ema0.2': 'secondary: same tranche structure with the EMA0.2 account score',
        'c2_boxcar': 'secondary: daily rebalance to reference_t x mean of the last N days multipliers (1+0.5clip(z), alpha=1 score), isolates signal aggregation from skeleton staleness',
    },
    'execution_price': {'open': 'T+1 open (pilot_v1)', 'vwap': 'T+1 daily VWAP (mentor scheme); P0 and SHORT rerun with the same execution price for same-caliber comparison'},
    'costs': {'base_fee_per_side': 0.0002, 'stress_fee_per_side': 0.0004},
    'baselines': ['P0 daily (per execution price and fee)', 'P0_tranche5 and P0_tranche20 (no tilt, same staleness) for c1'],
    'comparisons': ['candidate vs P0 (same execution, fee)', 'c1 candidate vs P0_trancheN', 'VWAP arm vs SHORT arm with identical usage/N/execution/fee (same NULL/STATS suffix and head)',
                    'VWAP arm vs SHORT_<suffix>_head4 a_ema0.2 open-execution (current SHORT use) at same fee'],
    'windows': {'continuous2025_2026': 'all 416 rows, rebase at first close (initial build excluded)', '2025': '243 rows', '2026': 'last 2025 close anchors 173 2026 returns; no reset'},
    'statistics': {'annualized_relative_cagr': 'prod(1+rc)/(1+rb) ** (252/n) - 1', 'bootstrap': 'paired circular 20-day blocks, 2000 replicates, seed 20261011 (pilot_v1 paired_summary)',
                   'turnover': 'mean daily two-way traded notional / close NAV, excluding build row', 'multiple_testing_adjusted': False},
    'decision_rule_declared_in_advance': 'The holding-period scheme is worth further work only if, for head4 (primary) on VWAP_NULL or VWAP_STATS, c1 at 2bp beats both P0 and the same-usage SHORT arm with a 95% CI excluding 0 on continuous2025_2026, keeps the sign in both years, and survives 4bp; otherwise not.',
    'inputs_sha256': inputs,
    'no_live_config_touched': True, 'no_network': True, 'no_model_training': True,
}
C.write_json(path, protocol)
print('frozen', C.sha(path))
