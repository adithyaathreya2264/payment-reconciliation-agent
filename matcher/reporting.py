"""Internal stats: match-rate %, per-tier breakdown -- no ground truth needed."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from . import config
from .models import EscalationRecord, LLMDecision, MatchResult


def build_report(matches: list[MatchResult], escalations: list[EscalationRecord], total_bank_records: int) -> dict:
    tier_counts = Counter(m.tier for m in matches)

    invoice_claim_counts: Counter = Counter()
    for m in matches:
        invoice_claim_counts.update(m.matched_invoice_ids)
    cross_claimed = {iid: n for iid, n in invoice_claim_counts.items() if n > 1}

    return {
        "total_bank_records": total_bank_records,
        "resolved": len(matches),
        "resolved_pct": round(len(matches) / total_bank_records, 4) if total_bank_records else 0.0,
        "by_tier": {
            "exact": tier_counts.get("exact", 0),
            "tolerance": tier_counts.get("tolerance", 0),
            "subset_sum": tier_counts.get("subset_sum", 0),
        },
        "unresolved": len(escalations),
        "unresolved_pct": round(len(escalations) / total_bank_records, 4) if total_bank_records else 0.0,
        "escalation_breakdown": dict(Counter(e.stage_reached for e in escalations)),
        "mean_confidence_by_tier": {
            tier: (round(statistics.mean(m.confidence for m in matches if m.tier == tier), 3) if tier_counts.get(tier) else None)
            for tier in ("exact", "tolerance", "subset_sum")
        },
        "cross_claimed_invoice_count": len(cross_claimed),
        "cross_claimed_invoice_ids": cross_claimed,
    }


def print_report(report: dict) -> None:
    print(f"Total bank records: {report['total_bank_records']}")
    print(f"Resolved: {report['resolved']} ({report['resolved_pct']:.1%})")
    print("By tier:")
    for tier, count in report["by_tier"].items():
        conf = report["mean_confidence_by_tier"][tier]
        conf_str = f"mean confidence={conf}" if conf is not None else "mean confidence=n/a"
        print(f"  {tier:<12} {count:>6}  {conf_str}")
    print(f"Unresolved (escalated): {report['unresolved']} ({report['unresolved_pct']:.1%})")
    print("Escalation breakdown:")
    for stage, count in report["escalation_breakdown"].items():
        print(f"  {stage:<24} {count:>6}")
    if report["cross_claimed_invoice_count"]:
        print(
            f"WARNING: {report['cross_claimed_invoice_count']} invoice(s) appear in more than one "
            "resolved match's matched_invoice_ids -- a genuine cross-assignment signal, see grade.py "
            "for whether these are expected (duplicate_amount_collision) or real errors."
        )


def build_llm_report(
    llm_decisions: list[LLMDecision],
    total_bank_records: int,
    input_cost_per_mtok: float = config.LLM_INPUT_COST_PER_MTOK,
    output_cost_per_mtok: float = config.LLM_OUTPUT_COST_PER_MTOK,
) -> dict:
    """Cost/latency/throughput accounting for the escalation tier -- the evidence
    behind the 'AI touches only the genuinely-ambiguous residual' architecture claim.
    Distinguishes origin="rule" (the zero-candidate orphan rule, zero cost/latency by
    construction) from origin="llm" (an actual model call) -- summing/averaging cost
    or latency across both would understate the true per-LLM-call figures and overstate
    how much of the residual actually needed AI.

    input_cost_per_mtok/output_cost_per_mtok default to the Anthropic rates but are
    overridable -- pass config.GROQ_INPUT_COST_PER_MTOK/GROQ_OUTPUT_COST_PER_MTOK when
    the decisions came from GroqLLMClient, since the two providers' pricing differs by
    over 30x and defaulting silently to Anthropic's rate would misreport actual spend."""
    n_escalated = len(llm_decisions)
    rule_decisions = [d for d in llm_decisions if d.origin == "rule"]
    llm_only = [d for d in llm_decisions if d.origin == "llm"]
    n_llm = len(llm_only)

    total_input_tokens = sum(d.input_tokens for d in llm_only)
    total_output_tokens = sum(d.output_tokens for d in llm_only)
    total_latency_ms = sum(d.latency_ms for d in llm_only)
    estimated_cost_usd = (
        total_input_tokens / 1_000_000 * input_cost_per_mtok
        + total_output_tokens / 1_000_000 * output_cost_per_mtok
    )
    return {
        "total_bank_records": total_bank_records,
        "n_escalated": n_escalated,
        "escalated_pct_of_total": round(n_escalated / total_bank_records, 4) if total_bank_records else 0.0,
        "n_resolved_by_rule": len(rule_decisions),
        "n_resolved_by_llm": n_llm,
        "llm_call_pct_of_total": round(n_llm / total_bank_records, 4) if total_bank_records else 0.0,
        "decision_counts": dict(Counter(d.decision for d in llm_decisions)),
        "decision_counts_by_origin": {
            origin: dict(Counter(d.decision for d in llm_decisions if d.origin == origin))
            for origin in ("rule", "llm")
        },
        "mean_tool_calls_used_llm_only": round(sum(d.tool_calls_used for d in llm_only) / n_llm, 2) if n_llm else None,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "estimated_cost_usd": round(estimated_cost_usd, 4),
        "total_latency_ms": round(total_latency_ms, 1),
        "mean_latency_ms_llm_only": round(total_latency_ms / n_llm, 1) if n_llm else None,
    }


def print_llm_report(report: dict) -> None:
    print(f"Escalated: {report['n_escalated']} / {report['total_bank_records']} bank records "
          f"({report['escalated_pct_of_total']:.1%}) -- the rest resolved by the deterministic tiers 1-3.")
    print(f"  resolved by deterministic rule (zero cost/latency): {report['n_resolved_by_rule']}")
    print(f"  resolved by an actual LLM call: {report['n_resolved_by_llm']} "
          f"({report['llm_call_pct_of_total']:.1%} of all bank records)")
    print(f"Decisions overall: {report['decision_counts']}")
    print(f"Decisions by origin: {report['decision_counts_by_origin']}")
    print(f"Mean tool calls per case (LLM-origin only): {report['mean_tool_calls_used_llm_only']}")
    print(f"Tokens (LLM-origin only): {report['total_input_tokens']} in / {report['total_output_tokens']} out "
          f"-- estimated cost ${report['estimated_cost_usd']}")
    print(f"Latency (LLM-origin only): total {report['total_latency_ms']}ms, "
          f"mean {report['mean_latency_ms_llm_only']}ms/case")


def write_llm_decisions_json(llm_decisions: list[LLMDecision], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(d) for d in llm_decisions], f, indent=2)


def write_matches_json(matches: list[MatchResult], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(m) for m in matches], f, indent=2)


def write_escalations_json(escalations: list[EscalationRecord], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(e) for e in escalations], f, indent=2)


def write_report_json(report: dict, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
