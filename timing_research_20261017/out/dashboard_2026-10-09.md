【择时仪表盘·只读，不参与交易】
波段：中证500下行段·底背离-11.4%（效率1.07，历史51%的同向段走得更远）；创业板指下行段·底背离-18.6%（效率1.41，历史37%的同向段走得更远）；沪深300下行段、中证1000上行段
LPPLS 泡沫置信度：中证500 正0.00/负0.00（40日峰 0.00/0.00，无信号）；创业板指 正0.00/负0.00（40日峰 0.00/0.00，无信号）
3.0 战术：成交额活跃度 −1（Z=0.02）、行业集中度 −1（CR10=78%）、PB 动量 −1 → 合计 -3/3
宽度（中证500）：今日上涨 62%（20日均 43%），站上60日线 29%，60日新低 8%；背离：无
兴证外部：不定期择时·中证500 不看多（10-09）；股债性价比3.0分位 19.8%（09-30）；大小盘 小盘（09-30）；成长价值 价值（09-30）

```json
{
 "day": "2026-10-09",
 "generated_at": "2026-10-17T12:01:00",
 "panel": "/Users/leslie/Downloads/中证500量化60min/c5_core/data/A_Layer_Live",
 "swing": {
  "000905": {
   "asof": "2026-10-09",
   "state": "下行段·底背离",
   "falling": true,
   "dir": 1,
   "exception": -1,
   "status": -1,
   "last_pivot": "2026-08-17",
   "last_pivot_px": 8184.64,
   "leg_return": -0.1141,
   "price_eff": 1.067,
   "time_eff": 2.944,
   "n_completed_same": 55,
   "p_leg_extends": 0.509,
   "median_leg_amp": -0.1204,
   "n_pivots": 113
  },
  "000300": {
   "asof": "2026-10-09",
   "state": "下行段",
   "falling": true,
   "dir": -1,
   "exception": 1,
   "status": -1,
   "last_pivot": "2026-06-22",
   "last_pivot_px": 5059.66,
   "leg_return": -0.1467,
   "price_eff": 2.146,
   "time_eff": 7.786,
   "n_completed_same": 60,
   "p_leg_extends": 0.2,
   "median_leg_amp": -0.0881,
   "n_pivots": 123
  },
  "000852": {
   "asof": "2026-10-09",
   "state": "上行段",
   "falling": false,
   "dir": 1,
   "exception": 1,
   "status": 1,
   "last_pivot": "2026-07-30",
   "last_pivot_px": 6900.66,
   "leg_return": 0.037,
   "price_eff": 0.129,
   "time_eff": 2.448,
   "n_completed_same": 47,
   "p_leg_extends": 0.957,
   "median_leg_amp": 0.1091,
   "n_pivots": 96
  },
  "399006": {
   "asof": "2026-10-09",
   "state": "下行段·底背离",
   "falling": true,
   "dir": 1,
   "exception": -1,
   "status": -1,
   "last_pivot": "2026-08-17",
   "last_pivot_px": 3740.16,
   "leg_return": -0.1863,
   "price_eff": 1.406,
   "time_eff": 2.944,
   "n_completed_same": 51,
   "p_leg_extends": 0.373,
   "median_leg_amp": -0.1446,
   "n_pivots": 105
  }
 },
 "lppls": {
  "000905": {
   "asof": "2026-10-09",
   "pos": 0.0,
   "neg": 0.0,
   "pos_rule": {
    "peak": 0.0,
    "peak_date": "2026-08-07",
    "now": 0.0,
    "fired": false
   },
   "neg_rule": {
    "peak": 0.0,
    "peak_date": "2026-08-07",
    "now": 0.0,
    "fired": false
   },
   "computed_days": 60,
   "seconds": 94.7,
   "signal": 0
  },
  "399006": {
   "asof": "2026-10-09",
   "pos": 0.0,
   "neg": 0.0,
   "pos_rule": {
    "peak": 0.0,
    "peak_date": "2026-08-07",
    "now": 0.0,
    "fired": false
   },
   "neg_rule": {
    "peak": 0.0,
    "peak_date": "2026-08-07",
    "now": 0.0,
    "fired": false
   },
   "computed_days": 60,
   "seconds": 92.3,
   "signal": 0
  }
 },
 "errors": {},
 "breadth": {
  "asof": "2026-10-09",
  "up_ratio": 0.62,
  "up_ratio_ma20": 0.434,
  "above_ma60": 0.29,
  "at_60d_low": 0.078,
  "weekly_corr": 0.799,
  "divergence": false,
  "divergence_kind": "无",
  "signal": 0
 },
 "tactical": {
  "activity": {
   "z": 0.02,
   "ma20_amount_yi": 17778.0,
   "signal": -1
  },
  "cr10": {
   "cr10": 0.775,
   "z": 0.01,
   "n_industries": 38,
   "signal": -1
  },
  "pb_momentum": {
   "spread_today": -0.0758,
   "ma20": -0.0113,
   "signal": -1
  },
  "total": -3,
  "asof": "2026-10-09"
 },
 "external": {
  "兴证股债性价比3.0分位": {
   "value": "19.8%",
   "as_of": "2026-09-30",
   "published": "2026-10-07",
   "source": "兴证金工月报"
  },
  "兴证大小盘": {
   "value": "小盘",
   "as_of": "2026-09-30",
   "published": "2026-10-07",
   "source": "兴证金工月报"
  },
  "兴证成长价值": {
   "value": "价值",
   "as_of": "2026-09-30",
   "published": "2026-10-07",
   "source": "兴证金工月报"
  },
  "兴证不定期择时·中证500": {
   "value": "不看多",
   "as_of": "2026-10-09",
   "published": "2026-10-09",
   "source": "兴证金工周报"
  },
  "兴证波段·沪深300": {
   "value": "下行段·前高5060·段内-14.67%",
   "as_of": "2026-10-09",
   "published": "2026-10-09",
   "source": "兴证金工周报"
  }
 },
 "stale": []
}
```
