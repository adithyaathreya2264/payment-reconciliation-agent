from __future__ import annotations

from . import config
from .models import BankRecordRow, InvoiceRecord, MatchResult, ScoreBreakdown, SettlementRecord


def compute_settlement_lag_days(
    settlement: SettlementRecord, invoices_by_id: dict[str, InvoiceRecord]
) -> int | None:

    if settlement.settled_at is None:
        return None
    dates = [
        invoices_by_id[iid].expected_date
        for iid in settlement.claimed_invoice_ids
        if iid in invoices_by_id
    ]
    if not dates:
        return None
    return (settlement.settled_at - max(dates)).days


def compute_posting_lag_days(bank: BankRecordRow, settlement: SettlementRecord) -> int | None:
    if settlement.settled_at is None:
        return None
    return (bank.date - settlement.settled_at).days


def match(
    bank: BankRecordRow,
    unmatched_settlements: dict[str, SettlementRecord],
    invoices_by_id: dict[str, InvoiceRecord],
) -> MatchResult | None:
    utr = bank.utr_reference or bank.parsed_utr_from_narration
    if not utr:
        return None

    candidates = [s for s in unmatched_settlements.values() if s.settlement_utr == utr]
    if not candidates:
        return None

    for s in candidates:
        amount_delta = bank.amount - s.credit
        amount_delta_abs = abs(amount_delta)
        posting_lag_days = compute_posting_lag_days(bank, s)
        settlement_lag_days = compute_settlement_lag_days(s, invoices_by_id)

        if posting_lag_days is None or settlement_lag_days is None:
            continue
        if not (0 <= posting_lag_days <= config.POSTING_LAG_MAX_DAYS):
            continue
        if not (1 <= settlement_lag_days <= config.TIER1_MAX_NORMAL_LAG_DAYS):
            continue
        if amount_delta_abs > config.TIER1_AMOUNT_TOLERANCE:
            continue

        return MatchResult(
            bank_record_id=bank.bank_record_id,
            tier="exact",
            confidence=config.TIER1_CONFIDENCE,
            settlement_id=s.settlement_id,
            matched_invoice_ids=list(s.claimed_invoice_ids),
            score=ScoreBreakdown(
                amount_delta=amount_delta,
                amount_delta_abs=amount_delta_abs,
                settlement_lag_days=settlement_lag_days,
                posting_lag_days=posting_lag_days,
                amount_delta_normalized=0.0,
                lag_delta_normalized=0.0,
            ),
            candidate_pool_size=len(candidates),
        )
    return None
