"""Ordinary, predeclared development tasks; definitions are not chosen by returns."""
from copy import deepcopy

TASKS = {
    'price_repair': {
        'case_id': 'ashare_csi500_price_repair_v1', 'family': 'price_repair',
        'task': '研究A股下跌后的价格修复：哪些修复会延续，哪些只是下跌中的短暂反弹？提出竞争解释，用实验区分并改进策略。',
        'baseline': {'score_expression': '-pct_change(close, 20)',
            'filter_expression': '(close < lag(close, 20) * 0.95) & (close > lag(close, 1))',
            'top_n': 10, 'rebalance_days': 5, 'gross_exposure': 0.8},
    },
    'relative_strength': {
        'case_id': 'ashare_csi500_relative_strength_v1', 'family': 'trend_continuation',
        'task': '研究A股相对强弱的持续与反转。普通起点是优先持有此前一段时间涨幅较大的股票；自行研究哪些条件支持持续、哪些意味着衰竭，并允许否定起点。',
        'baseline': {'score_expression': 'pct_change(close, 60)',
            'filter_expression': '(pct_change(close, 60) > 0) & (cs_rank(pct_change(close, 60)) >= 0.8)',
            'top_n': 10, 'rebalance_days': 5, 'gross_exposure': 0.8},
    },
    'volume_expansion': {
        'case_id': 'ashare_csi500_volume_expansion_v1', 'family': 'volume_price',
        'task': '研究A股成交量相对近期常态突然增加之后的价格行为。放量可能有多种原因，不预设它代表建仓，也不提供已知有效的辅助条件；自行提出和区分解释，设计可执行策略，允许认定无效。',
        'baseline': {'score_expression': 'amount / lag(rolling_mean(amount, 20), 1)',
            'filter_expression': '(amount > 2 * lag(rolling_mean(amount, 20), 1)) & (close > open)',
            'top_n': 10, 'rebalance_days': 5, 'gross_exposure': 0.8},
    },
    'sharp_drop_repair': {
        'case_id': 'ashare_csi500_sharp_drop_repair_v1', 'family': 'price_repair',
        'task': '研究A股单日急跌后的价格修复。普通起点是前一个交易日下跌超过3%，最新收盘上涨。根据证据改进或放弃，不预设急跌必然修复。',
        'baseline': {'score_expression': '-lag(pct_change(close, 1), 1)',
            'filter_expression': '(lag(pct_change(close, 1), 1) < -0.03) & (pct_change(close, 1) > 0)',
            'top_n': 10, 'rebalance_days': 5, 'gross_exposure': 0.8},
    },
    'price_breakout': {
        'case_id': 'ashare_csi500_price_breakout_v1', 'family': 'trend_continuation',
        'task': '研究A股突破近期价格区间后的延续。普通起点是收盘超过此前20个已完成交易日的最高收盘价。根据证据改进或放弃，不预设突破必然延续。',
        'baseline': {'score_expression': 'close / lag(rolling_max(close, 20), 1) - 1',
            'filter_expression': 'close > lag(rolling_max(close, 20), 1)',
            'top_n': 10, 'rebalance_days': 5, 'gross_exposure': 0.8},
    },
}


def task_definition(name='price_repair'):
    if name not in TASKS:
        raise ValueError('unknown A-share task')
    return deepcopy(TASKS[name])
