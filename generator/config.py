from __future__ import annotations

from datetime import date

FAILURE_MODE_TARGETS: dict[str, float] = {
    "clean_match": 0.66,
    "rounding_fee_variance": 0.11,
    "late_settlement": 0.07,  
    "entity_name_variant": 0.045,
    "duplicate_amount_collision": 0.035,  
    "ambiguous_subset_sum": 0.03,
    "partial_batch_failure": 0.02,
    "orphan_payment": 0.015,
    "orphan_invoice": 0.015,  
}

_total = sum(FAILURE_MODE_TARGETS.values())
if abs(_total - 1.0) > 1e-9:
    raise ValueError(f"FAILURE_MODE_TARGETS must sum to 1.0, got {_total}")

LOW_FREQUENCY_MODES = (
    "ambiguous_subset_sum",
    "partial_batch_failure",
    "orphan_payment",
    "orphan_invoice",
)
MIN_HEALTHY_COUNT = 10

EXPECTED_TIERS: dict[str, tuple[str, str]] = {
    "clean_match": ("exact", "exact"),
    "rounding_fee_variance": ("tolerance", "tolerance"),
    "late_settlement": ("tolerance", "tolerance"),
    "entity_name_variant": ("fuzzy", "fuzzy"),
    "duplicate_amount_collision": ("exact", "fuzzy"),
    "ambiguous_subset_sum": ("subset_sum", "llm_escalation"),
    
    "partial_batch_failure": ("tolerance", "subset_sum"),
    "orphan_payment": ("unresolvable", "unresolvable"),
    "orphan_invoice": ("unresolvable", "unresolvable"),
}

TRUE_REASONS: dict[str, str] = {
    "clean_match": "Exact amount and date match, no distortion.",
    "rounding_fee_variance": "Settlement net amount perturbed by a small rounding/fee miscalculation.",
    "late_settlement": "Settlement posted later than the normal T+1/T+2 window.",
    "entity_name_variant": "Bank narration uses an alternate form of the counterparty name.",
    "duplicate_amount_collision": "Two unrelated invoices share the same amount and a nearby date.",
    "ambiguous_subset_sum": "Two distinct subsets of the candidate invoice pool sum to the same amount within tolerance.",
    "partial_batch_failure": "One invoice was dropped from the batch before settlement, shorting the total by exactly its amount.",
    "orphan_payment": "Bank credit exists with no corresponding invoice or settlement.",
    "orphan_invoice": "Invoice exists but was never paid.",
}


DEFAULT_NUM_INVOICES = 2000
AMOUNT_MIN = 500.0
AMOUNT_MAX = 200_000.0
DEFAULT_START_DATE = date(2025, 1, 1)
DEFAULT_END_DATE = date(2026, 8, 23)

PAYMENT_METHOD_WEIGHTS: dict[str, float] = {
    "UPI": 0.55,
    "NEFT": 0.20,
    "RTGS": 0.10,
    "card": 0.15,
}


MIN_BATCH_SIZE = 5
MAX_BATCH_SIZE = 40


GST_RATE = 0.18
MDR_RATES: dict[str, float] = {
    "UPI": 0.0,  
    "card": 0.02,
    "NEFT": None,  
    "RTGS": None,  
}
MDR_FLAT_FEES: dict[str, float] = {
    "NEFT": 5.0,
    "RTGS": 25.0,
}


ROUNDING_VARIANCE_MIN = 1.0
ROUNDING_VARIANCE_MAX = 50.0

LATE_SETTLEMENT_EXTRA_DAYS_MIN = 5
LATE_SETTLEMENT_EXTRA_DAYS_MAX = 10

BANK_POSTING_LAG_MAX = 2
