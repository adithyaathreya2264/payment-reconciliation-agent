"""Confidence formulas per tier."""

from __future__ import annotations

from . import config
from .models import ScoreBreakdown


def tier1_confidence(score: ScoreBreakdown) -> float:
    return config.TIER1_CONFIDENCE


def tier2_confidence(score: ScoreBreakdown) -> float:
    # accept() already guaranteed one dimension is near-exact -- the other is what's
    # "spending" confidence, so take the worse (larger) of the two normalized deltas.
    # The normalization itself (in tier_tolerance.py, where these fields are computed)
    # was fixed to use the realistic achievable range rather than the full configured
    # band width -- see config.py::TIER2_LAG_REALISTIC_MIN_OVERSHOOT_DAYS and
    # MATCHER_STATUS.md's calibration section. Before that fix, every real
    # late_settlement case scored 0.5-0.625 regardless of how barely-vs-very late it
    # was; after it, the range is 0.5-0.833, genuinely discriminating within the band.
    used = max(score.amount_delta_normalized, score.lag_delta_normalized)
    return round(max(0.5, 1.0 - used), 3)


def tier3_confidence(score: ScoreBreakdown, subset_size: int) -> float:
    """subset_size matters independently of amount closeness: a single invoice
    landing within tolerance of a target amount is a coincidence that will happen
    fairly often across a few thousand invoices (a size-1 "subset" carries no more
    evidence than a raw amount lookup), whereas a specific multi-invoice combination
    summing to the same target within a tight tolerance is combinatorially far less
    likely to occur by chance. Calibrated against real matcher output: on the
    seed-42 dataset, every genuinely correct Tier 3 match had subset_size >= 2
    (confidence 0.70-0.85 under the old formula); the one confirmed false positive
    was a subset_size == 1 match that the old formula scored at 0.803 -- squarely
    inside the correct range, giving zero discriminative power. See
    MATCHER_STATUS.md's calibration section.
    """
    used = score.amount_delta_normalized
    base = config.TIER3_BASE_CONFIDENCE * (1.0 - 0.3 * used)
    if subset_size <= 1:
        base *= config.TIER3_SINGLE_INVOICE_PENALTY
    return round(base, 3)
