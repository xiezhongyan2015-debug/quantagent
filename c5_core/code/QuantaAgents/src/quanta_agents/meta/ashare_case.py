"""Frozen A-share research case with lazy, time-bounded market access.

The strategy interface only exposes causal factor expressions.  Final data is
sealed by default; confirmation is an executor-only evaluation surface, never
an agent diagnostic tool.  This adapter is research infrastructure, not evidence
that an architecture or strategy will achieve a target Sharpe ratio.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from quanta_agents.local_parquet_backtest import normalize_stock_code, run_target_weight_backtest
from quanta_agents.meta.factor_algebra import evaluate_expression, expression_guide, validate_expression
from quanta_agents.meta.ashare_tasks import task_definition


CASE_ID = "ashare_csi500_price_repair_v1"
DEFAULT_DATA_ROOT = Path("/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/parquet_cn_a_qfq_tradeable_v2_2015_2025")
DEFAULT_MEMBERSHIP_PATH = Path("/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/instruments/csi500.txt")
DEFAULT_CALENDAR_PATH = Path("/Users/leslie/Downloads/中证500量化60min/c5_core/data/qlib_data/qlib_bin/calendars/day.txt")
DEFAULT_SPLITS = {
    "development": {"start": "2017-01-01", "end": "2021-12-31"},
    "confirmation": {"start": "2022-01-01", "end": "2023-12-31"},
    "final": {"start": "2024-01-01", "end": "2025-12-31"},
}
FEATURE_COLUMNS = (
    "open", "high", "low", "close", "volume", "amount",
    "float_market_cap", "total_market_cap",
)
EXECUTION_COLUMNS = (
    "qfq_ratio", "raw_open", "raw_prev_close", "prev_close", "is_st", "is_delisting",
)
REQUIRED_COLUMNS = ("date", "code", *FEATURE_COLUMNS, *EXECUTION_COLUMNS)
MAIN_BOARD = re.compile(r"^(?:sh60[0135]|sz00[0123])\d{3}$")
MAX_STOCK_WEIGHT = 0.10
EXECUTION_SPEC = {
    "capital": 1_000_000.0,
    "risk_free_rate": 0.02,
    "buy_cost": 0.0003,
    "sell_cost": 0.0008,
    "sell_cost_before_change": 0.0013,
    "sell_cost_change_date": "2023-08-28",
    "slippage": 0.001,
    "lot_size": 100,
    "raw_share_lots": True,
    "min_commission": 5.0,
}
LIMITATIONS = [
    "integration_only: one case and a few runs cannot establish architecture superiority or stable Sharpe > 1",
    "Historical data may already have been used elsewhere in the project; confirmation is not certified globally unseen data",
    "Point-in-time index intervals are file snapshots; announcement-time provenance has not been independently audited",
    "Research OHLC uses each stock's first available warmup-session raw-price anchor; source vintage and historical share-capital provenance still require independent verification",
    "Daily opening execution is a model; queue position, auction capacity, intraday depth and delisting recoveries are not verified",
    "No independent suspension calendar is present; absent bars block trading and retained holdings use the engine's last known mark",
    "Volume and amount units follow the Qlib conversion convention; capacity has not been independently validated",
    "The final split is sealed by default and must not be repeatedly reopened for architecture selection",
]


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _finite(value: Any) -> float | None:
    number = float(value)
    return number if math.isfinite(number) else None


def strategy_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "score_expression": {"type": "string", "minLength": 1, "maxLength": 2000,
                                 "description": "Causal factor expression; highest scores are bought."},
            "filter_expression": {"type": "string", "minLength": 1, "maxLength": 2000,
                                  "description": "Causal expression; finite nonzero values pass."},
            "top_n": {"type": "integer", "minimum": 5, "maximum": 30},
            "rebalance_days": {"type": "integer", "minimum": 1, "maximum": 20},
            "gross_exposure": {"type": "number", "minimum": 0.2, "maximum": 1.0},
        },
        "required": ["score_expression", "filter_expression", "top_n", "rebalance_days", "gross_exposure"],
        "additionalProperties": False,
    }


def baseline_strategy() -> dict[str, Any]:
    """An ordinary decline-then-repair definition, selected without backtest search."""
    return {
        "score_expression": "-(close / lag(close, 20) - 1)",
        "filter_expression": "(close < lag(close, 20) * 0.95) & (close > lag(close, 1))",
        "top_n": 10,
        "rebalance_days": 5,
        "gross_exposure": 0.8,
    }


def validate_strategy(strategy: dict[str, Any]) -> dict[str, Any]:
    schema = strategy_schema()
    if not isinstance(strategy, dict) or set(strategy) != set(schema["required"]):
        raise ValueError("strategy must contain exactly the five declared strategy fields")
    result = dict(strategy)
    for field in ("score_expression", "filter_expression"):
        value = result[field]
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError(f"{field} must be a nonempty expression of at most 2000 characters")
        validate_expression(value, set(FEATURE_COLUMNS))
    for field, minimum, maximum in (("top_n", 5, 30), ("rebalance_days", 1, 20)):
        value = result[field]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{field} must be an integer in [{minimum}, {maximum}]")
    exposure = result["gross_exposure"]
    if isinstance(exposure, bool) or not isinstance(exposure, (int, float)) or not 0.2 <= exposure <= 1.0:
        raise ValueError("gross_exposure must be a finite number in [0.2, 1]")
    result["gross_exposure"] = float(exposure)
    return result


class AShareCase:
    """A shared read-only data adapter; init reads membership and file metadata only."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        allow_final = cfg.get("allow_final", False)
        if type(allow_final) is not bool:
            raise ValueError("allow_final must be an explicit boolean; text is not a release authorization")
        self.task_name = cfg.get("task", "price_repair")
        self.task_definition = task_definition(self.task_name)
        self.data_root = Path(cfg.get("data_root", DEFAULT_DATA_ROOT)).expanduser().resolve()
        self.membership_path = Path(cfg.get("membership_path", DEFAULT_MEMBERSHIP_PATH)).expanduser().resolve()
        calendar_default = DEFAULT_CALENDAR_PATH if self.data_root == DEFAULT_DATA_ROOT.resolve() else None
        calendar_path = cfg.get("calendar_path", calendar_default)
        self.calendar_path = Path(calendar_path).expanduser().resolve() if calendar_path else None
        self.warmup_start = pd.Timestamp(cfg.get("warmup_start", "2015-01-05")).normalize()
        self.splits = {name: dict(value) for name, value in cfg.get("splits", DEFAULT_SPLITS).items()}
        if set(self.splits) != set(DEFAULT_SPLITS):
            raise ValueError("splits must contain development, confirmation and final")
        previous_end = None
        for name in DEFAULT_SPLITS:
            bounds = self.splits[name]
            start, end = pd.Timestamp(bounds["start"]).normalize(), pd.Timestamp(bounds["end"]).normalize()
            if start > end or (previous_end is not None and start <= previous_end):
                raise ValueError("chronological splits must be nonempty and disjoint")
            self.splits[name] = {"start": str(start.date()), "end": str(end.date())}
            previous_end = end
        if self.warmup_start > pd.Timestamp(self.splits["development"]["start"]):
            raise ValueError("warmup_start must precede development")
        # Legacy integration switch only; formal release requires its own
        # controller custody and source admission, never this config value.
        self.allow_final = allow_final
        self.min_history_days = int(cfg.get("min_history_days", 120))
        self.min_average_amount = float(cfg.get("min_average_amount", 20_000_000.0))
        if self.min_history_days < 1 or self.min_average_amount < 0 or not math.isfinite(self.min_average_amount):
            raise ValueError("invalid fixed data eligibility thresholds")

        membership_bytes = self.membership_path.read_bytes()
        membership = pd.read_csv(self.membership_path, sep="\t", header=None,
                                 names=["code", "start_date", "end_date"], dtype=str)
        membership["code"] = membership["code"].map(lambda x: normalize_stock_code(x) if str(x)[:2].lower() != "bj" else str(x).lower())
        membership["start_date"] = pd.to_datetime(membership["start_date"], errors="raise").dt.normalize()
        membership["end_date"] = pd.to_datetime(membership["end_date"], errors="raise").dt.normalize()
        if (membership.start_date > membership.end_date).any():
            raise ValueError("membership contains reversed intervals")
        membership = membership.loc[
            membership.code.str.match(MAIN_BOARD)
            & (membership.end_date >= self.warmup_start)
            & (membership.start_date <= pd.Timestamp(self.splits["final"]["end"]))
        ].copy()
        if membership.empty:
            raise ValueError("no historical main-board CSI500 membership intervals")
        self.membership = membership.sort_values(["code", "start_date", "end_date"]).reset_index(drop=True)
        self.codes = sorted(self.membership.code.unique())
        self.files = {code: self.data_root / f"{code}.parquet" for code in self.codes}
        missing = [code for code, path in self.files.items() if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"historical membership data files missing: {missing[:20]}")
        self._inventory = {code: self._file_identity(path) for code, path in self.files.items()}
        self._membership_hash = hashlib.sha256(membership_bytes).hexdigest()
        self._calendar_hash = None
        self._calendar = None
        if self.calendar_path is not None:
            data = self.calendar_path.read_bytes()
            self._calendar_hash = hashlib.sha256(data).hexdigest()
            dates = pd.to_datetime(data.decode("utf-8-sig").splitlines(), errors="raise")
            self._calendar = pd.DatetimeIndex(dates).normalize().unique().sort_values()
        self._snapshot_id = _hash({"inventory": self._inventory, "membership": self._membership_hash,
                                  "calendar": self._calendar_hash, "splits": self.splits,
                                  "warmup_start": str(self.warmup_start.date())})
        self._loaded: dict[str, dict[str, Any]] = {}
        self._evaluations: dict[tuple[str, str], dict[str, Any]] = {}
        self._evaluation_raw: dict[tuple[str, str], dict[str, Any]] = {}
        self.confirmation_access_count = 0

    @staticmethod
    def _file_identity(path: Path) -> dict[str, int]:
        stat = path.stat()
        return {"size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}

    def manifest(self) -> dict[str, Any]:
        return {
            "case_id": self.task_definition["case_id"],
            "task_key": self.task_name,
            "mechanism_family": self.task_definition["family"],
            "evaluation_family_id": CASE_ID,
            "shared_market_exposure": "All tasks share market periods; changing the task does not renew a holdout.",
            "kind": "real_ashare_daily",
            "scope": "integration_only",
            "promotion_enabled": False,
            "task": self.task_definition["task"],
            "initial_strategy": self.initial_strategy(),
            "task_definition_hash": _hash({"task": self.task_definition["task"], "initial_strategy": self.initial_strategy()}),
            "universe": "historical CSI500 intervals, Shanghai/Shenzhen main board, decision and execution dates checked",
            "asset_file_count": len(self.codes),
            "warmup_start": str(self.warmup_start.date()),
            "splits": self.splits,
            "final_sealed": not self.allow_final,
            "data_columns": list(FEATURE_COLUMNS),
            "research_price_scale": "original adjusted OHLC / first available warmup qfq_ratio per stock; fixed historical anchor cancels future uniform front-adjustment rescaling",
            "execution_fields": list(EXECUTION_COLUMNS),
            "data_root": str(self.data_root),
            "membership_path": str(self.membership_path),
            "datahash": self._snapshot_id,
            "snapshot_hash_method": "file size/mtime inventory + membership/calendar content SHA256; projected market content SHA256 added on each time-bounded load",
            "membership_sha256": self._membership_hash,
            "calendar_sha256": self._calendar_hash,
            "eligibility": {"min_history_days": self.min_history_days,
                            "mean_amount_last_20_sessions_min": self.min_average_amount,
                            "exclude_current_st_and_delisting": True},
            "execution": {**EXECUTION_SPEC,
                          "max_stock_target_weight": MAX_STOCK_WEIGHT,
                          "decision": "completed close t; next market-session open t+1",
                          "positions": "long only, raw 100-share buy lots, no same-session round trip, cash earns zero",
                          "rebalance": "at most top_n eligible finite scores; each receives min(gross_exposure/top_n, 10%); unfilled slots remain cash; stable code-order ties",
                          "terminal": "predeclared zero target at penultimate close; last-session open liquidation attempted, blocked inventory remains marked"},
            "scoring": "annualized daily excess-return Sharpe after engine fees/slippage, including all flat cash days",
            "capacity_verified": False,
            "limitations": list(LIMITATIONS),
        }

    def _load(self, split: str) -> dict[str, Any]:
        if split not in self.splits:
            raise ValueError("unknown split")
        if split == "final" and not self.allow_final:
            raise PermissionError("final split is sealed; this integration run does not load final outcomes")
        if split in self._loaded:
            return self._loaded[split]
        start = pd.Timestamp(self.splits[split]["start"])
        end = pd.Timestamp(self.splits[split]["end"])
        frames = []
        content_hasher = hashlib.sha256()
        for code, path in self.files.items():
            if self._file_identity(path) != self._inventory[code]:
                raise RuntimeError(f"source snapshot changed before loading: {code}")
            # Predicate pushdown confines decoded returns to the requested cutoff.
            table = pq.read_table(path, columns=list(REQUIRED_COLUMNS),
                                  filters=[("date", ">=", self.warmup_start.to_pydatetime()),
                                           ("date", "<=", end.to_pydatetime())])
            frame = table.to_pandas()
            if self._file_identity(path) != self._inventory[code]:
                raise RuntimeError(f"source snapshot changed during loading: {code}")
            if frame.empty:
                continue
            frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
            frame["code"] = frame["code"].map(normalize_stock_code)
            if not frame.code.eq(code).all() or frame.date.duplicated().any():
                raise ValueError(f"data identity or duplicate-date failure: {code}")
            frame = frame.sort_values("date").reset_index(drop=True)
            for column in ("is_st", "is_delisting"):
                if frame[column].isna().any() or not frame[column].isin([0, 1, False, True]).all():
                    raise ValueError(f"missing/invalid execution status: {code}:{column}")
                frame[column] = frame[column].astype(bool)
            for column in ("open", "high", "low", "close", "qfq_ratio", "raw_open", "raw_prev_close"):
                values = pd.to_numeric(frame[column], errors="coerce")
                if not np.isfinite(values).all() or not values.gt(0).all():
                    raise ValueError(f"missing/invalid price or adjustment data: {code}:{column}")
            content_hasher.update(code.encode("ascii"))
            content_hasher.update(pd.util.hash_pandas_object(frame, index=False).values.tobytes())
            # A future dividend can uniformly rescale all front-adjusted history.
            # Cancel that scale in research features, while retaining untouched
            # front-adjusted prices and ratios for the execution engine.
            anchor_ratio = float(frame.qfq_ratio.iloc[0])
            for column in ("open", "high", "low", "close"):
                frame[f"_research_{column}"] = frame[column] / anchor_ratio
            frames.append(frame)
        if not frames:
            raise ValueError(f"no market rows through {end.date()}")
        market = pd.concat(frames, ignore_index=True)
        dates = (self._calendar[(self._calendar >= self.warmup_start) & (self._calendar <= end)]
                 if self._calendar is not None else pd.DatetimeIndex(sorted(market.date.unique())))
        fields = {column: market.pivot(index="date", columns="code", values=(f"_research_{column}" if column in {"open", "high", "low", "close"} else column))
                  .reindex(index=dates, columns=self.codes).astype(float) for column in FEATURE_COLUMNS}
        st = market.pivot(index="date", columns="code", values="is_st").reindex(index=dates, columns=self.codes)
        delisting = market.pivot(index="date", columns="code", values="is_delisting").reindex(index=dates, columns=self.codes)
        member = pd.DataFrame(False, index=dates, columns=self.codes)
        for row in self.membership.itertuples(index=False):
            member.loc[(dates >= row.start_date) & (dates <= row.end_date), row.code] = True
        valid = fields["close"].notna() & fields["volume"].gt(0)
        eligible = (member & st.eq(False) & delisting.eq(False) & valid
                    & (valid.cumsum() >= self.min_history_days)
                    & (fields["amount"].rolling(20, min_periods=20).mean() >= self.min_average_amount))
        evaluation_market = market.loc[(market.date >= start) & (market.date <= end), list(REQUIRED_COLUMNS)].copy()
        evaluation_market = evaluation_market.rename(columns={"code": "raw_code", "date": "trade_date"})
        market_dates = dates[(dates >= start) & (dates <= end)]
        if len(market_dates) < 3:
            raise ValueError("split must contain at least three market sessions")
        bundle = {"start": start, "end": end, "calendar_codes": tuple(self.codes),
                  "raw_codes": tuple(self.codes), "market_dates": market_dates,
                  "bars_by_date": {pd.Timestamp(day): frame.set_index("raw_code", drop=False)
                                   for day, frame in evaluation_market.groupby("trade_date", sort=True)}}
        loaded = {"fields": fields, "eligible": eligible, "bundle": bundle,
                  "row_count": len(market), "market_dates": market_dates,
                  "loaded_through": str(end.date()),
                  "content_sha256": content_hasher.hexdigest(),
                  "datahash": _hash({"snapshot": self._snapshot_id, "through": str(end.date()),
                                     "projected_content": content_hasher.hexdigest()})}
        self._loaded[split] = loaded
        return loaded

    def _weights(self, strategy: dict[str, Any], loaded: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
        fields = loaded["fields"]
        scores = evaluate_expression(strategy["score_expression"], fields, rank_universe=loaded["eligible"])
        filters = evaluate_expression(strategy["filter_expression"], fields, rank_universe=loaded["eligible"])
        eligible = loaded["eligible"] & np.isfinite(scores) & np.isfinite(filters) & filters.ne(0)
        scores = scores.where(eligible)
        dates = loaded["market_dates"]
        schedule = dates[::strategy["rebalance_days"]]
        weights = pd.DataFrame(0.0, index=schedule, columns=self.codes)
        for day in schedule:
            # Stable code ordering provides a frozen tie-breaker, never return-based.
            selected = scores.loc[day].dropna().sort_values(ascending=False, kind="mergesort").head(strategy["top_n"]).index
            if len(selected):
                weights.loc[day, selected] = min(MAX_STOCK_WEIGHT, strategy["gross_exposure"] / strategy["top_n"])
        # Split end is a declared accounting boundary, known before research.
        weights = weights.loc[weights.index < dates[-2]]
        weights.loc[dates[-2]] = 0.0
        return weights.sort_index(), eligible

    @staticmethod
    def _year_metrics(daily: pd.DataFrame) -> list[dict[str, Any]]:
        result = []
        risk_free_daily = (1 + EXECUTION_SPEC["risk_free_rate"]) ** (1 / 252) - 1
        for year, group in daily.groupby(pd.to_datetime(daily.date).dt.year, sort=True):
            returns = group["return"].astype(float)
            std = returns.std(ddof=1)
            equity = np.r_[1.0, (1 + returns).cumprod().to_numpy()]
            drawdown = equity / np.maximum.accumulate(equity) - 1
            result.append({"year": int(year), "days": len(group),
                           "total_return": float(equity[-1] - 1),
                           "sharpe_ratio": float((returns.mean() - risk_free_daily) / std * math.sqrt(252)) if std > 0 else 0.0,
                           "max_drawdown": float(drawdown.min()),
                           "trade_count": int(group.trade_count.sum()),
                           "commission": float(group.commission.sum())})
        return result

    def initial_strategy(self) -> dict[str, Any]:
        # Preserve the exact legacy expression for first-case replay.
        return baseline_strategy() if self.task_name == "price_repair" else validate_strategy(self.task_definition["baseline"])

    def evaluate(self, strategy: dict[str, Any], split: str = "development") -> dict[str, Any]:
        clean = validate_strategy(strategy)
        if split == "confirmation":
            self.confirmation_access_count += 1
        loaded = self._load(split)
        key = (split, _hash(clean))
        if key in self._evaluations:
            return json.loads(json.dumps(self._evaluations[key]))
        weights, eligible = self._weights(clean, loaded)
        result = run_target_weight_backtest(
            weights, parquet_path=str(self.data_root / "*.parquet"),
            start=self.splits[split]["start"], end=self.splits[split]["end"],
            prepared_market=loaded["bundle"], membership_df=self.membership,
            **EXECUTION_SPEC,
        )
        result["_target_weights"] = weights
        daily = result["_daily_df"]
        trades = result["_trades_df"]
        metric_keys = ("capital", "end_balance", "total_return", "annual_return", "annual_volatility",
                       "sharpe_ratio", "max_drawdown", "max_ddpercent", "total_trade_count",
                       "total_commission", "total_slippage", "win_rate", "profit_loss_ratio")
        metrics = {name: (_finite(result[name]) if result.get(name) is not None else None) for name in metric_keys}
        completed = int(trades.realized_pnl.notna().sum()) if "realized_pnl" in trades else 0
        active_days = int(daily.position_value.gt(0).sum())
        metrics.update({"days": len(daily), "active_days": active_days, "completed_exit_orders": completed,
                        "average_gross_exposure": float((daily.position_value / daily.balance).mean()),
                        "terminal_position_value": float(daily.position_value.iloc[-1]),
                        "rebalancing_decisions": len(weights),
                        "eligible_signal_observations": int(eligible.reindex(loaded["market_dates"]).sum().sum())})
        worst_exit_orders = []
        if completed:
            for row in trades.loc[trades.realized_pnl.notna()].nsmallest(20, "realized_pnl").itertuples(index=False):
                worst_exit_orders.append({"date": str(pd.Timestamp(row.date).date()), "code": row.code,
                                          "realized_pnl": float(row.realized_pnl), "turnover": float(row.turnover),
                                          "commission": float(row.commission)})
        output = {
            "case_id": self.task_definition["case_id"], "split": split, "scope": "integration_only",
            "strategy": clean, "strategy_hash": _hash(clean),
            "score": metrics["sharpe_ratio"], "metrics": metrics,
            "eligible": bool(len(daily) >= 100 and completed >= 30 and active_days >= 60),
            "eligibility_meaning": "minimum activity for research comparison; not statistical proof or promotion approval",
            "yearly": self._year_metrics(daily),
            "worst_exit_orders": worst_exit_orders,
            "worst_exit_orders_meaning": "at most 20 realized sell orders, which may be partial rebalances rather than complete round trips",
            "datahash": loaded["datahash"], "snapshot_id": self._snapshot_id,
            "loaded_through": loaded["loaded_through"],
            "promotion_enabled": False, "capacity_verified": False,
            "execution_valid": False,
            "execution_invalid_reasons": ["Adjusted-price approximation lacks a separate real-share inventory and corporate-action cash ledger", "Auction capacity and independent suspension/delisting treatment have not been validated"],
            "confirmation_reusable_for_selection": False,
            "limitations": list(LIMITATIONS),
            "execution_debug": result.get("_backtest_debug", {}),
        }
        self._evaluations[key] = output
        self._evaluation_raw[key] = result
        return json.loads(json.dumps(output, allow_nan=False))

    def development_packet(self) -> dict[str, Any]:
        loaded = self._load("development")
        fields, dates = loaded["fields"], loaded["market_dates"]
        active = loaded["eligible"].reindex(dates).sum(axis=1)
        return {
            "case_id": self.task_definition["case_id"],
            "task": self.manifest()["task"],
            "research_objective": {"target_net_sharpe": 1.0,
                "meaning": "Aim for net portfolio Sharpe above 1 with broad development-period support; a high development score alone is not success, and absence of alpha is a valid outcome.",
                "formal_success_requires": "Frozen out-of-sample evaluation, verified execution, independent research repeats and cross-task evidence; currently not established"},
            "scope": "integration_only; research observations restricted to development",
            "development": self.splits["development"],
            "warmup_start": str(self.warmup_start.date()),
            "datahash": loaded["datahash"], "loaded_through": loaded["loaded_through"],
            "rows_including_warmup": loaded["row_count"],
            "market_sessions": len(dates), "stock_file_count": len(self.codes),
            "eligible_stocks_per_day": {"minimum": int(active.min()), "median": float(active.median()), "maximum": int(active.max())},
            "field_descriptions": {"open/high/low/close": "completed daily OHLC on each stock's fixed first-warmup-session raw-price anchor; uniform future front-adjustment rescaling is cancelled; these are not current raw prices",
                                   "volume": "unadjusted shares according to conversion code; independent unit audit pending",
                                   "amount": "CNY daily turnover according to conversion code",
                                   "float_market_cap/total_market_cap": "daily raw-price market capitalization, CNY"},
            "factor_expression_guide": expression_guide(),
            "strategy_schema": strategy_schema(),
            "baseline_strategy": self.initial_strategy(),
            "fixed_execution": dict(EXECUTION_SPEC),
            "fixed_allocation": {"max_stock_target_weight": MAX_STOCK_WEIGHT,
                                 "weight_each_selected_stock": "min(gross_exposure / top_n, 0.10)",
                                 "unused_slots": "remain cash; do not redistribute among fewer signals"},
            "research_tools": {"evaluate": "Evaluate a strategy on development only, returning all-day portfolio costs, risk and annual breakdowns",
                               "diagnose": "Group candidate signals by a causal expression; inspect gross next-open horizon returns, groups by year, and paired high-minus-low same-date spreads. These are descriptive diagnostics, not executable portfolio returns or independent statistical samples."},
            "research_prompt": "不用追求指定均线。考虑价格修复、量价关系、波动和市场环境等竞争解释；在实验前说明预测与证伪条件。失败实验也计入证据，允许保留基线或认定证据不足。",
            "limitations": list(LIMITATIONS),
        }

    def diagnose(self, strategy: dict[str, Any], expression: str, horizon: int = 5) -> dict[str, Any]:
        clean = validate_strategy(strategy)
        validate_expression(expression, set(FEATURE_COLUMNS))
        if isinstance(horizon, bool) or not isinstance(horizon, int) or not 1 <= horizon <= 20:
            raise ValueError("horizon must be an integer in [1, 20]")
        loaded = self._load("development")
        feature = evaluate_expression(expression, loaded["fields"], rank_universe=loaded["eligible"])
        _, eligible = self._weights(clean, loaded)
        opening = loaded["fields"]["open"]
        # Decision t -> buy open t+1 -> hypothetical exit open t+h+1.
        # Positive shift arguments are only used here in the trusted evaluator.
        outcome = opening.shift(-(horizon + 1)) / opening.shift(-1) - 1
        dates = loaded["market_dates"]
        mask = eligible & np.isfinite(feature) & np.isfinite(outcome)
        mask.loc[~mask.index.isin(dates)] = False
        x = feature.where(mask).stack()
        y = outcome.where(mask).stack()
        observations = pd.DataFrame({"feature": x, "forward_return": y}).dropna()
        groups = []
        if not observations.empty:
            # Fixed global quintiles are diagnostic summaries, never signal inputs.
            boundaries = observations.feature.quantile([0.2, 0.4, 0.6, 0.8]).to_numpy()
            observations["group"] = np.searchsorted(boundaries, observations.feature, side="left") + 1
            for group, values in observations.groupby("group", sort=True):
                groups.append({"group": int(group), "observations": len(values),
                               "feature_min": float(values.feature.min()), "feature_max": float(values.feature.max()),
                               "mean_gross_forward_return": float(values.forward_return.mean()),
                               "median_gross_forward_return": float(values.forward_return.median()),
                               "positive_fraction": float(values.forward_return.gt(0).mean()),
                               "distinct_signal_dates": int(values.index.get_level_values(0).nunique())})
        yearly = []
        groups_by_year = []
        paired_daily_spread = {"high_group": 5, "low_group": 1, "paired_signal_dates": 0,
                               "mean_gross_forward_spread": None, "positive_fraction": None}
        if not observations.empty:
            for year, values in observations.groupby(observations.index.get_level_values(0).year, sort=True):
                yearly.append({"year": int(year), "observations": len(values),
                               "mean_gross_forward_return": float(values.forward_return.mean())})
                for group, grouped in values.groupby("group", sort=True):
                    date_means = grouped.forward_return.groupby(level=0).mean()
                    groups_by_year.append({"year": int(year), "group": int(group),
                        "observations": len(grouped), "distinct_signal_dates": len(date_means),
                        "mean_gross_forward_return": float(grouped.forward_return.mean()),
                        "equal_date_mean_gross_forward_return": float(date_means.mean()),
                        "median_gross_forward_return": float(grouped.forward_return.median()),
                        "positive_fraction": float(grouped.forward_return.gt(0).mean())})
            daily_groups = observations.groupby([observations.index.get_level_values(0), "group"]).forward_return.mean().unstack("group")
            if 1 in daily_groups and 5 in daily_groups:
                spread = (daily_groups[5] - daily_groups[1]).dropna()
                if len(spread):
                    paired_daily_spread.update(paired_signal_dates=len(spread),
                        mean_gross_forward_spread=float(spread.mean()), positive_fraction=float(spread.gt(0).mean()))
        return {"case_id": self.task_definition["case_id"], "split": "development", "expression": expression,
                "horizon_sessions": horizon, "observations": len(observations), "groups": groups,
                "yearly": yearly, "groups_by_year": groups_by_year,
                "paired_daily_spread": paired_daily_spread,
                "group_boundary_method": "Pooled development quintiles for descriptive diagnostics only; never tradable thresholds or an out-of-sample estimate",
                "datahash": loaded["datahash"],
                "last_allowed_outcome_date": loaded["loaded_through"],
                "limitations": ["Gross event-level diagnostic, not cost-adjusted executable portfolio Sharpe",
                                "Overlapping events are dependent; group comparisons are descriptive, not independent t-tests",
                                "Same-date high-minus-low spreads reduce calendar composition differences, but do not remove stock/style confounding or overlap dependence",
                                "Future diagnostic returns are computed only by the trusted evaluator, never exposed as factor fields",
                                "Signals near the cutoff with an unavailable full horizon are excluded; no next-split prices are loaded"]}
