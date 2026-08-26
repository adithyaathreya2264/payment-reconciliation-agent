from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def grade_entries(decisions: list[dict], gt_entries: list[dict], escalations: list[dict]) -> list[dict]:
    entries_by_bank = {e["bank_record_id"]: e for e in gt_entries if e["bank_record_id"]}
    stage_by_bank = {e["bank_record_id"]: e["stage_reached"] for e in escalations}

    results = []
    for d in decisions:
        e = entries_by_bank.get(d["bank_record_id"])
        if e is None:
            continue
        true_ids = set(e["true_match_ids"])
        candidate_ids = set(d["candidate_ids"])
        decision = d["decision"]

        if not true_ids:
            
            if decision == "no_match":
                outcome = "correct_no_match"
            elif decision == "insufficient_evidence":
                outcome = "honest_defer_no_match_case"
            else:
                outcome = "hallucinated_match"
        else:
            
            if decision == "match" and candidate_ids == true_ids:
                outcome = "correct_match"
            elif decision == "insufficient_evidence":
                outcome = "honest_defer_match_case"
            elif decision == "match":
                outcome = "wrong_match"
            else:  
                outcome = "incorrect_no_match"

        results.append({
            "bank_record_id": d["bank_record_id"],
            "outcome": outcome,
            "stage_reached": stage_by_bank.get(d["bank_record_id"]),
            "failure_mode": e["failure_mode_injected"],
            "confidence": d["confidence"],
            "origin": d.get("origin", "llm"),
        })
    return results


def origin_breakdown(graded: list[dict]) -> dict:
    
    breakdown = {}
    for origin in ("rule", "llm"):
        subset = [g for g in graded if g["origin"] == origin]
        correct = sum(
            1 for g in subset
            if g["outcome"] in ("correct_no_match", "correct_match")
        )
        breakdown[origin] = {
            "n": len(subset),
            "n_correct": correct,
            "accuracy": round(correct / len(subset), 3) if subset else None,
            "outcome_counts": dict(Counter(g["outcome"] for g in subset)),
        }
    return breakdown


def precision_recall(graded: list[dict]) -> dict:
    correct_match = sum(1 for g in graded if g["outcome"] == "correct_match")
    wrong_match = sum(1 for g in graded if g["outcome"] == "wrong_match")
    hallucinated = sum(1 for g in graded if g["outcome"] == "hallucinated_match")
    fp = wrong_match + hallucinated

    
    fn = sum(1 for g in graded if g["outcome"] in ("honest_defer_match_case", "incorrect_no_match"))

    precision = round(correct_match / (correct_match + fp), 3) if (correct_match + fp) else None
    recall = round(correct_match / (correct_match + fn), 3) if (correct_match + fn) else None
    return {"tp": correct_match, "fp": fp, "fn": fn, "precision": precision, "recall": recall}


def headline_breakdowns(graded: list[dict]) -> dict:
    ambiguous = [g for g in graded if g["stage_reached"] == "tier3_ambiguous"]
    no_candidates = [g for g in graded if g["stage_reached"] == "tier3_no_candidates"]

    return {
        "tier3_ambiguous": {
            "total": len(ambiguous),
            "correct_match": sum(1 for g in ambiguous if g["outcome"] == "correct_match"),
            "honest_defer": sum(1 for g in ambiguous if g["outcome"] == "honest_defer_match_case"),
            "wrong_match": sum(1 for g in ambiguous if g["outcome"] == "wrong_match"),
            "incorrect_no_match": sum(1 for g in ambiguous if g["outcome"] == "incorrect_no_match"),
        },
        "tier3_no_candidates": {
            "total": len(no_candidates),
            "correct_no_match": sum(1 for g in no_candidates if g["outcome"] == "correct_no_match"),
            "honest_defer": sum(1 for g in no_candidates if g["outcome"] in ("honest_defer_no_match_case", "honest_defer_match_case")),
            "hallucinated_match": sum(1 for g in no_candidates if g["outcome"] == "hallucinated_match"),
            "correct_match": sum(1 for g in no_candidates if g["outcome"] == "correct_match"),
        },
    }


def run_grade(matcher_output_dir: Path, ground_truth_path: Path) -> dict:
    decisions = _load_json(matcher_output_dir / "llm_decisions.json")
    escalations = _load_json(matcher_output_dir / "escalations.json")
    gt = _load_json(ground_truth_path)

    graded = grade_entries(decisions, gt["entries"], escalations)
    return {
        "n_graded": len(graded),
        "outcome_counts": dict(Counter(g["outcome"] for g in graded)),
        "origin_breakdown": origin_breakdown(graded),
        "precision_recall": precision_recall(graded),
        "headline_breakdowns": headline_breakdowns(graded),
        "note_construction_method": (
            "construction_method (found/nudged) does not exist in ground_truth.json -- "
            "batching.py never implemented the nudge fallback described in the original "
            "spec, so every ambiguous_subset_sum case is 'found'. This comparison is "
            "moot on this dataset, not omitted by oversight."
        ),
    }


def print_grade_report(report: dict) -> None:
    print(f"Graded {report['n_graded']} LLM decisions.")
    print(f"Outcome counts: {report['outcome_counts']}")

    print("\n Origin breakdown (rule vs. actual LLM call) ")
    for origin, stats in report["origin_breakdown"].items():
        print(f"  {origin:<6} n={stats['n']:>4}  correct={stats['n_correct']:>4}  "
              f"accuracy={stats['accuracy']}  outcomes={stats['outcome_counts']}")

    pr = report["precision_recall"]
    print(f"Precision/recall (match decisions): tp={pr['tp']} fp={pr['fp']} fn={pr['fn']} "
          f"precision={pr['precision']} recall={pr['recall']}")

    hb = report["headline_breakdowns"]
    print("\n tier3_ambiguous breakdown (competing-subset disambiguation) ")
    for k, v in hb["tier3_ambiguous"].items():
        print(f"  {k:<20} {v}")
    print("\n tier3_no_candidates breakdown (mostly orphan_payment) ")
    for k, v in hb["tier3_no_candidates"].items():
        print(f"  {k:<20} {v}")

    print(f"\nNote: {report['note_construction_method']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade LLM tier decisions against ground_truth.json.")
    parser.add_argument("--matcher-output", type=Path, default=Path("matcher_output"))
    parser.add_argument("--ground-truth", type=Path, default=Path("data/answer_key/ground_truth.json"))
    args = parser.parse_args()

    report = run_grade(args.matcher_output, args.ground_truth)
    print_grade_report(report)
    with open(args.matcher_output / "llm_grade_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    main()
