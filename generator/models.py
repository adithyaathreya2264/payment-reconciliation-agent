from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Invoice:
    invoice_id: str
    counterparty_canonical: str
    expected_amount: float
    expected_date: date
    payment_method: str  
    status: str  
    planned_failure_mode: str  
    collision_group_id: str | None = None


@dataclass
class Settlement:
    settlement_id: str
    entity_id: str
    type: str  
    debit: float
    credit: float
    amount: float  
    fee: float
    tax: float
    net_amount: float
    settlement_utr: str
    settled_at: date
    order_id: str
    invoice_ids: list[str] = field(default_factory=list)
    failure_mode: str = "clean_match"
    true_net_amount: float | None = None  


@dataclass
class BankRecord:
    bank_record_id: str
    date: date
    narration: str
    amount: float
    bank_name: str
    utr_reference: str | None
    settlement_id: str | None  
    payment_method: str


@dataclass
class GroundTruthEntry:
    invoice_ids: list[str]
    bank_record_id: str | None
    settlement_id: str | None
    true_match_ids: list[str]
    failure_mode_injected: str
    true_reason: str
    detectable_at_tier: str
    identifiable_at_tier: str
    competing_subset_ids: list[str] | None = None
    collision_group_id: str | None = None
    dropped_invoice_id: str | None = None
