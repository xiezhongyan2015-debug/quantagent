"""Frozen independent V10A acceptance. Never an automatic patch target."""
from .oracle import (FROZEN_PROTOCOL, audit_package, freeze_authority, freeze_oracle,
                     independent_daily, independent_annual, judge_annual,
                     scalar_pearson, verify_oracle)

__all__ = ["FROZEN_PROTOCOL", "audit_package", "freeze_authority", "freeze_oracle",
           "independent_daily", "independent_annual", "judge_annual",
           "scalar_pearson", "verify_oracle"]
