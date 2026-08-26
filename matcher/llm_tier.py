from __future__ import annotations

import json
from collections import defaultdict
from datetime import date

from . import config
from .llm_client import ContentBlock, ContextTooLargeError, LLMClient
from .models import BankRecordRow, EscalationRecord, InvoiceRecord, LLMDecision, MatchResult

SYSTEM_PROMPT = """You are the final escalation tier in a bank-to-invoice reconciliation \
pipeline. Deterministic arithmetic tiers (exact UTR+amount+date match, tolerance-band \
match, subset-sum search) have already run and could NOT confidently resolve this one \
bank record -- that is why you are being asked.

Two kinds of cases reach you:
- "tier3_ambiguous": a real settlement exists, but 2+ different combinations of \
invoices sum to the bank amount within tolerance, and arithmetic alone can't tell \
which is correct. You'll be given "invoice_pool" (every distinct invoice involved, \
each listed once with its full details) and "candidate_subsets" (each competing \
combination, as a list of invoice IDs referencing invoice_pool -- look up each ID's \
details there).
- "tier3_no_candidates": no combination of invoices in the search pool ("candidate_pool") \
summed to the bank amount within tolerance at all. Most such cases are genuine orphan \
payments with no real invoice behind them -- but occasionally the true match exists \
outside the pool the deterministic search considered, or a narration hint (a name, a \
partial reference) points at a specific candidate worth a second look.

You have one optional tool, check_counterparty_pattern, which looks up REAL data from \
already-resolved settlements (never fabricated) -- how many invoices a given \
counterparty has typically been batched with before. Use it only if it would actually \
help; you do not have to.

When you are done reasoning, call submit_decision exactly once. Rules for that call:
- "match": you are reasonably confident a SPECIFIC set of invoice IDs explains this \
bank record. Being merely "plausible" is not enough -- you must be able to name the \
IDs and be genuinely more confident in that specific answer than the alternatives.
- "no_match": you are reasonably confident this bank record has NO corresponding \
invoice at all (a genuine exception, e.g. an orphan payment).
- "insufficient_evidence": you cannot confidently tell -- the case is genuinely \
ambiguous or under-evidenced even after reasoning about it. THIS IS A CORRECT AND \
EXPECTED OUTCOME, not a failure. Prefer it over a low-confidence guess. A human \
reconciler who can't tell should say so, not pick one arbitrarily.

Set confidence to your genuine belief (0.0-1.0) in the decision you submitted, not just \
in "something being present" -- a low-confidence "match" is often better expressed as \
insufficient_evidence instead. Keep reason concise (1-3 sentences) and specific to the \
evidence you actually used."""

CHECK_COUNTERPARTY_TOOL = {
    "name": "check_counterparty_pattern",
    "description": (
        "Look up how a counterparty has historically been batched in already-resolved "
        "settlements (number of settlements observed, batch-size range and mean). "
        "Real computed data, not fabricated. Optional -- use only if it would help."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "counterparty_name": {
                "type": "string",
                "description": "Exact counterparty_canonical name to look up.",
            }
        },
        "required": ["counterparty_name"],
        "additionalProperties": False,
    },
}

SUBMIT_DECISION_TOOL = {
    "name": "submit_decision",
    "description": "Submit your final decision for this bank record. Call exactly once when done reasoning.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["match", "no_match", "insufficient_evidence"],
            },
            "candidate_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Invoice IDs that correctly explain this bank record. Empty if no_match or insufficient_evidence.",
            },
            "confidence": {
                "type": "number",
                "description": "Genuine confidence in this decision, 0.0-1.0.",
            },
            "reason": {
                "type": "string",
                "description": "Concise justification (1-3 sentences) specific to the evidence used.",
            },
        },
        "required": ["decision", "candidate_ids", "confidence", "reason"],
        "additionalProperties": False,
    },
}


def build_counterparty_stats(
    matches: list[MatchResult], invoices_by_id: dict[str, InvoiceRecord]
) -> dict[str, dict]:

    observations: dict[str, list[int]] = defaultdict(list)
    for m in matches:
        if m.tier not in ("exact", "tolerance"):
            continue
        batch_size = len(m.matched_invoice_ids)
        counterparties = {
            invoices_by_id[iid].counterparty_canonical
            for iid in m.matched_invoice_ids
            if iid in invoices_by_id
        }
        for cp in counterparties:
            observations[cp].append(batch_size)

    stats = {}
    for cp, sizes in observations.items():
        stats[cp] = {
            "num_settlements_observed": len(sizes),
            "mean_batch_size": round(sum(sizes) / len(sizes), 1),
            "min_batch_size": min(sizes),
            "max_batch_size": max(sizes),
        }
    return stats


def _lookup_counterparty(name: str, stats: dict[str, dict]) -> str:
    s = stats.get(name)
    if s is None:
        return f"No historical settlement data found for counterparty '{name}'."
    return (
        f"'{name}' has appeared in {s['num_settlements_observed']} resolved settlements; "
        f"batch sizes ranged {s['min_batch_size']}-{s['max_batch_size']}, "
        f"mean {s['mean_batch_size']}."
    )


def _invoice_dict(inv: InvoiceRecord) -> dict:
    return {
        "invoice_id": inv.invoice_id,
        "counterparty_canonical": inv.counterparty_canonical,
        "expected_amount": inv.expected_amount,
        "expected_date": inv.expected_date.isoformat(),
        "payment_method": inv.payment_method,
    }


def _bank_dict(bank: BankRecordRow) -> dict:
    return {
        "bank_record_id": bank.bank_record_id,
        "amount": bank.amount,
        "date": bank.date.isoformat(),
        "narration": bank.narration,
        "bank_name": bank.bank_name,
    }


def build_context(
    escalation: EscalationRecord, bank: BankRecordRow, invoices_by_id: dict[str, InvoiceRecord]
) -> dict:
    base = {"stage": escalation.stage_reached, "bank_record": _bank_dict(bank)}
    if escalation.stage_reached == "tier3_ambiguous":
        
        subsets = escalation.candidate_subsets or []
        unique_ids = sorted({iid for subset in subsets for iid in subset if iid in invoices_by_id})
        base["invoice_pool"] = [_invoice_dict(invoices_by_id[iid]) for iid in unique_ids]
        base["candidate_subsets"] = [
            [iid for iid in subset if iid in invoices_by_id] for subset in subsets
        ]
    else:
        base["candidate_pool"] = [
            _invoice_dict(invoices_by_id[iid])
            for iid in escalation.pool_invoice_ids
            if iid in invoices_by_id
        ]
    return base


def _blocks_to_message_content(blocks: list[ContentBlock]) -> list[dict]:
    out = []
    for b in blocks:
        if b.type == "text":
            out.append({"type": "text", "text": b.text or ""})
        elif b.type == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return out


def resolve_one(
    client: LLMClient,
    escalation: EscalationRecord,
    bank: BankRecordRow,
    invoices_by_id: dict[str, InvoiceRecord],
    counterparty_stats: dict[str, dict],
) -> LLMDecision:
    if escalation.stage_reached == "tier3_pool_too_large":
        return LLMDecision(
            bank_record_id=escalation.bank_record_id,
            decision="insufficient_evidence",
            candidate_ids=[],
            confidence=0.0,
            reason=f"Pool size {len(escalation.pool_invoice_ids)} exceeds the review cap; not sent to the LLM.",
            tool_calls_used=0,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0.0,
        )

    context = build_context(escalation, bank, invoices_by_id)
    messages = [{"role": "user", "content": json.dumps(context, indent=2)}]
    tools = [CHECK_COUNTERPARTY_TOOL, SUBMIT_DECISION_TOOL]

    total_input_tokens = 0
    total_output_tokens = 0
    total_latency_ms = 0.0
    tool_calls_used = 0

    for turn in range(config.LLM_MAX_TURNS):
        forced_final = turn == config.LLM_MAX_TURNS - 1
        tool_choice = {"type": "tool", "name": "submit_decision"} if forced_final else None

        try:
            response = client.create_message(
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                max_tokens=config.LLM_MAX_TOKENS,
            )
        except ContextTooLargeError as exc:

            return LLMDecision(
                bank_record_id=escalation.bank_record_id,
                decision="insufficient_evidence",
                candidate_ids=[],
                confidence=0.0,
                reason=(
                    f"context_exceeds_provider_limit: estimated request size "
                    f"{exc.estimated_tokens} exceeds the provider's {exc.limit}-token "
                    f"ceiling; not sent to the LLM."
                ),
                tool_calls_used=tool_calls_used,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                latency_ms=total_latency_ms,
                origin="rule",
            )
        total_input_tokens += response.input_tokens
        total_output_tokens += response.output_tokens
        total_latency_ms += response.latency_ms
        messages.append({"role": "assistant", "content": _blocks_to_message_content(response.content)})

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        submit_block = next((b for b in tool_use_blocks if b.name == "submit_decision"), None)
        if submit_block is not None:
            tool_calls_used += len(tool_use_blocks)
            inp = submit_block.input or {}
            return LLMDecision(
                bank_record_id=escalation.bank_record_id,
                decision=inp.get("decision", "insufficient_evidence"),
                candidate_ids=list(inp.get("candidate_ids", [])),
                confidence=float(inp.get("confidence", 0.0)),
                reason=str(inp.get("reason", "")),
                tool_calls_used=tool_calls_used,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                latency_ms=total_latency_ms,
            )

        if not tool_use_blocks:
            break

        tool_results = []
        for b in tool_use_blocks:
            tool_calls_used += 1
            if b.name == "check_counterparty_pattern":
                name = (b.input or {}).get("counterparty_name", "")
                result_text = _lookup_counterparty(name, counterparty_stats)
            else:
                result_text = f"Unknown tool '{b.name}'."
            tool_results.append({"type": "tool_result", "tool_use_id": b.id, "content": result_text})
        messages.append({"role": "user", "content": tool_results})

    return LLMDecision(
        bank_record_id=escalation.bank_record_id,
        decision="insufficient_evidence",
        candidate_ids=[],
        confidence=0.0,
        reason="LLM did not submit a decision within the turn budget.",
        tool_calls_used=tool_calls_used,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        latency_ms=total_latency_ms,
    )


def resolve(
    client: LLMClient,
    escalations: list[EscalationRecord],
    bank_by_id: dict[str, BankRecordRow],
    invoices_by_id: dict[str, InvoiceRecord],
    matches: list[MatchResult],
) -> list[LLMDecision]:
    counterparty_stats = build_counterparty_stats(matches, invoices_by_id)
    return [
        resolve_one(client, e, bank_by_id[e.bank_record_id], invoices_by_id, counterparty_stats)
        for e in escalations
    ]
