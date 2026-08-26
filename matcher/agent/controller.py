from __future__ import annotations

from dataclasses import asdict

from . import tools
from .states import DecisionTrace, Stage, Transition
from ..llm_client import LLMClient
from ..models import BankRecordRow, EscalationRecord, InvoiceRecord, LLMDecision, MatchResult, SettlementRecord


class AgentController:
    def run(
        self,
        invoices: list[InvoiceRecord],
        settlements: list[SettlementRecord],
        bank_records: list[BankRecordRow],
        llm_client: LLMClient | None = None,
    ) -> tuple[list[MatchResult], list[EscalationRecord], list[LLMDecision], dict[str, DecisionTrace]]:
        self._invoices_by_id = {i.invoice_id: i for i in invoices}
        self._unmatched_settlements = {s.settlement_id: s for s in settlements}
        self._unmatched_invoices = dict(self._invoices_by_id)

        self._matches: list[MatchResult] = []
        self._traces: dict[str, DecisionTrace] = {
            b.bank_record_id: DecisionTrace(bank_record_id=b.bank_record_id) for b in bank_records
        }

        still_pending = self._run_exact(bank_records)
        still_pending = self._run_tolerance(still_pending)
        escalations = self._run_subset_sum(still_pending)

        llm_decisions: list[LLMDecision] = []
        if llm_client is not None and escalations:
            rule_resolved, remaining_escalations = self._run_zero_candidate_rule(escalations)

            llm_resolved: list[LLMDecision] = []
            if remaining_escalations:
                bank_by_id = {b.bank_record_id: b for b in bank_records}
                llm_resolved = self._run_llm_escalation(
                    llm_client, remaining_escalations, bank_by_id, self._invoices_by_id, self._matches
                )

            llm_decisions = rule_resolved + llm_resolved

        return self._matches, escalations, llm_decisions, self._traces

    def _consume(self, result: MatchResult) -> None:
        self._matches.append(result)
        if result.settlement_id:
            self._unmatched_settlements.pop(result.settlement_id, None)
        for iid in result.matched_invoice_ids:
            self._unmatched_invoices.pop(iid, None)

    def _resolve_trace(self, bank_record_id: str, match: MatchResult) -> None:
        trace = self._traces[bank_record_id]
        trace.final_stage = Stage.RESOLVED
        trace.final_decision = {"kind": "match", **asdict(match)}

    def _resolve_trace_llm(self, bank_record_id: str, decision: LLMDecision) -> None:
        trace = self._traces[bank_record_id]
        trace.final_stage = Stage.RESOLVED
        trace.final_decision = {"kind": "llm_decision", **asdict(decision)}

    def _run_exact(self, bank_list: list[BankRecordRow]) -> list[BankRecordRow]:
        still_pending = []
        for b in bank_list:
            result = tools.exact_tool(b, self._unmatched_settlements, self._invoices_by_id)
            if result.resolved:
                self._consume(result.decision)
                self._traces[b.bank_record_id].transitions.append(
                    Transition(from_stage=Stage.EXACT, to_stage=Stage.RESOLVED, reason=result.reason, observed=result.observed)
                )
                self._resolve_trace(b.bank_record_id, result.decision)
            else:
                still_pending.append(b)
                self._traces[b.bank_record_id].transitions.append(
                    Transition(from_stage=Stage.EXACT, to_stage=Stage.TOLERANCE, reason=result.reason, observed=result.observed)
                )
        return still_pending

    def _run_tolerance(self, bank_list: list[BankRecordRow]) -> list[BankRecordRow]:
        still_pending = []
        for b in bank_list:
            result = tools.tolerance_tool(b, self._unmatched_settlements, self._invoices_by_id)
            if result.resolved:
                self._consume(result.decision)
                self._traces[b.bank_record_id].transitions.append(
                    Transition(from_stage=Stage.TOLERANCE, to_stage=Stage.RESOLVED, reason=result.reason, observed=result.observed)
                )
                self._resolve_trace(b.bank_record_id, result.decision)
            else:
                still_pending.append(b)
                self._traces[b.bank_record_id].transitions.append(
                    Transition(from_stage=Stage.TOLERANCE, to_stage=Stage.SUBSET_SUM, reason=result.reason, observed=result.observed)
                )
        return still_pending

    def _run_subset_sum(self, bank_list: list[BankRecordRow]) -> list[EscalationRecord]:
        escalations: list[EscalationRecord] = []
        for b in bank_list:
            result = tools.subset_sum_tool(b, self._unmatched_invoices)
            if result.resolved:
                self._consume(result.decision)
                self._traces[b.bank_record_id].transitions.append(
                    Transition(from_stage=Stage.SUBSET_SUM, to_stage=Stage.RESOLVED, reason=result.reason, observed=result.observed)
                )
                self._resolve_trace(b.bank_record_id, result.decision)
            else:
                escalation = result.decision
                escalations.append(escalation)
                next_stage = Stage.ZERO_CANDIDATE_RULE if escalation.stage_reached == "tier3_no_candidates" else Stage.LLM_ESCALATION
                self._traces[b.bank_record_id].transitions.append(
                    Transition(from_stage=Stage.SUBSET_SUM, to_stage=next_stage, reason=result.reason, observed=result.observed)
                )
        return escalations

    def _run_zero_candidate_rule(
        self, escalations: list[EscalationRecord]
    ) -> tuple[list[LLMDecision], list[EscalationRecord]]:
        rule_resolved: list[LLMDecision] = []
        remaining_escalations: list[EscalationRecord] = []
        for e in escalations:
            if e.stage_reached == "tier3_no_candidates":
                result = tools.zero_candidate_rule_tool(e)
                decision = result.decision
                rule_resolved.append(decision)
                self._traces[e.bank_record_id].transitions.append(
                    Transition(from_stage=Stage.ZERO_CANDIDATE_RULE, to_stage=Stage.RESOLVED, reason=result.reason, observed=result.observed)
                )
                self._resolve_trace_llm(e.bank_record_id, decision)
            else:
                remaining_escalations.append(e)
        return rule_resolved, remaining_escalations

    def _run_llm_escalation(
        self,
        client: LLMClient,
        escalations: list[EscalationRecord],
        bank_by_id: dict[str, BankRecordRow],
        invoices_by_id: dict[str, InvoiceRecord],
        matches: list[MatchResult],
    ) -> list[LLMDecision]:
        tool_results = tools.llm_escalation_tool(client, escalations, bank_by_id, invoices_by_id, matches)
        decisions: list[LLMDecision] = []
        for escalation, result in zip(escalations, tool_results):
            decision = result.decision
            decisions.append(decision)
            self._traces[escalation.bank_record_id].transitions.append(
                Transition(from_stage=Stage.LLM_ESCALATION, to_stage=Stage.RESOLVED, reason=result.reason, observed=result.observed)
            )
            self._resolve_trace_llm(escalation.bank_record_id, decision)
        return decisions
