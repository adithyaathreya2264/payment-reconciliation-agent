"""CLI entry point for the agent-wrapped pipeline: python -m matcher.run_agent
--data-dir data/ [--llm | --llm-groq | --llm-mock]

Mirrors matcher/run.py but drives AgentController instead of orchestrator.run(),
writing to a separate output directory (default matcher_output_agent/) and
additionally dumping decision_traces.json -- one DecisionTrace per bank record,
the full tier-by-tier path with reasons and observed data. Does not touch
matcher/run.py or matcher/orchestrator.py; see matcher/agent/regression_check.py
for the proof that this path produces identical matches/escalations/llm_decisions.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from . import config, loaders, reporting
from .agent.controller import AgentController
from .agent.states import DecisionTrace, Stage, Transition
from .llm_client import LLMClient, MockLLMClient


def _trace_to_dict(trace: DecisionTrace) -> dict:
    return {
        "bank_record_id": trace.bank_record_id,
        "transitions": [
            {
                "from_stage": t.from_stage.value,
                "to_stage": t.to_stage.value,
                "reason": t.reason,
                "observed": t.observed,
            }
            for t in trace.transitions
        ],
        "final_stage": trace.final_stage.value if trace.final_stage is not None else None,
        "final_decision": trace.final_decision,
    }


def write_decision_traces_json(traces: dict[str, DecisionTrace], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump({bid: _trace_to_dict(t) for bid, t in traces.items()}, f, indent=2)


def run(
    data_dir: Path,
    out_dir: Path,
    llm_client: LLMClient | None = None,
    llm_input_cost_per_mtok: float = config.LLM_INPUT_COST_PER_MTOK,
    llm_output_cost_per_mtok: float = config.LLM_OUTPUT_COST_PER_MTOK,
) -> dict:
    invoices = loaders.load_invoices(data_dir / "invoices.csv")
    settlements = loaders.load_settlements(data_dir / "settlement_report.csv")
    bank_records = loaders.load_bank_statement(data_dir / "bank_statement.csv")

    matches, escalations, llm_decisions, traces = AgentController().run(invoices, settlements, bank_records, llm_client)
    report = reporting.build_report(matches, escalations, len(bank_records))

    out_dir.mkdir(parents=True, exist_ok=True)
    reporting.write_matches_json(matches, out_dir / "matches.json")
    reporting.write_escalations_json(escalations, out_dir / "escalations.json")
    reporting.write_report_json(report, out_dir / "report.json")
    write_decision_traces_json(traces, out_dir / "decision_traces.json")
    reporting.print_report(report)

    if llm_client is not None:
        reporting.write_llm_decisions_json(llm_decisions, out_dir / "llm_decisions.json")
        llm_report = reporting.build_llm_report(
            llm_decisions, len(bank_records), llm_input_cost_per_mtok, llm_output_cost_per_mtok
        )
        reporting.write_report_json(llm_report, out_dir / "llm_report.json")
        print()
        reporting.print_llm_report(llm_report)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the agent-wrapped reconciliation matcher.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out-dir", type=Path, default=Path("matcher_output_agent"))
    llm_group = parser.add_mutually_exclusive_group()
    llm_group.add_argument("--llm", action="store_true", help="escalate unresolved residual to the real Anthropic API (costs money)")
    llm_group.add_argument("--llm-groq", action="store_true", help="escalate unresolved residual to the real Groq API (costs money, subject to TPM throttling)")
    llm_group.add_argument("--llm-mock", action="store_true", help="escalate unresolved residual to a zero-cost mock LLM client (plumbing test only)")
    args = parser.parse_args()

    llm_client: LLMClient | None = None
    input_cost = config.LLM_INPUT_COST_PER_MTOK
    output_cost = config.LLM_OUTPUT_COST_PER_MTOK
    if args.llm:
        from .llm_client import AnthropicLLMClient

        llm_client = AnthropicLLMClient(config.LLM_MODEL)
    elif args.llm_groq:
        from .llm_client import GroqLLMClient

        llm_client = GroqLLMClient(config.GROQ_MODEL, config.GROQ_TPM_LIMIT, account_tpm_limit=config.GROQ_ACCOUNT_TPM_LIMIT)
        input_cost = config.GROQ_INPUT_COST_PER_MTOK
        output_cost = config.GROQ_OUTPUT_COST_PER_MTOK
    elif args.llm_mock:
        llm_client = MockLLMClient()

    run(args.data_dir, args.out_dir, llm_client, input_cost, output_cost)


if __name__ == "__main__":
    main()
