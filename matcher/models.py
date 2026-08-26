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
    credit: float
    amount: float
    fee: float
    tax: float
    settlement_utr: str
    order_id_raw: str
    claimed_invoice_ids: list[str]
    settled_at: date | None


@dataclass
class BankRecordRow:
    bank_record_id: str
    date: date
    narration: str
    amount: float
    bank_name: str
    utr_reference: str | None
    parsed_utr_from_narration: str | None


@dataclass
class ScoreBreakdown:
    amount_delta: float
    amount_delta_abs: float
    settlement_lag_days: int | None
    posting_lag_days: int | None
    amount_delta_normalized: float
    lag_delta_normalized: float
    notes: str = ""


@dataclass
class MatchResult:
    bank_record_id: str
    tier: str
    confidence: float
    settlement_id: str | None
    matched_invoice_ids: list[str]
    score: ScoreBreakdown
    candidate_pool_size: int


@dataclass
class EscalationRecord:
    bank_record_id: str
    stage_reached: str
    reason: str
    candidate_subsets: list[list[str]] | None
    pool_invoice_ids: list[str]


@dataclass
class LLMDecision:

    bank_record_id: str
    decision: str
    candidate_ids: list[str]
    confidence: float
    reason: str
    tool_calls_used: int
    input_tokens: int
    output_tokens: int
    latency_ms: float
    origin: str = "llm"
