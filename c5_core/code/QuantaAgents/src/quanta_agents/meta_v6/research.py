"""Small, durable research stages using the verified gpt-6-astra/xhigh gateway.

The researcher supplies hypotheses and portfolio decisions. This module records
and executes them; a model response, engineering test or exposed-data score never
automatically becomes a verified financial result.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
import traceback
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUN = ROOT / "output/research/meta_v6_factor_calendar_20260909"


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str)


def save_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dump(value)
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise ValueError("immutable artifact already exists with different content: " + str(path))
        return
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)


PROTOCOL = {
    "version": "v6_calendar_development_1",
    "objective": "Construct reasonable calendar-factor strategies and evaluate mean full-year net Sharpe > 1 without lookahead or unreported search",
    "data": {"root": "/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/parquet_cn_a_qfq_tradeable_v2_2015_2025",
             "calendar": "/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/calendars/day.txt",
             "membership": "/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/instruments/csi300.txt",
             "start": "2015-01-01", "end": "2024-12-31",
             "universe": "union of historical CSI300 members, with membership at the signal date",
             "scope_reason": "prior calendar-factor research universe, no performance-based stock selection; broader markets may be separately registered",
             "exposure": "all supplied 2015-2024 numeric data is previously_exposed_development",
             "held_out": "2025 numeric results withheld in this stage; earlier c007 aggregate exposure prevents globally pristine holdout claim"},
    "factor_screen": {"horizons": [1, 5, 20], "quantiles": 5,
                      "minimum_cross_section": 30,
                      "label": "adjusted_open[t+1+h]/adjusted_open[t+1]-1",
                      "selection": "economic hypothesis, coverage, sign stability, redundancy and incremental evidence; no automatic single-IC gate"},
    "development_partition": {"warmup": ["2015-01-01", "2015-12-31"],
                              "fit": ["2016-01-01", "2020-12-31"],
                              "temporal_diagnostics": ["2021-01-01", "2024-12-31"],
                              "not_independent_holdout": True},
    "account": {"capital": 1000000.0, "risk_free_rate": 0.02,
                "buy_commission": 0.0003, "sell_commission": 0.0003,
                "sell_levy_before_2023_08_28": 0.001,
                "sell_levy_from_2023_08_28": 0.0005,
                "slippage": 0.001, "min_commission": 5.0, "lot_size": 100,
                "max_prior_day_amount_fraction": 0.01,
                "signal": "day D close", "execution": "D+1 open",
                "accounting": "continuous adjusted units approximation; no separate dividend/tax cash entries",
                "missing": "no filled return labels; unavailable orders blocked; held marks retained and stale exposure reported",
                "terminal": "mark-to-market with retained inventory and explicit liquidation-cost stress, not assumed liquidation"},
    "evaluation": {"start": "2016-01-01", "end": "2024-12-31",
                   "sessions_per_year": 252, "volatility_ddof": 1,
                   "objective": "arithmetic mean of full-calendar-year net daily-return Sharpes",
                   "cash_days_and_losing_years": "retained; annual accounts never reset",
                   "zero_variance": "unknown Sharpe, never success",
                   "reports": ["all full years", "full period", "worst year", "drawdown", "costs", "turnover", "exposure", "cost stress", "missing data"]},
    "initial_stage_search": {"fixed_seed_factors": 3, "new_hypotheses_max": 8,
                             "portfolio_candidates_max": 4, "controlled_revisions_max": 2,
                             "purpose": "bounded first research stage, not a limit on the active user objective",
                             "rejected_and_failed_attempts": "retained in the factor library and research history"},
    "validation": {"known_exposure_never_reset": True, "future_value_perturbation_checks": True,
                   "no_year_or_stock_identity_predictors": True,
                   "train_only_fit": True, "label_horizon_purge_at_training_end": 21,
                   "financial_success": "requires complete real-data evidence, preserved exposure caveats, and suitable independent validation; never inferred from implementation tests"},
}

SEEDS = [
    {"name": "HF0219_ATR14", "direction": -1,
     "expression": "ema(where(high-low > abs(high-lag(close,1)), where(high-low > abs(low-lag(close,1)), high-low, abs(low-lag(close,1))), where(abs(high-lag(close,1)) > abs(low-lag(close,1)), abs(high-lag(close,1)), abs(low-lag(close,1)))),14)",
     "hypothesis": "Original ATR14-low calendar seed; absolute price-level sensitivity is a known concern"},
    {"name": "HF0017_TEMA20", "direction": -1,
     "expression": "3*ema(close,20)-3*ema(ema(close,20),20)+ema(ema(ema(close,20),20),20)",
     "hypothesis": "Original TEMA20-low calendar seed; treat absolute price level as a confound to test"},
    {"name": "HF0091_AMIHUD20", "direction": 1,
     "expression": "rolling_mean(abs(pct_change(close,1))/amount,20)",
     "hypothesis": "Original Amihud20-high calendar seed; illiquidity premium may be offset by trading costs"},
]


def initialize_run(root=DEFAULT_RUN):
    root = Path(root)
    save_once(root / "protocol.json", PROTOCOL)
    save_once(root / "seed_factors.json", SEEDS)
    return root


def object_schema(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


TEXT = {"type": "string"}
FACTOR_SCHEMA = object_schema({
    "name": TEXT, "expression": TEXT, "direction": {"type": "integer", "enum": [-1, 1]},
    "hypothesis": TEXT, "incremental_role": TEXT, "falsification": TEXT,
})
HYPOTHESIS_SCHEMA = object_schema({
    "factors": {"type": "array", "items": FACTOR_SCHEMA},
    "seed_review": TEXT, "combination_plan": TEXT, "selection_rule": TEXT,
    "missing_data_questions": TEXT, "failure_conditions": TEXT,
})


def hypothesis_prompt():
    return """You are the actual strategy researcher for QuantaAgents V6, using gpt-6-astra with xhigh effort.
Return a concise Chinese research declaration in the required JSON schema. Do not invent execution results.
The user wants structured, fast factor testing, then evidence-based combinations and sensible trading strategies,
with mean full-year net Sharpe > 1, no lookahead and no hidden overfitting. Instruments/trading methods are not
restricted by the user. This first registered stage uses the original historical CSI300 calendar-factor universe.
All 2015-2024 data is previously exposed development data. You have no new numeric results yet. Do not portray
the date split as independent OOS. 2025 numeric data is excluded, and some old aggregate 2025 exposure is known.

Original seeds and fixed protocol follow. Critically examine economic mechanisms and absolute-price confounding.
Propose at most eight additional distinct factor hypotheses, not eight near-identical parameter variants. Each
factor's expression produces only causal daily stock scores; direction +1 means high is preferred, -1 low preferred.
No stock IDs, future returns, year labels, fitted full-sample constants, random generated data or future normalization.
Available source fields: open, high, low, close (continuous adjusted signal prices anchored at the first observed
session to remove uniform future rescaling), volume, amount. Float_market_cap and total_market_cap may be available
but their historical publication/vintage is not independently certified. Market state may be derived causally later.
Do not use raw prices, qfq_ratio or adjustment_factor as alpha. HF0280 intraday features and monthly price-lag are
historical ideas, but their dedicated data/operators are not yet confirmed; do not silently replace them with proxies.

Safe expression operators: arithmetic + - * /, comparisons, where(condition,x,y), abs, log, clip(x,lo,hi),
lag(x,k), pct_change(x,k), rolling_mean/std/min/max/sum(x,w), cs_rank(x), ema(x,w), rolling_corr(x,y,w),
rolling_residual(y,x,w), ts_rank(x,w). Lag/change k must be positive; rolling windows are literal 2..120.
No if statements, pandas code, strings or free-form execution. Expressions must stay simple and short.
Discuss how to combine information, including a factor that is useful conditionally despite weak standalone IC.
Define falsification tests before seeing results. A seed is a previously researched prior, not your new discovery.
""" + "\nPROTOCOL\n" + dump(PROTOCOL) + "\nPRIOR SEEDS\n" + dump(SEEDS)


def call_researcher(root, stage, prompt, schema, *, timeout_seconds=1200):
    from . import gateway

    root = initialize_run(root)
    folder = root / "model_calls" / stage
    folder.mkdir(parents=True, exist_ok=True)
    receipt_path = folder / "admitted_receipt.json"
    encoded = lambda x: json.dumps(x, ensure_ascii=False, allow_nan=False, default=str)
    prompt_hash = hashlib.sha256(encoded(prompt).encode()).hexdigest()
    def admit(receipt):
        if receipt.get("runtime_identity", {}).get("verified") is not True:
            # Recover exactly the completed local session, without new inference.
            save_once(folder / "completion_before_identity_recovery.json", receipt)
            gateway.capture_saved_session(folder, receipt)
            receipt = gateway.verify_saved_completion(folder, expected_prompt_hash=prompt_hash,
                                                       expected_schema=schema)
        if receipt.get("runtime_identity", {}).get("verified") is not True:
            raise ValueError("completed response retained but local model/effort identity is not verified")
        save_once(receipt_path, receipt)
        return receipt
    if (folder / "codex_request.json").exists():
        # Recover an existing billable response offline. A live/incomplete call
        # is not restarted or replaced merely because observation expired.
        receipt = gateway.verify_saved_completion(folder, expected_prompt_hash=prompt_hash, expected_schema=schema)
        return admit(receipt)
    if (folder / "intent.json").exists():
        raise ValueError("stage intent already exists; inspect original state before any retry")
    intent = {"intent_version": 1, "intent_id": uuid4().hex,
              "prompt_hash": prompt_hash,
              "schema_hash": hashlib.sha256(encoded(schema).encode()).hexdigest(),
              "schema": schema, "model": "gpt-6-astra", "effort": "xhigh",
              "protocol_sha256": hashlib.sha256((root / "protocol.json").read_bytes()).hexdigest(),
              "stage": stage, "timeout_seconds": timeout_seconds, "created_epoch": time.time()}
    save_once(folder / "intent.json", intent)
    deadline = time.monotonic() + timeout_seconds
    def on_event(event):
        with (folder / "gateway_events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(dump(event) + "\n")
    try:
        receipt = gateway.CodexGateway(timeout_seconds=timeout_seconds).run(
            prompt=prompt, schema=schema, workdir=folder, on_event=on_event,
            cancelled=lambda: time.monotonic() >= deadline or (root / "cancel.request").exists())
        return admit(receipt)
    except Exception as exc:
        save_once(folder / "stage_failure.json", {"type": type(exc).__name__, "error": str(exc),
                  "usage": getattr(exc, "usage", None), "traceback": traceback.format_exc(),
                  "saved_response_must_be_recovered_before_new_paid_call": True})
        raise
