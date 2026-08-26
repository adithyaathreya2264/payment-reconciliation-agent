from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

from . import tools
from .controller import AgentController
from .. import loaders, orchestrator, tier_subset_sum
from ..llm_client import MockLLMClient
from ..models import EscalationRecord


def _run_negative_test() -> bool:
    print("Negative test: zero_candidate_rule_tool gate on a wrong-stage escalation")

    wrong_stage_escalation = EscalationRecord(
        bank_record_id="TEST-NEGATIVE-0001",
        stage_reached="tier3_ambiguous",
        reason="synthetic escalation for gate test",
        candidate_subsets=[["INV-1"], ["INV-2"]],
        pool_invoice_ids=["INV-1", "INV-2"],
    )

    calls: list[EscalationRecord] = []
    original = tier_subset_sum.classify_zero_candidate_orphan
    tier_subset_sum.classify_zero_candidate_orphan = lambda e: calls.append(e) or original(e)  # type: ignore
    try:
        result = tools.zero_candidate_rule_tool(wrong_stage_escalation)
    finally:
        tier_subset_sum.classify_zero_candidate_orphan = original

    if calls:
        print("  FAIL: zero_candidate_rule_tool invoked classify_zero_candidate_orphan on a "
              "non-tier3_no_candidates escalation -- the gate did not short-circuit.")
        return False
    if result.resolved:
        print("  FAIL: zero_candidate_rule_tool reported resolved=True for a wrong-stage escalation.")
        return False
    print("  OK: gate short-circuited without calling the classifier, resolved=False.")

    try:
        original(wrong_stage_escalation)
    except ValueError:
        print("  OK: classify_zero_candidate_orphan itself still raises ValueError on a wrong-stage "
              "escalation when called directly -- the underlying contract is untouched.")
        return True
    else:
        print("  FAIL: classify_zero_candidate_orphan did not raise ValueError on a wrong-stage "
              "escalation called directly.")
        return False


def _diff_lists(name: str, old: list, new: list) -> list[str]:
    problems = []
    if len(old) != len(new):
        problems.append(f"{name}: length mismatch old={len(old)} new={len(new)}")
        return problems
    for i, (o, n) in enumerate(zip(old, new)):
        od, nd = asdict(o), asdict(n)
        if od != nd:
            diff_keys = [k for k in od if od.get(k) != nd.get(k)]
            bank_record_id = od.get("bank_record_id", "?")
            problems.append(f"{name}[{i}] (bank_record_id={bank_record_id}): differing fields {diff_keys}")
            for k in diff_keys:
                problems.append(f"    {k}: old={od.get(k)!r} new={nd.get(k)!r}")
    return problems


def _run_one_mode(data_dir: Path, llm_client) -> list[str]:
    invoices = loaders.load_invoices(data_dir / "invoices.csv")
    settlements = loaders.load_settlements(data_dir / "settlement_report.csv")
    bank_records = loaders.load_bank_statement(data_dir / "bank_statement.csv")

    matches_old, escalations_old, llm_decisions_old = orchestrator.run(invoices, settlements, bank_records, llm_client)

    invoices2 = loaders.load_invoices(data_dir / "invoices.csv")
    settlements2 = loaders.load_settlements(data_dir / "settlement_report.csv")
    bank_records2 = loaders.load_bank_statement(data_dir / "bank_statement.csv")

    matches_new, escalations_new, llm_decisions_new, _traces = AgentController().run(
        invoices2, settlements2, bank_records2, llm_client
    )

    problems = []
    problems += _diff_lists("matches", matches_old, matches_new)
    problems += _diff_lists("escalations", escalations_old, escalations_new)
    problems += _diff_lists("llm_decisions", llm_decisions_old, llm_decisions_new)
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="Regression-check AgentController against orchestrator.run().")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--llm-mode", choices=["none", "mock", "both"], default="both")
    args = parser.parse_args()

    ok = _run_negative_test()
    print()

    all_problems: list[str] = []

    if args.llm_mode in ("none", "both"):
        print("Diffing with llm_client=None ...")
        problems = _run_one_mode(args.data_dir, None)
        if problems:
            print(f"  {len(problems)} problem line(s):")
            for p in problems:
                print(f"    {p}")
        else:
            print("  OK: matches/escalations/llm_decisions identical (llm_decisions empty in this mode).")
        all_problems += problems

    if args.llm_mode in ("mock", "both"):
        print("Diffing with llm_client=MockLLMClient() ...")
        problems = _run_one_mode(args.data_dir, MockLLMClient())
        if problems:
            print(f"  {len(problems)} problem line(s):")
            for p in problems:
                print(f"    {p}")
        else:
            print("  OK: matches/escalations/llm_decisions identical.")
        all_problems += problems

    if not ok or all_problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
