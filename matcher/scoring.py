from __future__ import annotations

from . import config
from .models import ScoreBreakdown


def tier1_confidence(score: ScoreBreakdown) -> float:
    return config.TIER1_CONFIDENCE


def tier2_confidence(score: ScoreBreakdown) -> float:
    
    used = max(score.amount_delta_normalized, score.lag_delta_normalized)
    return round(max(0.5, 1.0 - used), 3)


def tier3_confidence(score: ScoreBreakdown, subset_size: int) -> float:

    used = score.amount_delta_normalized
    base = config.TIER3_BASE_CONFIDENCE * (1.0 - 0.3 * used)
    if subset_size <= 1:
        base *= config.TIER3_SINGLE_INVOICE_PENALTY
    return round(base, 3)
