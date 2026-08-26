"""Typed records for the matcher.

Deliberately distinct from generator.models: this package must simulate a real
downstream consumer that only ever sees the three CSVs (and, in grade.py alone, the
hidden ground truth) — never the generator's internal Python objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class InvoiceRecord:
    invoice_id: str
    counterparty_canonical: str
    expected_amount: float
    expected_date: date
    payment_method: str
    status: str


@dataclass
class SettlementRecord:
    settlement_id: str
    entity_id: str
    type: str
    debit: float
    credit: float  # NOTE: this is net_amount per the Razorpay-shaped CSV, not a literal "credit"
    amount: float  # gross
    fee: float
    tax: float
    settlement_utr: str
    order_id_raw: str  # verbatim pipe-joined string, kept for traceability
    claimed_invoice_ids: list[str]  # order_id_raw.split("|")
    settled_at: date | None  # None only if reading a pre-fix CSV missing the column


@dataclass
class BankRecordRow:
    bank_record_id: str
    date: date
    narration: str
    amount: float
    bank_name: str
    utr_reference: str | None  # "" in CSV -> None
    parsed_utr_from_narration: str | None  # best-effort secondary signal, see loaders.py


@dataclass
class ScoreBreakdown:
    amount_delta: float
    amount_delta_abs: float
    settlement_lag_days: int | None  # settled_at - latest claimed invoice date; None for Tier 3
    posting_lag_days: int | None  # bank.date - settled_at; None for Tier 3
    amount_delta_normalized: float
    lag_delta_normalized: float  # normalized settlement_lag_days overshoot beyond the normal range
    notes: str = ""


@dataclass
class MatchResult:
    bank_record_id: str
    tier: str  # "exact" | "tolerance" | "subset_sum"
    confidence: float
    settlement_id: str | None  # None allowed only for a Tier 3 match with no corresponding settlement row
    matched_invoice_ids: list[str]
    score: ScoreBreakdown
    candidate_pool_size: int


@dataclass
class EscalationRecord:
    bank_record_id: str
    stage_reached: str  # "tier3_ambiguous" | "tier3_no_candidates" | "tier3_pool_too_large"
    reason: str
    candidate_subsets: list[list[str]] | None  # only for tier3_ambiguous
    pool_invoice_ids: list[str]


@dataclass
class LLMDecision:
    """An escalation-tier decision, regardless of whether it came from a deterministic
    rule (origin="rule", zero cost/latency by construction) or an actual LLM call
    (origin="llm"). Keeping one shape for both lets grade_llm.py and reporting.py grade
    and report them uniformly while still reporting the origin split distinctly --
    see tier_subset_sum.py::classify_zero_candidate_orphan and orchestrator.py."""

    bank_record_id: str
    decision: str  # "match" | "no_match" | "insufficient_evidence"
    candidate_ids: list[str]
    confidence: float
    reason: str
    tool_calls_used: int
    input_tokens: int
    output_tokens: int
    latency_ms: float
    origin: str = "llm"  # "rule" | "llm"
