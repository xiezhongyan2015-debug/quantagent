"""Causal, raw-value cross-sectional OLS and the declared HF0280 composition.

Formula authority: experiments/factor_calendar_daily_hf0280_research_sharpe15.yaml
line 7 and factor_calendar_daily_hf0280_top30_research_sharpe15.yaml line 9.
The seven minute-derived inputs are supplied observations, never reconstructed
here from prices. Their publication timing and the membership mask must be
admitted by the caller. This module reads no files and executes no research,
portfolio, model, or time-series regression.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd


VERSION = "v6_daily_raw_cross_sectional_ols_hf0280_v1"
HF0280_FIELDS = ("gu_1m", "gd_1m", "rbar_up17", "rbar_down17",
                 "r_0931_1000", "r_1001_1030", "overnight_return")
HF0280_MIN_STOCKS = 50
HF0280_WINDOW = 20
_INTERCEPT = "intercept"


@dataclass
class CrossSectionalOLS:
    residuals: pd.DataFrame
    coefficients: pd.DataFrame
    diagnostics: pd.DataFrame


@dataclass
class HF0280Result:
    scores: pd.DataFrame
    daily_residual: pd.DataFrame
    regressions: dict[str, CrossSectionalOLS]
    diagnostics: pd.DataFrame
    semantics: dict


def _dates(index, name):
    if (not isinstance(index, pd.DatetimeIndex) or index.empty or index.hasnans
            or index.tz is not None or not index.is_unique or not index.is_monotonic_increasing
            or not index.equals(index.normalize())):
        raise ValueError(name + " requires unique increasing timezone-naive daily dates")


def _scope(eligible, calendar):
    _dates(calendar, "calendar")
    if (not isinstance(eligible, pd.DataFrame) or not eligible.index.equals(calendar)
            or eligible.empty or not eligible.columns.is_unique
            or any(not isinstance(name, str) or not name for name in eligible.columns)
            or not eligible.isin([True, False, 0, 1]).all(axis=None)):
        raise ValueError("eligible must be a complete boolean matrix on the explicit calendar")
    return eligible.astype(bool).copy(deep=True)


def _values(frame, eligible, calendar, name):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(name + " must be a numeric date-by-stock DataFrame")
    _dates(frame.index, name)
    if (not frame.index.isin(calendar).all() or not frame.columns.equals(eligible.columns)
            or any(not pd.api.types.is_numeric_dtype(t) or pd.api.types.is_complex_dtype(t)
                   or pd.api.types.is_bool_dtype(t) for t in frame.dtypes)):
        raise ValueError(name + " must have real numeric values and exactly the fixed stock columns")
    # Reinsert omitted common sessions, never roll over a stock's observed rows.
    result = frame.reindex(calendar).astype(float).copy(deep=True)
    return result.where(np.isfinite(result))


def cross_sectional_ols(dependent, controls, *, eligible, calendar, min_stocks=50):
    """Fit y[D] = intercept + sum(beta[k,D] * x[k,D]) across stocks only.

    Every date uses its own eligible, finite complete cases for y and all named
    controls. No ranking, weighting, imputation, previous coefficient carry, or
    cross-date fitting occurs. Excluded stocks remain NaN, including predictions.
    Require max(min_stocks, number_of_coefficients + 1) samples, so positive
    residual degrees of freedom remain even for a smaller generic minimum.

    A singular design, solver failure or nonfinite output leaves the *whole*
    date's residuals and coefficients NaN with an explicit status. Numerical
    rank uses NumPy SVD least squares with rcond = eps * max(n, p), explicitly
    frozen here rather than an implicit library default. Zero residuals from a
    valid full-rank regression remain zero; they are not manufactured failures.

    `calendar` is the complete caller-supplied session list. Missing frame dates
    are inserted as NaN; dates outside it and reordered/missing stock axes fail.
    A wrong externally supplied calendar or future-derived field cannot be
    certified by a numeric operator. Callers own point-in-time source admission.
    """
    mask = _scope(eligible, calendar)
    if not isinstance(controls, Mapping):
        raise TypeError("controls must be a mapping of names to numeric matrices")
    if any(not isinstance(name, str) or not name or name == _INTERCEPT for name in controls):
        raise ValueError("controls require nonempty names distinct from intercept")
    if type(min_stocks) is not int or min_stocks < 1:
        raise ValueError("min_stocks must be a positive integer")
    y = _values(dependent, mask, calendar, "dependent").to_numpy()
    names = list(controls)
    xs = [_values(controls[name], mask, calendar, name).to_numpy() for name in names]
    allowed = mask.to_numpy()
    p = len(names) + 1
    minimum = max(min_stocks, p + 1)
    residuals = np.full(y.shape, np.nan)
    coefficients = np.full((len(calendar), p), np.nan)
    observations = []
    for i in range(len(calendar)):
        complete = allowed[i] & np.isfinite(y[i])
        for x in xs:
            complete &= np.isfinite(x[i])
        n = int(complete.sum())
        observation = {"eligible_stocks": int(allowed[i].sum()), "complete_stocks": n,
                       "required_stocks": minimum, "parameters": p, "rank": None,
                       "residual_degrees_of_freedom": None, "rcond": None,
                       "status": "insufficient_samples"}
        observations.append(observation)
        if n < minimum:
            continue
        design = np.column_stack([np.ones(n)] + [x[i, complete] for x in xs])
        rcond = np.finfo(float).eps * max(design.shape)
        observation["rcond"] = rcond
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                beta, _, rank, singular = np.linalg.lstsq(design, y[i, complete], rcond=rcond)
                observation["rank"] = int(rank)
                observation["residual_degrees_of_freedom"] = n - int(rank)
                if rank < p:
                    observation["status"] = "rank_deficient"
                    continue
                error = y[i, complete] - design @ beta
                if not (np.isfinite(beta).all() and np.isfinite(error).all() and np.isfinite(singular).all()):
                    observation["status"] = "nonfinite_solution"
                    continue
        except np.linalg.LinAlgError:
            observation["status"] = "solver_failed"
            continue
        except FloatingPointError:
            observation["status"] = "nonfinite_solution"
            continue
        coefficients[i] = beta
        residuals[i, complete] = error
        observation["status"] = "ok"
    return CrossSectionalOLS(
        pd.DataFrame(residuals, index=calendar, columns=mask.columns),
        pd.DataFrame(coefficients, index=calendar, columns=[_INTERCEPT, *names]),
        pd.DataFrame(observations, index=calendar),
    )


def compute_hf0280(fields, *, eligible, calendar):
    """Apply the original fixed three-regression / complete trailing-20 formula.

    up = residual(gu_1m ~ 1 + rbar_up17 + early_return + late_return + overnight)
    down = residual(gd_1m ~ 1 + rbar_down17 + same three return controls)
    final = residual(down ~ 1 + up)
    score[D, stock] = arithmetic mean(final[D-19:D, stock]), all 20 required.

    Each regression independently requires 50 eligible complete stocks; the
    third uses the intersection of available up/down residuals. This follows
    the declaration's "each regression" minimum, rather than silently imposing
    a seven-field joint sample on the two initial regressions. Any failed step
    makes the final daily cross section all NaN. Missing dates, missing inputs,
    and nonmembership interrupt that stock's 20-session window. Direction is
    high-value-preferred, without ranking, reversal, TEMA/Amihud blending, or
    trading. The seven supplied primitives are not inferred from minute bars.
    """
    if not isinstance(fields, Mapping) or not set(HF0280_FIELDS) <= set(fields):
        raise ValueError("all seven declared HF0280 fields are required")
    common = {name: fields[name] for name in ("r_0931_1000", "r_1001_1030", "overnight_return")}
    kwargs = {"eligible": eligible, "calendar": calendar, "min_stocks": HF0280_MIN_STOCKS}
    up = cross_sectional_ols(fields["gu_1m"], {"rbar_up17": fields["rbar_up17"], **common}, **kwargs)
    down = cross_sectional_ols(fields["gd_1m"], {"rbar_down17": fields["rbar_down17"], **common}, **kwargs)
    final = cross_sectional_ols(down.residuals, {"epsilon_up": up.residuals}, **kwargs)
    successful = up.diagnostics.status.eq("ok") & down.diagnostics.status.eq("ok") & final.diagnostics.status.eq("ok")
    daily = final.residuals.where(successful, axis=0)
    scores = daily.rolling(HF0280_WINDOW, min_periods=HF0280_WINDOW).mean()
    diagnostics = pd.DataFrame({"up_status": up.diagnostics.status, "down_status": down.diagnostics.status,
        "final_status": final.diagnostics.status, "all_regressions_ok": successful,
        "daily_valid_stocks": daily.notna().sum(axis=1), "rolling_valid_stocks": scores.notna().sum(axis=1)}, index=calendar)
    semantics = {"version": VERSION, "fields": list(HF0280_FIELDS), "minimum_stocks_per_regression": 50,
        "intercept": True, "controls_ranked": False, "rolling_window_sessions": 20,
        "rolling_min_periods": 20, "rolling_includes_current_completed_day": True,
        "regression_samples": "each equation complete cases; third equation residual intersection",
        "direction": "higher_preferred", "input_publication_timing_verified_by_operator": False,
        "financial_success": False, "source_formula": "factor_calendar_daily_hf0280_research_sharpe15.yaml:7"}
    return HF0280Result(scores, daily, {"up": up, "down": down, "final": final}, diagnostics, semantics)
