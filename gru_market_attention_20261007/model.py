"""The PLAN single GRU. Continuous NaNs become zero plus explicit missing flags."""
from __future__ import annotations

from contextlib import nullcontext
import torch
from torch import nn
from torch.func import functional_call


class PlanGRU(nn.Module):
    def __init__(self, groups: dict, n_industries: int = 128, variant: str = "full"):
        super().__init__()
        if variant not in ("full", "pv", "pv_factors", "pv_fund"):
            raise ValueError(f"Unknown input variant: {variant}")
        self.groups = {k: list(v) for k, v in groups.items()}
        self.variant = variant
        self.hour = nn.Sequential(nn.Linear(2 * len(groups["hour"]), 32), nn.GELU())
        self.daily = nn.Sequential(nn.Linear(2 * (len(groups["daily"]) + len(groups["factors"])), 64), nn.GELU())
        self.industry = nn.Embedding(n_industries, 8)
        self.fund = nn.Sequential(nn.Linear(2 * (len(groups["fund"]) + len(groups["ages"])) + 8, 16), nn.GELU())
        self.slot = nn.Embedding(4, 4)
        self.project = nn.Sequential(nn.LayerNorm(116), nn.Linear(116, 64), nn.GELU(), nn.Dropout(0.10))
        self.gru = nn.GRU(64, 128, num_layers=2, batch_first=True, dropout=0.15)
        self.head = nn.Sequential(nn.LayerNorm(128), nn.Linear(128, 64), nn.GELU(), nn.Dropout(0.20), nn.Linear(64, 1))

    @staticmethod
    def with_missing(x: torch.Tensor) -> torch.Tensor:
        missing = ~torch.isfinite(x)
        return torch.cat((torch.where(missing, 0.0, x), missing.to(x.dtype)), dim=-1)

    def forward(self, x: torch.Tensor, industry: torch.Tensor, slots: torch.Tensor) -> torch.Tensor:
        if x.dtype != torch.float32:
            raise ValueError("PLAN uses FP32 inputs")
        h = self.hour(self.with_missing(x[..., self.groups["hour"]]))
        daily = self.with_missing(x[..., self.groups["daily"]])
        factors = self.with_missing(x[..., self.groups["factors"]])
        if self.variant in ("pv", "pv_fund"):
            factors = torch.zeros_like(factors)
        d = self.daily(torch.cat((daily, factors), dim=-1))
        f = self.fund(torch.cat((self.with_missing(x[..., self.groups["fund"] + self.groups["ages"]]),
                                 self.industry(industry)), dim=-1))
        if self.variant in ("pv", "pv_factors"):
            f = f * 0.0
        if slots.ndim == 1:
            slots = slots.unsqueeze(0).expand(x.shape[0], -1)
        z = self.project(torch.cat((h, d, f, self.slot(slots)), dim=-1))
        z, _ = self.gru(z)  # A new zero hidden state for every independent window.
        return self.head(z[:, -1]).squeeze(-1)


def ic_losses(scores: torch.Tensor, labels: torch.Tensor, counts: list[int], min_stocks: int = 100,
              variance_floor: float = 1e-12) -> tuple[torch.Tensor, int]:
    """Four losses; each valid timestamp is one equally weighted observation."""
    losses, skipped = [], 0
    start = 0
    for n in counts:
        s, y = scores[start:start + n], labels[start:start + n]
        start += n
        common = torch.isfinite(s) & torch.isfinite(y).all(dim=-1)
        s, y = s[common], y[common]
        if s.numel() < min_stocks:
            skipped += 1
            continue
        sc, yc = s - s.mean(), y - y.mean(dim=0)
        sy = yc.square().mean(dim=0)
        if bool((sy <= variance_floor).any()):
            skipped += 1
            continue
        # clamp only protects a constant model. Such a model gets IC=0, not a skipped loss.
        corr = (sc[:, None] * yc).mean(dim=0) / (sc.square().mean().clamp_min(variance_floor).sqrt() * sy.sqrt())
        losses.append(1.0 - corr)
    if start != scores.numel():
        raise ValueError("Cross section counts do not cover scores")
    if not losses:
        raise ValueError("No eligible cross section; min count or label variance failed")
    return torch.stack(losses).mean(dim=0), skipped


def adamw_groups(model: nn.Module, weight_decay: float) -> list[dict]:
    decay, no_decay = [], []
    for name, p in model.named_parameters():
        (no_decay if p.ndim < 2 or name.endswith("bias") else decay).append(p)
    return [{"params": decay, "weight_decay": weight_decay}, {"params": no_decay, "weight_decay": 0.0}]


def unrolled_step(model: PlanGRU, logits: torch.Tensor, inner: tuple, outer: tuple,
                  learning_rate: float = 1e-3, weight_decay: float = 1e-4,
                  entropy_gamma: float = 1e-3, min_stocks: int = 100):
    """One differentiable SGD step. Caller writes detached parameters back after the hyperstep.

    cuDNN's GRU does not implement the needed double backward on the tested cluster.
    The fallback remains FP32 and only applies to this BLO computation.
    """
    x, industry, slots, labels, counts = inner
    vx, vi, vs, vy, vc = outer
    context = torch.backends.cudnn.flags(enabled=False) if x.is_cuda else nullcontext()
    with context:
        parameters = dict(model.named_parameters())
        weights = logits.softmax(dim=0)
        losses, skipped = ic_losses(model(x, industry, slots), labels, counts, min_stocks)
        inner_loss = (weights * losses).sum()
        grads = torch.autograd.grad(inner_loss, tuple(parameters.values()), create_graph=True)
        updated = {name: p - learning_rate * (g + (weight_decay * p if p.ndim >= 2 else 0.0))
                   for (name, p), g in zip(parameters.items(), grads)}
        validation_scores = functional_call(model, updated, (vx, vi, vs))
        outer_losses, outer_skipped = ic_losses(validation_scores, vy, vc, min_stocks)
        target_loss = outer_losses[3]
        entropy = -(weights * weights.clamp_min(1e-12).log()).sum()
        predictive_gradient = torch.autograd.grad(target_loss, logits, retain_graph=True)[0]
        gradient = torch.autograd.grad(target_loss - entropy_gamma * entropy, logits)[0]
    if not torch.isfinite(gradient).all() or not torch.isfinite(predictive_gradient).all():
        raise FloatingPointError("BLO produced a non-finite hypergradient")
    if not all(bool(torch.isfinite(p).all()) for p in updated.values()):
        raise FloatingPointError("BLO produced non-finite model parameters")
    return updated, gradient.detach(), {"inner_loss": float(inner_loss.detach()),
        "outer_y4_loss": float(target_loss.detach()), "entropy": float(entropy.detach()),
        "predictive_hypergradient": predictive_gradient.detach().cpu().tolist(),
        "hypergradient_norm": float(gradient.detach().norm()), "skipped_sections": skipped + outer_skipped}
