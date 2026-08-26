"""Tier 3: bounded subset-sum search.

For a bank record unresolved after Tiers 1-2 -- this includes both orphan_payment
(no settlement/UTR exists at all) and ambiguous_subset_sum (a settlement DOES exist,
but its order_id is deliberately blanked, so Tier 1/2 can never confirm its identity
via compute_settlement_lag_days -- see tier_exact.py's docstring on that function).
Both land here identically: search a date-bounded pool of still-unmatched invoices
for any subset summing to the bank amount within tolerance.

Zero minimal subsets -> escalate (arithmetic alone can't explain this record:
entity_name_variant, duplicate_amount_collision). Exactly one -> resolved, but at a
confidence below 1.0. More than one -> genuine ambiguity -- package all of them as
competing hypotheses and do not guess.

Note: the pool is "still unmatched" at the time THIS bank record is processed (Tier 3
runs after Tier 1/2 have resolved everything they can across ALL bank records). For
an ambiguous_subset_sum case, this means the specific decoy invoices the generator
engineered may already have been claimed by their own legitimate settlement and are
no longer visible here -- in which case this search may find only the true subset and
resolve it as if unique. This is a real, reported limitation (see
grade.py's ambiguous_incorrectly_resolved bucket), not a silent failure: many cases
still exhibit genuine multi-candidate ambiguity from whatever else remains unclaimed
in the pool at that point, which is itself an honest reflection of how ambiguity in a
real reconciliation system depends on processing order.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from . import config, scoring
from .models import BankRecordRow, EscalationRecord, InvoiceRecord, LLMDecision, MatchResult, ScoreBreakdown

_MAX_DP_SUBSETS = 5000  # safety valve against combinatorial blow-up in pathological pools


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
                    continue  # all amounts positive: can never come back down
                new_dp[new_sum].append(subset | {inv_id})
                total_stored += 1
                if total_stored > _MAX_DP_SUBSETS:
                    raise _PoolTooComplex()
        dp = new_dp

    valid_sums = [s for s in dp if target_c - tol_c <= s <= target_c + tol_c]
    raw_subsets = [subset for s in valid_sums for subset in dp[s] if subset]  # drop the empty set
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
    """A tier3_no_candidates escalation means Tier 3's own subset-sum search already
    concluded, exhaustively, that nothing in the bounded pool explains the bank
    amount -- a real computed result already stored on the escalation, not something
    this function re-derives or guesses at. That's strong enough evidence to classify
    it as a likely orphan payment without an LLM call.

    Hard precondition: only ever call this on a tier3_no_candidates escalation. It
    raises rather than silently handling tier3_ambiguous (or any other stage), so a
    future refactor can't accidentally widen this rule's scope to cases where
    candidate subsets genuinely exist and picking "no_match" would be wrong.

    Known, accepted limitation (validated against ground truth, see config.py's
    ORPHAN_RULE_CONFIDENCE comment): 34/35 tier3_no_candidates cases on the seed-42
    dataset are genuine orphans this rule correctly classifies. The 1 miss is an
    ambiguous_subset_sum case whose decoy fell outside Tier 3's search window --
    structurally indistinguishable from a genuine orphan at runtime (there is no
    stored signal that tells the two apart without seeing ground truth), so this
    function cannot and does not special-case it. It's excluded from being counted as
    a *new* failure mode in grading -- it's the same already-documented
    search-window limitation, not a defect in this rule.
    """
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
