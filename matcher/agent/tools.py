from __future__ import annotations

from dataclasses import asdict, dataclass

from .. import tier_exact, tier_subset_sum, tier_tolerance
from ..llm_client import LLMClient
from ..llm_tier import build_context, resolve as llm_resolve
from ..models import BankRecordRow, EscalationRecord, InvoiceRecord, LLMDecision, MatchResult, SettlementRecord


@dataclass
class ToolResult:
    resolved: bool
    reason: str
    observed: dict
    decision: MatchResult | EscalationRecord | LLMDecision | None


def _match_observed(match: MatchResult) -> dict:
    return {
        "tier": match.tier,
        "settlement_id": match.settlement_id,
        "matched_invoice_ids": match.matched_invoice_ids,
        "confidence": match.confidence,
        "candidate_pool_size": match.candidate_pool_size,
        "score": asdict(match.score),
    }


def exact_tool(
    bank: BankRecordRow,
    unmatched_settlements: dict[str, SettlementRecord],
    invoices_by_id: dict[str, InvoiceRecord],
) -> ToolResult:
    result = tier_exact.match(bank, unmatched_settlements, invoices_by_id)
    if result is None:
        return ToolResult(resolved=False, reason="no exact UTR+amount+settlement-lag match found", observed={"result": "no_match"}, decision=None)
    return ToolResult(resolved=True, reason="exact match", observed=_match_observed(result), decision=result)


def tolerance_tool(
    bank: BankRecordRow,
    unmatched_settlements: dict[str, SettlementRecord],
    invoices_by_id: dict[str, InvoiceRecord],
) -> ToolResult:
    result = tier_tolerance.match(bank, unmatched_settlements, invoices_by_id)
    if result is None:
        return ToolResult(resolved=False, reason="no candidate within tolerance band", observed={"result": "no_match"}, decision=None)
    return ToolResult(resolved=True, reason="tolerance-band match", observed=_match_observed(result), decision=result)


def subset_sum_tool(bank: BankRecordRow, unmatched_invoices: dict[str, InvoiceRecord]) -> ToolResult:
    outcome = tier_subset_sum.match(bank, unmatched_invoices)
    if isinstance(outcome, MatchResult):
        return ToolResult(resolved=True, reason="unique subset found within tolerance", observed=_match_observed(outcome), decision=outcome)
    observed = {
        "stage_reached": outcome.stage_reached,
        "reason": outcome.reason,
        "pool_invoice_ids": outcome.pool_invoice_ids,
        "pool_size": len(outcome.pool_invoice_ids),
        "candidate_subsets": outcome.candidate_subsets,
    }
    return ToolResult(resolved=False, reason=outcome.reason, observed=observed, decision=outcome)


def zero_candidate_rule_tool(escalation: EscalationRecord) -> ToolResult:

    if escalation.stage_reached != "tier3_no_candidates":
        return ToolResult(
            resolved=False,
            reason=f"zero-candidate rule does not apply to stage_reached={escalation.stage_reached!r}",
            observed={"stage_reached": escalation.stage_reached, "gate": "not_applicable"},
            decision=None,
        )
    decision = tier_subset_sum.classify_zero_candidate_orphan(escalation)
    observed = {
        "rule": "classify_zero_candidate_orphan",
        "origin": decision.origin,
        "decision": decision.decision,
        "confidence": decision.confidence,
        "reason": decision.reason,
    }
    return ToolResult(resolved=True, reason="classified via deterministic zero-candidate orphan rule", observed=observed, decision=decision)


def llm_escalation_tool(
    client: LLMClient,
    escalations: list[EscalationRecord],
    bank_by_id: dict[str, BankRecordRow],
    invoices_by_id: dict[str, InvoiceRecord],
    matches: list[MatchResult],
) -> list[ToolResult]:

    decisions = llm_resolve(client, escalations, bank_by_id, invoices_by_id, matches)
    results = []
    for escalation, decision in zip(escalations, decisions):
        bank = bank_by_id[escalation.bank_record_id]
        observed = build_context(escalation, bank, invoices_by_id)
        observed.update(
            {
                "decision": decision.decision,
                "candidate_ids": decision.candidate_ids,
                "confidence": decision.confidence,
                "origin": decision.origin,
                "tool_calls_used": decision.tool_calls_used,
                "input_tokens": decision.input_tokens,
                "output_tokens": decision.output_tokens,
                "latency_ms": decision.latency_ms,
            }
        )
        results.append(ToolResult(resolved=True, reason=decision.reason, observed=observed, decision=decision))
    return results
