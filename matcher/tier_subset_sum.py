from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from . import config, scoring
from .models import BankRecordRow, EscalationRecord, InvoiceRecord, LLMDecision, MatchResult, ScoreBreakdown

_MAX_DP_SUBSETS = 5000

class _PoolTooComplex(Exception):
    pass


def _candidate_pool(bank: BankRecordRow, unmatched_invoices: dict[str, InvoiceRecord]) -> list[InvoiceRecord]:
    lo = bank.date - timedelta(days=config.TIER3_DATE_WINDOW_DAYS)
    hi = bank.date
    return [inv for inv in unmatched_invoices.values() if lo <= inv.expected_date <= hi]


def _minimal_subsets(subsets: list[frozenset[str]]) -> list[frozenset[str]]:
    uniq = list(set(subsets))
    return [s for s in uniq if not any(other < s for other in uniq if other is not s)]


def find_subsets_within_tolerance(
    pool: list[InvoiceRecord], target: float, tolerance: float
) -> list[frozenset[str]]:
    scale = config.SUBSET_SUM_CENTS_SCALE
    target_c = round(target * scale)
    tol_c = round(tolerance * scale)
    items = [(inv.invoice_id, round(inv.expected_amount * scale)) for inv in pool]

    dp: dict[int, list[frozenset[str]]] = {0: [frozenset()]}
    total_stored = 1
    for inv_id, amt_c in items:
        new_dp: dict[int, list[frozenset[str]]] = defaultdict(list)
        for s, subsets in dp.items():
            new_dp[s].extend(subsets)
            for subset in subsets:
                if len(subset) >= config.TIER3_MAX_SUBSET_SIZE:
                    continue
                new_sum = s + amt_c
                if new_sum > target_c + tol_c:
                    continue
                new_dp[new_sum].append(subset | {inv_id})
                total_stored += 1
                if total_stored > _MAX_DP_SUBSETS:
                    raise _PoolTooComplex()
        dp = new_dp

    valid_sums = [s for s in dp if target_c - tol_c <= s <= target_c + tol_c]
    raw_subsets = [subset for s in valid_sums for subset in dp[s] if subset]
    return _minimal_subsets(raw_subsets)


def match(
    bank: BankRecordRow, unmatched_invoices: dict[str, InvoiceRecord]
) -> MatchResult | EscalationRecord:
    pool = _candidate_pool(bank, unmatched_invoices)
    target_amount = bank.amount
    max_pool_size = config.TIER3_MAX_POOL_SIZE

    if not pool:
        return EscalationRecord(
            bank_record_id=bank.bank_record_id,
            stage_reached="tier3_no_candidates",
            reason="no invoices in date window",
            candidate_subsets=None,
            pool_invoice_ids=[],
        )

    if len(pool) > max_pool_size:
        return EscalationRecord(
            bank_record_id=bank.bank_record_id,
            stage_reached="tier3_pool_too_large",
            reason=f"pool size {len(pool)} exceeds cap {max_pool_size}",
            candidate_subsets=None,
            pool_invoice_ids=[i.invoice_id for i in pool],
        )

    pool_ids = [i.invoice_id for i in pool]
    try:
        subsets = find_subsets_within_tolerance(pool, target_amount, config.TIER3_AMOUNT_TOLERANCE)
    except _PoolTooComplex:
        return EscalationRecord(
            bank_record_id=bank.bank_record_id,
            stage_reached="tier3_pool_too_large",
            reason="subset-sum search space exceeded safety cap",
            candidate_subsets=None,
            pool_invoice_ids=pool_ids,
        )

    if len(subsets) == 0:
        return EscalationRecord(
            bank_record_id=bank.bank_record_id,
            stage_reached="tier3_no_candidates",
            reason="no subset of candidate pool sums within tolerance",
            candidate_subsets=None,
            pool_invoice_ids=pool_ids,
        )

    if len(subsets) == 1:
        subset = subsets[0]
        pool_by_id = {i.invoice_id: i for i in pool}
        matched_ids = sorted(subset)
        subset_sum = round(sum(pool_by_id[iid].expected_amount for iid in matched_ids), 2)
        amount_delta = target_amount - subset_sum
        amount_delta_abs = abs(amount_delta)
        score = ScoreBreakdown(
            amount_delta=amount_delta,
            amount_delta_abs=amount_delta_abs,
            settlement_lag_days=None,
            posting_lag_days=None,
            amount_delta_normalized=amount_delta_abs / config.TIER3_AMOUNT_TOLERANCE,
            lag_delta_normalized=0.0,
        )
        return MatchResult(
            bank_record_id=bank.bank_record_id,
            tier="subset_sum",
            confidence=scoring.tier3_confidence(score, subset_size=len(matched_ids)),
            settlement_id=None,
            matched_invoice_ids=matched_ids,
            score=score,
            candidate_pool_size=len(pool),
        )

    return EscalationRecord(
        bank_record_id=bank.bank_record_id,
        stage_reached="tier3_ambiguous",
        reason=f"{len(subsets)} minimal subsets sum within tolerance",
        candidate_subsets=[sorted(s) for s in subsets],
        pool_invoice_ids=pool_ids,
    )


def classify_zero_candidate_orphan(escalation: EscalationRecord) -> LLMDecision | None:

    if escalation.stage_reached != "tier3_no_candidates":
        raise ValueError(
            f"classify_zero_candidate_orphan only applies to tier3_no_candidates "
            f"escalations, got {escalation.stage_reached!r} for {escalation.bank_record_id}"
        )
    return LLMDecision(
        bank_record_id=escalation.bank_record_id,
        decision="no_match",
        candidate_ids=[],
        confidence=config.ORPHAN_RULE_CONFIDENCE,
        reason=(
            "No combination of open invoices in the search window sums to this amount "
            "within tolerance -- classified as likely orphan payment."
        ),
        tool_calls_used=0,
        input_tokens=0,
        output_tokens=0,
        latency_ms=0.0,
        origin="rule",
    )
