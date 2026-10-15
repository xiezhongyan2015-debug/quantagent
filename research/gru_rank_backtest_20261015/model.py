"""The frozen OLD_RANK control architecture, with original state_dict names.

PlanGRU and MarketGRU layer/forward definitions retain the original mathematics.
The unused attention/FiLM parameters remain for strict original-checkpoint loads.
This module does not implement training or data downloads.
"""
from __future__ import annotations

import torch
from torch import nn

GROUPS = {
    "hour": list(range(24)), "daily": list(range(24, 40)),
    "factors": list(range(40, 104)), "fund": list(range(104, 120)),
    "ages": [120, 121],
}
SEQUENCE_LENGTH = 80
N_FEATURES = 122
N_INDUSTRIES = 39
N_PARAMETERS = 410116


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
        z, _ = self.gru(z)
        return self.head(z[:, -1]).squeeze(-1)


class MarketGRU(nn.Module):
    def __init__(self, groups=GROUPS, n_industries=N_INDUSTRIES, variant="control"):
        super().__init__()
        if groups != GROUPS or n_industries != 39 or variant != "control":
            raise ValueError("This package is the 122-input/39-industry OLD_RANK control")
        self.variant=variant;self.backbone=PlanGRU(groups,n_industries,'full');self.backbone.head=nn.Identity()
        self.attention=nn.TransformerEncoderLayer(128,4,dim_feedforward=512,dropout=.2,batch_first=True,norm_first=True,activation='gelu')
        self.film=nn.Sequential(nn.Linear(12,32),nn.GELU(),nn.Linear(32,256))
        self.head=nn.Sequential(nn.LayerNorm(128),nn.Linear(128,64),nn.GELU(),nn.Dropout(.2),nn.Linear(64,4))

    def forward(self,x,industry,slots,market,counts):
        z=self.backbone(x,industry,slots);out=[];start=0
        assert len(market)==len(counts) and sum(counts)==len(z)
        for i,n in enumerate(counts):
            h=z[start:start+n];start+=n
            if self.variant=='market_attention':
                h=self.attention(h.unsqueeze(0)).squeeze(0)
                m=market[i];missing=~torch.isfinite(m);m=torch.cat([torch.where(missing,0.,m),missing.float()])
                gamma,beta=self.film(m).chunk(2);h=h*(1+.1*torch.tanh(gamma))+.1*beta
            out.append(self.head(h))
        return torch.cat(out)
