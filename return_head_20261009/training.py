"""Stage A: a frozen PlanGRU encoder and a 129-parameter arithmetic-return head.

There is one fixed ridge specification, no validation-based parameter search and
no shared-parameter update. A batch is one *complete visible cross section*, not
an arbitrary minibatch. Future labels qualify fitting pairs only. The serving
mapping is Linear(128, 1) in original h coordinates; no stock rank/z-score is
applied to its outputs.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np

HIDDEN_SIZE = 128
RIDGE_MULTIPLIER = 1e-3
NS_DAY = 86_400_000_000_000
CHINA_OFFSET = 8 * 3_600_000_000_000
TARGET_UNIT = "arithmetic_label3"


def day_number(date: str) -> int:
    return int(np.datetime64(date, "D").astype(np.int64))


@dataclass(frozen=True)
class TrainingWindow:
    start_day: int
    end_day: int
    cutoff_day: int
    cutoff_ns: int
    embargo_trading_days: int

    def as_dict(self) -> dict:
        return dict(start=str(np.datetime64(self.start_day, "D")),
                    end_exclusive=str(np.datetime64(self.end_day, "D")),
                    label_cutoff_local_day=str(np.datetime64(self.cutoff_day, "D")),
                    label_cutoff_ns=self.cutoff_ns,
                    embargo_trading_days=self.embargo_trading_days,
                    label_end_strictly_before_cutoff=True)


def training_window(trading_days, *, start="2019-01-01", end="2024-01-01",
                    embargo_trading_days=2) -> TrainingWindow:
    """Match the old two-trading-day split embargo using the actual calendar.

    The last two sessions before end are excluded. Both signal day and raw y3
    exit right boundary must precede midnight on the first excluded session.
    Integer calendars must use epoch-day units (PanelDataset.trading_days).
    """
    calendar = np.asarray(trading_days)
    if calendar.ndim != 1 or calendar.size == 0:
        raise ValueError("Nonempty one-dimensional actual trading calendar required")
    if calendar.dtype.kind in "iu":
        calendar = calendar.astype(np.int64)
        if np.any(np.abs(calendar) > 1_000_000):
            raise ValueError("Integer trading calendar must use epoch-day units, not ns")
    else:
        calendar = calendar.astype("datetime64[D]").astype(np.int64)
    calendar = np.unique(calendar)
    start_day, end_day = day_number(start), day_number(end)
    if not 1 <= int(embargo_trading_days) == embargo_trading_days or end_day <= start_day:
        raise ValueError("Positive whole-session embargo and ordered interval required")
    preceding = calendar[calendar < end_day]
    if preceding.size < embargo_trading_days:
        raise ValueError("Insufficient actual trading calendar for embargo")
    cutoff = int(preceding[-int(embargo_trading_days)])
    if cutoff <= start_day:
        raise ValueError("Embargo leaves no possible training interval")
    return TrainingWindow(start_day, end_day, cutoff,
                          cutoff * NS_DAY - CHINA_OFFSET, int(embargo_trading_days))


def _vector(value, n, name, dtype):
    a = np.asarray(value)
    if a.ndim == 0:
        a = np.full(n, a.item())
    if a.shape != (n,):
        raise ValueError(name + " must be scalar or one value per visible stock")
    if dtype == np.int64 and a.dtype.kind not in "iu":
        raise ValueError(name + " must use integer UTC nanoseconds")
    if dtype == np.bool_ and a.dtype != np.bool_:
        raise ValueError(name + " must be explicitly Boolean")
    return a.astype(dtype, copy=False)


def _section(batch: Mapping, window: TrainingWindow):
    """Validate one full visible section, then form a separate fitting mask."""
    h = np.asarray(batch["h"], dtype=np.float64)
    if h.ndim != 2 or h.shape[1] != HIDDEN_SIZE:
        raise ValueError("One complete h[n,128] visible cross section required")
    n = h.shape[0]
    visible = _vector(batch.get("visible_mask", np.ones(n, dtype=np.bool_)),
                      n, "visible_mask", np.bool_)
    if not np.isfinite(h[visible]).all():
        raise FloatingPointError("Visible frozen encoder returned nonfinite h")
    time_value = np.asarray(batch["signal_time_ns"])
    if time_value.ndim != 0 or time_value.dtype.kind not in "iu":
        raise ValueError("One integer signal UTC-ns timestamp per complete section required")
    signal = int(time_value)
    signal_day = (signal + CHINA_OFFSET) // NS_DAY
    in_train = window.start_day <= signal_day < window.cutoff_day
    raw_y3 = np.asarray(batch["raw_y3"], dtype=np.float64)
    if raw_y3.shape != (n,):
        raise ValueError("One raw log label3 per visible stock required")
    end = _vector(batch["label_end_time_ns"], n, "label_end_time_ns", np.int64)
    entry = _vector(batch["earliest_execution_time_ns"], n,
                    "earliest_execution_time_ns", np.int64)
    mature = (entry > signal) & (end > entry) & (end < window.cutoff_ns)
    # Transform each outcome before averaging: E[expm1(y)] != expm1(E[y]).
    with np.errstate(over="ignore", invalid="ignore"):
        target = np.expm1(raw_y3)
    finite = np.isfinite(raw_y3) & np.isfinite(target)
    fit = visible & mature & finite & in_train
    return h, target, fit, signal, dict(visible=int(visible.sum()),
        fitting_pairs=int(fit.sum()), excluded_outside_train=int(not in_train),
        excluded_visible_nonfinite_target=int((visible & ~finite).sum()),
        excluded_visible_nonmature=int((visible & ~mature).sum()))


@dataclass(frozen=True)
class FittedReturnHead:
    weight: np.ndarray
    bias: float
    metadata: dict

    def predict(self, h) -> np.ndarray:
        """Arithmetic expected gross return per original visible stock, no rank."""
        h = np.asarray(h, dtype=np.float64)
        if h.ndim != 2 or h.shape[1] != HIDDEN_SIZE:
            raise ValueError("h[n,128] required")
        if not np.isfinite(h).all():
            raise FloatingPointError("Cannot silently remove nonfinite visible predictions")
        result = h @ self.weight.astype(np.float64) + float(self.bias)
        if not np.isfinite(result).all():
            raise FloatingPointError("Nonfinite arithmetic return prediction")
        return result


def fit_ridge_from_batches(batches: Iterable[Mapping], *, window: TrainingWindow,
                          min_visible_stocks=100, min_fitting_stocks=100) -> FittedReturnHead:
    """One streaming pass; section-balanced sufficient statistics in FP64.

    Each retained timestamp has weight 1/K and each finite mature pair within
    that timestamp weight 1/n_t. h is centered/scaled using only those same
    train pairs. Solve (G + lambda I) beta = cov(z,y), intercept unpenalized,
    lambda = 1e-3 * trace(G)/128, G=E_w[z z']. Zero-variance dimensions get
    zero coefficients. This fixed formula is not selected using 2024 metrics.
    """
    if (window.start_day != day_number("2019-01-01")
            or window.end_day != day_number("2024-01-01")
            or window.embargo_trading_days != 2
            or not window.start_day < window.cutoff_day < window.end_day
            or window.cutoff_ns != window.cutoff_day * NS_DAY - CHINA_OFFSET):
        raise ValueError("Stage A fixes 2019--2023 training with two-session embargo; no later fitting")
    if min_visible_stocks < 1 or min_fitting_stocks < 1:
        raise ValueError("Positive minimum stock counts required")
    sx, sxx, sxy = np.zeros(HIDDEN_SIZE), np.zeros((HIDDEN_SIZE, HIDDEN_SIZE)), np.zeros(HIDDEN_SIZE)
    sy = syy = 0.0
    kept, pairs, visible_rows = 0, 0, 0
    seen = set()
    first_time = last_time = None
    excluded = dict(outside_train_sections=0, below_visible_minimum_sections=0,
                    below_fitting_minimum_sections=0, nonfinite_targets=0,
                    nonmature_targets=0)
    for batch in batches:
        h, y, mask, signal, info = _section(batch, window)
        if signal in seen:
            raise ValueError("Duplicate timestamp: a cross section must not be split into minibatches")
        seen.add(signal)
        excluded["nonfinite_targets"] += info["excluded_visible_nonfinite_target"]
        excluded["nonmature_targets"] += info["excluded_visible_nonmature"]
        if info["excluded_outside_train"]:
            excluded["outside_train_sections"] += 1
            continue
        if info["visible"] < min_visible_stocks:
            excluded["below_visible_minimum_sections"] += 1
            continue
        n = info["fitting_pairs"]
        if n < min_fitting_stocks:
            excluded["below_fitting_minimum_sections"] += 1
            continue
        x, target = h[mask], y[mask]
        sx += x.mean(axis=0)
        sxx += x.T @ x / n
        sxy += x.T @ target / n
        sy += float(target.mean())
        syy += float(target @ target / n)
        kept += 1
        pairs += n
        visible_rows += info["visible"]
        first_time = signal if first_time is None else min(first_time, signal)
        last_time = signal if last_time is None else max(last_time, signal)
    if kept == 0:
        raise ValueError("No eligible, finite, matured pre2024 training sections")
    mean, mean_y = sx / kept, sy / kept
    cov = sxx / kept - np.outer(mean, mean)
    cov = (cov + cov.T) / 2
    variance = np.diag(cov).copy()
    # Rounding at machine scale is harmless; a materially negative variance is
    # evidence of corrupted statistics, never replaced with an invented scale.
    tolerance = 1e-12 * max(1.0, float(np.max(np.abs(np.diag(sxx / kept)))))
    if np.min(variance) < -tolerance:
        raise FloatingPointError("Materially negative weighted feature variance")
    active = variance > tolerance
    std = np.ones(HIDDEN_SIZE)
    std[active] = np.sqrt(variance[active])
    gram = cov / np.outer(std, std)
    gram[~active, :] = 0
    gram[:, ~active] = 0
    cov_zy = (sxy / kept - mean * mean_y) / std
    cov_zy[~active] = 0
    lam = RIDGE_MULTIPLIER * float(np.trace(gram)) / HIDDEN_SIZE
    beta = np.zeros(HIDDEN_SIZE)
    if active.any():
        aa = np.ix_(active, active)
        beta[active] = np.linalg.solve(gram[aa] + lam * np.eye(int(active.sum())), cov_zy[active])
    raw_weight = beta / std
    raw_bias = mean_y - mean @ raw_weight
    fit_mse = syy / kept - mean_y ** 2 - 2 * beta @ cov_zy + beta @ gram @ beta
    if fit_mse < -1e-10:
        raise FloatingPointError("Negative training residual sum of squares")
    weight = raw_weight.astype(np.float32)
    bias = float(np.float32(raw_bias))
    if not np.isfinite(weight).all() or not np.isfinite(bias):
        raise FloatingPointError("Nonfinite FP32 serving weights")
    metadata = dict(schema_version=1, stage="A_frozen_encoder_only",
        target_unit=TARGET_UNIT, target_formula="expm1(raw log y3) per outcome",
        target_is_gross=True, fees_deducted=False, benchmark_deducted=False,
        prediction_transform="none; direct arithmetic-return units",
        hidden_size=HIDDEN_SIZE, added_trainable_parameters=129,
        shared_parameters_updated=False, rank_head_updated=False,
        model_mode="eval; frozen original dropout disabled",
        fitting_window=window.as_dict(), training_sections=kept,
        fitting_pairs=pairs, visible_rows_in_retained_sections=visible_rows,
        seen_sections=len(seen), first_fitted_signal_time_ns=first_time,
        last_fitted_signal_time_ns=last_time, excluded=excluded,
        min_visible_stocks=int(min_visible_stocks), min_fitting_stocks=int(min_fitting_stocks),
        sample_weight="equal retained timestamps; equal mature finite stocks within timestamp",
        normalization="weighted train-only feature mean/population standard deviation",
        feature_mean=mean.tolist(), feature_std=std.tolist(),
        constant_feature_dimensions=np.flatnonzero(~active).tolist(),
        ridge_multiplier=RIDGE_MULTIPLIER, ridge_lambda=lam,
        ridge_formula="lambda=1e-3*trace(E_w[z z'])/128; unpenalized intercept",
        weighted_train_mse=float(max(fit_mse, 0)),
        feature_statistics_dtype="float64", serving_weights_dtype="float32",
        validation_outcomes_used_for_fitting=False,
        validation2024_not_independent="Old encoder checkpoints were selected using 2024 IC",
        years2025_2026_not_independent="Already exposed research backtest years")
    return FittedReturnHead(weight, bias, metadata)


def save_return_head(path, fit: FittedReturnHead, *, lineage=None):
    """Small portable head checkpoint; no copied encoder or optimizer state."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = dict(fit.metadata)
    if lineage is not None:
        metadata["lineage"] = lineage
    payload = json.dumps(metadata, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf8")
    # A file handle avoids numpy silently changing a caller's extension.
    with path.open("wb") as stream:
        np.savez_compressed(stream, weight=np.asarray(fit.weight, dtype=np.float32),
            bias=np.asarray(fit.bias, dtype=np.float32),
            metadata_utf8=np.frombuffer(payload, dtype=np.uint8))
    return dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                bytes=path.stat().st_size, added_parameters=129)


def load_return_head(path) -> FittedReturnHead:
    with np.load(path, allow_pickle=False) as archive:
        if set(archive.files) != {"weight", "bias", "metadata_utf8"}:
            raise ValueError("Unexpected return-head checkpoint fields")
        weight = archive["weight"].copy()
        bias = archive["bias"].copy()
        metadata = json.loads(archive["metadata_utf8"].tobytes().decode("utf8"))
    if weight.shape != (HIDDEN_SIZE,) or weight.dtype != np.float32 or bias.shape != ():
        raise ValueError("Expected FP32 128-weight/one-bias serving head")
    if metadata["target_unit"] != TARGET_UNIT or metadata["shared_parameters_updated"] is not False:
        raise ValueError("Only stage-A arithmetic-return checkpoints are supported")
    if not np.isfinite(weight).all() or not np.isfinite(bias):
        raise ValueError("Nonfinite saved weights")
    return FittedReturnHead(weight, float(bias), metadata)


def install_return_head(adapter, fit: FittedReturnHead):
    """Install the folded ridge readout without touching backbone or rank head."""
    import torch
    if adapter.target_unit != TARGET_UNIT or adapter._shared_names or adapter.teacher is not None:
        raise ValueError("Stage A requires arithmetic targets and no shared finetuning")
    if any(p.requires_grad for p in adapter.rank_model.parameters()):
        raise ValueError("Original encoder and rank head must remain frozen")
    if tuple(adapter.return_head.weight.shape) != (1, HIDDEN_SIZE):
        raise ValueError("Exactly one Linear128->1 return head required")
    with torch.no_grad():
        adapter.return_head.weight.copy_(torch.as_tensor(fit.weight,
            device=adapter.return_head.weight.device).reshape(1, HIDDEN_SIZE))
        adapter.return_head.bias.copy_(torch.as_tensor([fit.bias],
            dtype=adapter.return_head.bias.dtype, device=adapter.return_head.bias.device))
    adapter.eval()
    return adapter


def extract_visible_features(adapter, x, industry, slots):
    """One frozen GRU pass on an already visible pool; no label argument exists.

    Returns CPU NumPy (h, old_rank_score). This intentionally does not filter
    rows using returns. The caller builds the pool with require_labels=False,
    80 complete inputs and current membership before constructing x.
    """
    import torch
    if adapter._shared_names or adapter.teacher is not None or any(p.requires_grad for p in adapter.rank_model.parameters()):
        raise ValueError("Frozen original shared parameters required")
    captured = []
    hook = adapter.rank_model.head.register_forward_pre_hook(
        lambda module, args: captured.append(args[0].detach()))
    try:
        adapter.eval()
        with torch.inference_mode():
            out = adapter(x, industry, slots)
    finally:
        hook.remove()
    if len(captured) != 1:
        raise RuntimeError("Exactly one original final encoder representation required")
    h = captured[0].cpu().numpy().copy()
    rank_score = out["rank_score"].detach().cpu().numpy().copy()
    if not np.isfinite(h).all() or not np.isfinite(rank_score).all():
        raise FloatingPointError("Frozen original visible inference must be finite")
    return h, rank_score
