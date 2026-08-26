"""Tier 2: tolerance match.

Relaxes amount and settlement-lag independently, but only accepts a candidate if at
least one dimension is still near-exact -- this is what stops two independently-loose
thresholds from compounding into an accept-everything net.
"""

from __future__ import annotations

from . import config
from .tier_exact import compute_posting_lag_days, compute_settlement_lag_days
from .models import BankRecordRow, InvoiceRecord, MatchResult, ScoreBreakdown, SettlementRecord


def _accept(amount_delta_abs: float, settlement_lag_days: int) -> bool:
    amount_in_band = amount_delta_abs <= config.TIER2_AMOUNT_TOLERANCE_MAX
    lag_in_band = 1 <= settlement_lag_days <= config.TIER2_MAX_LATE_LAG_DAYS
    if not (amount_in_band and lag_in_band):
        return False
    amount_exact_ish = amount_delta_abs <= config.TIER2_AMOUNT_EXACT_ISH
    lag_exact_ish = settlement_lag_days <= config.TIER2_LAG_EXACT_ISH_DAYS
    return amount_exact_ish or lag_exact_ish


def match(
    bank: BankRecordRow,
    unmatched_settlements: dict[str, SettlementRecord],
    invoices_by_id: dict[str, InvoiceRecord],
) -> MatchResult | None:
    best_key = None
    best_settlement = None
    best_score = None

    for s in unmatched_settlements.values():
        posting_lag_days = compute_posting_lag_days(bank, s)
        settlement_lag_days = compute_settlement_lag_days(s, invoices_by_id)
        if posting_lag_days is None or settlement_lag_days is None:
            continue
        if not (0 <= posting_lag_days <= config.POSTING_LAG_MAX_DAYS):
            continue

        amount_delta = bank.amount - s.credit
        amount_delta_abs = abs(amount_delta)
        if not _accept(amount_delta_abs, settlement_lag_days):
            continue

        # Both dimensions normalize against the REALISTIC achievable range, not the
        # full configured band width: a genuine rounding_fee_variance/late_settlement
        # case can never land near the unadjusted floor of 0 (the generator's own
        # ROUNDING_VARIANCE_MIN/LATE_SETTLEMENT_EXTRA_DAYS_MIN guarantee a nonzero
        # minimum distortion), so normalizing from 0 compresses every real case into
        # a narrow band regardless of how close-to-boundary vs. far-from-boundary it
        # actually is. See config.py::TIER2_LAG_REALISTIC_MIN_OVERSHOOT_DAYS and
        # MATCHER_STATUS.md's calibration section for the empirical evidence.
        amount_floor = config.TIER2_AMOUNT_TOLERANCE_MIN
        amount_span = config.TIER2_AMOUNT_TOLERANCE_MAX - amount_floor
        amount_delta_normalized = max(0.0, min(1.0, (amount_delta_abs - amount_floor) / amount_span))

        lag_overshoot = max(0, settlement_lag_days - config.TIER1_MAX_NORMAL_LAG_DAYS)
        lag_floor = config.TIER2_LAG_REALISTIC_MIN_OVERSHOOT_DAYS
        lag_span = (config.TIER2_MAX_LATE_LAG_DAYS - config.TIER1_MAX_NORMAL_LAG_DAYS) - lag_floor
        lag_delta_normalized = max(0.0, min(1.0, (lag_overshoot - lag_floor) / lag_span)) if lag_span else 0.0

        score = ScoreBreakdown(
            amount_delta=amount_delta,
            amount_delta_abs=amount_delta_abs,
            settlement_lag_days=settlement_lag_days,
            posting_lag_days=posting_lag_days,
            amount_delta_normalized=amount_delta_normalized,
            lag_delta_normalized=lag_delta_normalized,
        )
        key = amount_delta_normalized + lag_delta_normalized
        if best_key is None or key < best_key:
            best_key, best_settlement, best_score = key, s, score

    if best_settlement is None:
        return None

    from . import scoring

    confidence = scoring.tier2_confidence(best_score)
    return MatchResult(
        bank_record_id=bank.bank_record_id,
        tier="tolerance",
        confidence=confidence,
        settlement_id=best_settlement.settlement_id,
        matched_invoice_ids=list(best_settlement.claimed_invoice_ids),
        score=best_score,
        candidate_pool_size=len(unmatched_settlements),
    )
