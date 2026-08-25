"""Dataclasses for the reconciliation-generator entity model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Invoice:
    invoice_id: str
    counterparty_canonical: str
    expected_amount: float
    expected_date: date
    payment_method: str  # UPI | NEFT | RTGS | card
    status: str  # paid | unpaid | partially_paid
    planned_failure_mode: str  # internal only, not written to invoices.csv
    collision_group_id: str | None = None


@dataclass
class Settlement:
    settlement_id: str
    entity_id: str
    type: str  # "settlement"
    debit: float
    credit: float
    amount: float  # gross_amount (Razorpay convention)
    fee: float
    tax: float
    net_amount: float
    settlement_utr: str
    settled_at: date
    order_id: str
    invoice_ids: list[str] = field(default_factory=list)
    failure_mode: str = "clean_match"
    true_net_amount: float | None = None  # pre-distortion value, for rounding_fee_variance


@dataclass
class BankRecord:
    bank_record_id: str
    date: date
    narration: str
    amount: float
    bank_name: str
    utr_reference: str | None
    settlement_id: str | None  # None for orphan_payment
    payment_method: str


@dataclass
class GroundTruthEntry:
    invoice_ids: list[str]
    bank_record_id: str | None
    settlement_id: str | None
    true_match_ids: list[str]
    failure_mode_injected: str
    true_reason: str
    detectable_at_tier: str  # exact | tolerance | fuzzy | subset_sum | llm_escalation | unresolvable
    identifiable_at_tier: str  # tier at which the *specific* correct match can be pinned down
    competing_subset_ids: list[str] | None = None
    collision_group_id: str | None = None
    dropped_invoice_id: str | None = None
