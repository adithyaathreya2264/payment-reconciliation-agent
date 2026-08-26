from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import config, grade, grade_llm, loaders, orchestrator, reporting
from .llm_client import LLMClient, MockLLMClient


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

    matches, escalations, llm_decisions = orchestrator.run(invoices, settlements, bank_records, llm_client)
    report = reporting.build_report(matches, escalations, len(bank_records))

    out_dir.mkdir(parents=True, exist_ok=True)
    reporting.write_matches_json(matches, out_dir / "matches.json")
    reporting.write_escalations_json(escalations, out_dir / "escalations.json")
    reporting.write_report_json(report, out_dir / "report.json")
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
    parser = argparse.ArgumentParser(description="Run the deterministic reconciliation matcher.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out-dir", type=Path, default=Path("matcher_output"))
    parser.add_argument("--grade", action="store_true", help="also grade output against data/answer_key/ground_truth.json")
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

    if args.grade:
        print("\n" + "=" * 60)
        print("GRADING against ground truth")
        print("=" * 60)
        grade_report = grade.run_grade(args.out_dir, args.data_dir / "answer_key" / "ground_truth.json")
        grade.print_grade_report(grade_report)
        with open(args.out_dir / "grade_report.json", "w", encoding="utf-8") as f:
            json.dump(grade_report, f, indent=2)

        if llm_client is not None:
            print("\n" + "=" * 60)
            print("GRADING LLM tier against ground truth")
            print("=" * 60)
            llm_grade_report = grade_llm.run_grade(args.out_dir, args.data_dir / "answer_key" / "ground_truth.json")
            grade_llm.print_grade_report(llm_grade_report)
            with open(args.out_dir / "llm_grade_report.json", "w", encoding="utf-8") as f:
                json.dump(llm_grade_report, f, indent=2)


if __name__ == "__main__":
    main()
