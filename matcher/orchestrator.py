"""Runs the tiers in order across ALL bank records at each stage (not per-record
tier-1-through-3), so pool consumption doesn't depend on arbitrary CSV row order --
see the plan's rationale. Tier 1 for everyone, then Tier 2 for the rest, then Tier 3
for the rest."""

from __future__ import annotations

from . import llm_tier, tier_exact, tier_subset_sum, tier_tolerance
from .llm_client import LLMClient
from .models import BankRecordRow, EscalationRecord, InvoiceRecord, LLMDecision, MatchResult, SettlementRecord


def run(
    invoices: list[InvoiceRecord],
    settlements: list[SettlementRecord],
    bank_records: list[BankRecordRow],
    llm_client: LLMClient | None = None,
) -> tuple[list[MatchResult], list[EscalationRecord], list[LLMDecision]]:
    invoices_by_id = {i.invoice_id: i for i in invoices}
    unmatched_settlements = {s.settlement_id: s for s in settlements}
    unmatched_invoices = dict(invoices_by_id)

    matches: list[MatchResult] = []
    escalations: list[EscalationRecord] = []

    def consume(result: MatchResult) -> None:
        matches.append(result)
        if result.settlement_id:
            unmatched_settlements.pop(result.settlement_id, None)
        for iid in result.matched_invoice_ids:
            unmatched_invoices.pop(iid, None)

    def run_tier(bank_list, tier_fn):
        still_pending = []
        for b in bank_list:
            outcome = tier_fn(b, unmatched_settlements, invoices_by_id)
            if isinstance(outcome, MatchResult):
                consume(outcome)
            else:
                still_pending.append(b)
        return still_pending

    still_pending = run_tier(bank_records, tier_exact.match)
    still_pending = run_tier(still_pending, tier_tolerance.match)

    for b in still_pending:
        outcome = tier_subset_sum.match(b, unmatched_invoices)
        if isinstance(outcome, MatchResult):
            consume(outcome)
        else:
            escalations.append(outcome)

    llm_decisions: list[LLMDecision] = []
    if llm_client is not None and escalations:
        # Deterministic rule first: a tier3_no_candidates escalation already means
        # Tier 3's own search concluded nothing in the pool explains the amount --
        # that's real evidence, not a guess, so it needs no LLM call. Gated on the
        # stage field explicitly (classify_zero_candidate_orphan raises on any other
        # stage), so this can never accidentally swallow a genuinely ambiguous case.
        rule_resolved: list[LLMDecision] = []
        remaining_escalations: list[EscalationRecord] = []
        for e in escalations:
            if e.stage_reached == "tier3_no_candidates":
                rule_resolved.append(tier_subset_sum.classify_zero_candidate_orphan(e))
            else:
                remaining_escalations.append(e)

        llm_resolved: list[LLMDecision] = []
        if remaining_escalations:
            bank_by_id = {b.bank_record_id: b for b in bank_records}
            llm_resolved = llm_tier.resolve(llm_client, remaining_escalations, bank_by_id, invoices_by_id, matches)

        llm_decisions = rule_resolved + llm_resolved

    return matches, escalations, llm_decisions
