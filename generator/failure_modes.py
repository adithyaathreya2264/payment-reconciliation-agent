from __future__ import annotations

import random

from . import config

_MODE_NAMES = list(config.FAILURE_MODE_TARGETS.keys())
_MODE_WEIGHTS = list(config.FAILURE_MODE_TARGETS.values())


def sample_failure_mode(rng: random.Random) -> str:
    """Draw one failure mode per config.FAILURE_MODE_TARGETS. This is the literal
    'decide the ground truth before generating data' step."""
    return rng.choices(_MODE_NAMES, weights=_MODE_WEIGHTS, k=1)[0]


def true_reason(mode: str) -> str:
    return config.TRUE_REASONS[mode]


def detectable_tier(mode: str) -> str:
    return config.EXPECTED_TIERS[mode][0]


def identifiable_tier(mode: str) -> str:
    return config.EXPECTED_TIERS[mode][1]


def apply_rounding_variance(true_net_amount: float, rng: random.Random) -> tuple[float, float]:
    """Perturb a settlement's net amount by a small plausible rounding/fee miscalculation.

    Returns (distorted_amount, delta) where delta = distorted_amount - true_net_amount.
    """
    magnitude = rng.uniform(config.ROUNDING_VARIANCE_MIN, config.ROUNDING_VARIANCE_MAX)
    sign = rng.choice([-1, 1])
    delta = round(sign * magnitude, 2)
    return round(true_net_amount + delta, 2), delta


def late_settlement_extra_days(rng: random.Random) -> int:
    return rng.randint(
        config.LATE_SETTLEMENT_EXTRA_DAYS_MIN, config.LATE_SETTLEMENT_EXTRA_DAYS_MAX
    )
