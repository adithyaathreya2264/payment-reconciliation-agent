"""Compares matcher output to data/answer_key/ground_truth.json.

This is the only matcher module (besides its own CLI) allowed to read
data/answer_key/ -- the matcher proper never sees it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_DETERMINISTIC_TIERS = {"exact", "tolerance", "subset_sum"}


def _load_ground_truth(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_matches(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_escalations(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def grade_generic_entries(entries: list[dict], matches_by_bank: dict, escalations_by_bank: dict) -> list[dict]:
    """Grades every ground-truth entry with a bank_record_id, EXCEPT
    ambiguous_subset_sum entries (graded separately -- see grade_ambiguous_subset_sum,
    since their semantics don't fit the plain TP/FP/FN bucket)."""
    results = []
    for e in entries:
        if e["bank_record_id"] is None:
            continue
        if e["failure_mode_injected"] == "ambiguous_subset_sum":
            continue

        expected_tier = e["identifiable_at_tier"]
        true_ids = set(e["true_match_ids"])
        match = matches_by_bank.get(e["bank_record_id"])
        escalation = escalations_by_bank.get(e["bank_record_id"])

        if match is not None:
            matched_ids = set(match["matched_invoice_ids"])
            correct_ids = matched_ids == true_ids
            if expected_tier not in _DETERMINISTIC_TIERS:
                # Two very different findings live here: a trusted signal (UTR/order_id)
                # resolving something ground truth expected to need fuzzy/LLM work is
                # informative, not alarming, AS LONG AS the ids are actually correct
                # (e.g. entity_name_variant resolved via UTR, ignoring the distorted
                # display name entirely). If the ids are WRONG, this is a genuine false
                # positive: the matcher confidently claimed a match for a case ground
                # truth says has none (e.g. hallucinating invoices for an orphan_payment).
                outcome = "overconfident_but_correct" if correct_ids else "overconfident_and_wrong"
            elif match["tier"] == expected_tier and correct_ids:
                outcome = "true_positive"
            elif correct_ids:
                outcome = "resolved_wrong_tier_correct_ids"
            else:
                outcome = "false_positive_wrong_ids"
            results.append({"bank_record_id": e["bank_record_id"], "outcome": outcome,
                             "expected_tier": expected_tier, "got_tier": match["tier"],
                             "failure_mode": e["failure_mode_injected"]})
        else:
            if expected_tier in _DETERMINISTIC_TIERS:
                outcome = "false_negative"
            else:
                outcome = "true_negative_correctly_deferred" if escalation else "missing_from_output"
            results.append({"bank_record_id": e["bank_record_id"], "outcome": outcome,
                             "expected_tier": expected_tier, "got_tier": None,
                             "failure_mode": e["failure_mode_injected"]})
    return results


def precision_recall_by_tier(graded_entries: list[dict]) -> dict:
    result = {}
    for tier in ("exact", "tolerance", "subset_sum"):
        tp = sum(1 for g in graded_entries if g["outcome"] == "true_positive" and g["expected_tier"] == tier)
        fp = sum(1 for g in graded_entries if g["outcome"] == "false_positive_wrong_ids" and g["got_tier"] == tier)
        fn = sum(1 for g in graded_entries if g["outcome"] == "false_negative" and g["expected_tier"] == tier)
        precision = round(tp / (tp + fp), 3) if (tp + fp) else None
        recall = round(tp / (tp + fn), 3) if (tp + fn) else None
        result[tier] = {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall}
    return result


def grade_ambiguous_subset_sum(entries: list[dict], matches_by_bank: dict, escalations_by_bank: dict) -> dict:
    total = 0
    correctly_flagged = 0
    incorrectly_resolved = 0  # subset_sum tier, but uniquely -- ground truth says ambiguous
    resolved_by_stronger_signal = 0  # resolved via exact/tolerance -- shouldn't happen post-fix
    missed_other = 0
    examples: list[dict] = []

    for e in entries:
        if e["failure_mode_injected"] != "ambiguous_subset_sum":
            continue
        total += 1
        bank_id = e["bank_record_id"]
        match = matches_by_bank.get(bank_id)
        escalation = escalations_by_bank.get(bank_id)
        true_ids = set(e["true_match_ids"])

        if match is not None and match["tier"] in ("exact", "tolerance"):
            resolved_by_stronger_signal += 1
            examples.append({"bank_record_id": bank_id, "outcome": "resolved_by_stronger_signal_than_intended",
                              "got_tier": match["tier"], "should_be_resolvable_by_tier": e["identifiable_at_tier"]})
        elif match is not None and match["tier"] == "subset_sum":
            incorrectly_resolved += 1
            examples.append({"bank_record_id": bank_id, "outcome": "ambiguous_incorrectly_resolved",
                              "got_tier": match["tier"], "matched_invoice_ids": match["matched_invoice_ids"],
                              "true_match_ids": e["true_match_ids"]})
        elif escalation is not None and escalation["stage_reached"] == "tier3_ambiguous":
            candidate_sets = [set(s) for s in (escalation["candidate_subsets"] or [])]
            if true_ids in candidate_sets:
                correctly_flagged += 1
            else:
                missed_other += 1
                examples.append({"bank_record_id": bank_id, "outcome": "ambiguous_flagged_but_true_subset_absent",
                                  "candidate_subsets": escalation["candidate_subsets"],
                                  "true_match_ids": e["true_match_ids"]})
        else:
            missed_other += 1
            stage = escalation["stage_reached"] if escalation else "missing_from_output"
            examples.append({"bank_record_id": bank_id, "outcome": "ambiguous_missed_other", "stage": stage})

    rate_resolved_by_stronger_signal = round(resolved_by_stronger_signal / total, 3) if total else None
    return {
        "ambiguous_subset_sum_total": total,
        "ambiguous_correctly_flagged": correctly_flagged,
        "ambiguous_incorrectly_resolved": incorrectly_resolved,
        "resolved_by_stronger_signal_than_intended": resolved_by_stronger_signal,
        "resolved_by_stronger_signal_rate": rate_resolved_by_stronger_signal,
        "ambiguous_missed_other": missed_other,
        "examples": examples[:10],  # cap for readability; full data is in matches.json/escalations.json
    }


def grade_orphan_invoices(entries: list[dict], matches: list[dict]) -> dict:
    all_matched_ids: set[str] = set()
    for m in matches:
        all_matched_ids.update(m["matched_invoice_ids"])

    false_matches = []
    total = 0
    for e in entries:
        if e["failure_mode_injected"] != "orphan_invoice":
            continue
        total += 1
        invoice_id = e["invoice_ids"][0]
        if invoice_id in all_matched_ids:
            false_matches.append(invoice_id)

    return {"orphan_invoice_total": total, "orphan_invoice_false_matches": false_matches}


def grade_duplicate_collisions(collision_groups: list[dict], entries: list[dict], matches_by_bank: dict) -> dict:
    invoice_to_entry: dict[str, dict] = {}
    for e in entries:
        for iid in e["true_match_ids"]:
            invoice_to_entry[iid] = e

    results = []
    for group in collision_groups:
        ids = group["invoice_ids"]
        if len(ids) != 2:
            continue
        id1, id2 = ids
        entry1 = invoice_to_entry.get(id1)
        entry2 = invoice_to_entry.get(id2)
        if entry1 is None or entry2 is None:
            continue  # one side is itself an orphan_invoice with no bank-side entry
        m1 = matches_by_bank.get(entry1["bank_record_id"])
        m2 = matches_by_bank.get(entry2["bank_record_id"])
        cross_assigned = False
        if m1 and id2 in m1["matched_invoice_ids"] and id1 not in m1["matched_invoice_ids"]:
            cross_assigned = True
        if m2 and id1 in m2["matched_invoice_ids"] and id2 not in m2["matched_invoice_ids"]:
            cross_assigned = True
        results.append({
            "collision_group_id": group["collision_group_id"],
            "cross_assigned": cross_assigned,
            "bank_record_1_tier": m1["tier"] if m1 else None,
            "bank_record_2_tier": m2["tier"] if m2 else None,
        })

    cross_assigned_count = sum(1 for r in results if r["cross_assigned"])
    return {
        "collision_groups_checked": len(results),
        "cross_assigned_count": cross_assigned_count,
        "cross_assigned_rate": round(cross_assigned_count / len(results), 3) if results else None,
        "cross_assigned_examples": [r for r in results if r["cross_assigned"]][:10],
    }


def run_grade(matcher_output_dir: Path, ground_truth_path: Path) -> dict:
    gt = _load_ground_truth(ground_truth_path)
    entries = gt["entries"]
    collision_groups = gt["collision_groups"]

    matches = _load_matches(matcher_output_dir / "matches.json")
    escalations = _load_escalations(matcher_output_dir / "escalations.json")
    matches_by_bank = {m["bank_record_id"]: m for m in matches}
    escalations_by_bank = {e["bank_record_id"]: e for e in escalations}

    generic = grade_generic_entries(entries, matches_by_bank, escalations_by_bank)
    pr_by_tier = precision_recall_by_tier(generic)
    ambiguous = grade_ambiguous_subset_sum(entries, matches_by_bank, escalations_by_bank)
    orphan_invoices = grade_orphan_invoices(entries, matches)
    collisions = grade_duplicate_collisions(collision_groups, entries, matches_by_bank)

    return {
        "precision_recall_by_tier": pr_by_tier,
        "outcome_counts": {
            outcome: sum(1 for g in generic if g["outcome"] == outcome)
            for outcome in sorted({g["outcome"] for g in generic})
        },
        "ambiguous_subset_sum": ambiguous,
        "orphan_invoices": orphan_invoices,
        "duplicate_collisions": collisions,
    }


def print_grade_report(report: dict) -> None:
    print("=== Precision / Recall by tier ===")
    for tier, stats in report["precision_recall_by_tier"].items():
        print(f"  {tier:<12} tp={stats['tp']:>4} fp={stats['fp']:>4} fn={stats['fn']:>4} "
              f"precision={stats['precision']} recall={stats['recall']}")

    print("\n=== Outcome counts (generic entries, excludes ambiguous_subset_sum) ===")
    for outcome, count in report["outcome_counts"].items():
        print(f"  {outcome:<32} {count:>5}")

    amb = report["ambiguous_subset_sum"]
    print("\n=== ambiguous_subset_sum (headline check) ===")
    print(f"  total                                  {amb['ambiguous_subset_sum_total']:>5}")
    print(f"  correctly flagged (tier3_ambiguous)     {amb['ambiguous_correctly_flagged']:>5}")
    print(f"  incorrectly resolved (unique subset_sum){amb['ambiguous_incorrectly_resolved']:>5}")
    print(f"  resolved_by_stronger_signal_than_intended {amb['resolved_by_stronger_signal_than_intended']:>3}"
          f"  (rate={amb['resolved_by_stronger_signal_rate']})")
    print(f"  missed_other                            {amb['ambiguous_missed_other']:>5}")

    orphan = report["orphan_invoices"]
    print("\n=== orphan_invoice false-match check ===")
    print(f"  total orphan invoices: {orphan['orphan_invoice_total']}, "
          f"false matches: {len(orphan['orphan_invoice_false_matches'])}")
    if orphan["orphan_invoice_false_matches"]:
        print(f"  false-matched invoice ids: {orphan['orphan_invoice_false_matches']}")

    coll = report["duplicate_collisions"]
    print("\n=== duplicate_amount_collision cross-assignment check ===")
    print(f"  groups checked: {coll['collision_groups_checked']}, "
          f"cross-assigned: {coll['cross_assigned_count']} (rate={coll['cross_assigned_rate']})")
    if coll["cross_assigned_examples"]:
        print("  examples:")
        for ex in coll["cross_assigned_examples"]:
            print(f"    {ex}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade matcher output against ground_truth.json.")
    parser.add_argument("--matcher-output", type=Path, default=Path("matcher_output"))
    parser.add_argument("--ground-truth", type=Path, default=Path("data/answer_key/ground_truth.json"))
    args = parser.parse_args()

    report = run_grade(args.matcher_output, args.ground_truth)
    print_grade_report(report)
    with open(args.matcher_output / "grade_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    main()
