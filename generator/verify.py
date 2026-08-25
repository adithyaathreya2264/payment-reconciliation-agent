from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from . import config

GST_RATE = config.GST_RATE


def _load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_ground_truth(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _fee_for(gross_amount: float, payment_method: str) -> float:
    if payment_method in config.MDR_FLAT_FEES:
        return config.MDR_FLAT_FEES[payment_method]
    return round(gross_amount * config.MDR_RATES[payment_method], 2)


def run_checks(data_dir: Path) -> bool:
    invoices = _load_csv(data_dir / "invoices.csv")
    settlements = _load_csv(data_dir / "settlement_report.csv")
    bank_records = _load_csv(data_dir / "bank_statement.csv")
    gt = _load_ground_truth(data_dir / "answer_key" / "ground_truth.json")
    entries = gt["entries"]

    invoices_by_id = {r["invoice_id"]: r for r in invoices}
    settlements_by_id = {r["settlement_id"]: r for r in settlements}
    bank_by_id = {r["bank_record_id"]: r for r in bank_records}

    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        status = "PASS" if condition else "FAIL"
        print(f"[{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
        if not condition:
            failures.append(name)

    
    check("invoice_id uniqueness", len(invoices_by_id) == len(invoices))
    check("settlement_id uniqueness", len(settlements_by_id) == len(settlements))
    check("bank_record_id uniqueness", len(bank_by_id) == len(bank_records))

    
    ref_ok = True
    for e in entries:
        sid = e.get("settlement_id")
        if sid and sid not in settlements_by_id:
            ref_ok = False
        bid = e.get("bank_record_id")
        if bid and bid not in bank_by_id:
            ref_ok = False
    check("ground truth settlement/bank references resolve", ref_ok)

    
    clean_ok = True
    for e in entries:
        if e["failure_mode_injected"] != "clean_match" or not e["settlement_id"]:
            continue
        s = settlements_by_id[e["settlement_id"]]
        gross = round(sum(float(invoices_by_id[iid]["expected_amount"]) for iid in e["true_match_ids"]), 2)
        method_counts = Counter(invoices_by_id[iid]["payment_method"] for iid in e["true_match_ids"])
        method = method_counts.most_common(1)[0][0]
        fee = _fee_for(gross, method)
        tax = round(fee * GST_RATE, 2)
        expected_net = round(gross - fee - tax, 2)
        actual_net = float(s["credit"])
        if abs(expected_net - actual_net) > 0.01:
            clean_ok = False
    check("clean-match settlements reconcile to invoice sums minus fees", clean_ok)

    
    rounding_ok = True
    for e in entries:
        if e["failure_mode_injected"] != "rounding_fee_variance" or not e["settlement_id"]:
            continue
        s = settlements_by_id[e["settlement_id"]]
        gross = round(sum(float(invoices_by_id[iid]["expected_amount"]) for iid in e["true_match_ids"]), 2)
        method_counts = Counter(invoices_by_id[iid]["payment_method"] for iid in e["true_match_ids"])
        method = method_counts.most_common(1)[0][0]
        fee = _fee_for(gross, method)
        tax = round(fee * GST_RATE, 2)
        true_net = round(gross - fee - tax, 2)
        actual_net = float(s["credit"])
        delta = abs(actual_net - true_net)
        if not (config.ROUNDING_VARIANCE_MIN - 0.01 <= delta <= config.ROUNDING_VARIANCE_MAX + 0.01):
            rounding_ok = False
    check("rounding_fee_variance deltas within configured band", rounding_ok)

    
    orphan_invoice_ids = {e["invoice_ids"][0] for e in entries if e["failure_mode_injected"] == "orphan_invoice"}
    batched_invoice_ids = set()
    for e in entries:
        if e["settlement_id"]:
            batched_invoice_ids.update(e["true_match_ids"])
            if e.get("dropped_invoice_id"):
                batched_invoice_ids.discard(e["dropped_invoice_id"])
    check("orphan invoices never appear in a settlement", orphan_invoice_ids.isdisjoint(batched_invoice_ids))

    orphan_payment_ok = all(
        e["settlement_id"] is None and e["true_match_ids"] == []
        for e in entries
        if e["failure_mode_injected"] == "orphan_payment"
    )
    check("orphan payments have no settlement/match", orphan_payment_ok)

    
    collision_ok = True
    for group in gt["collision_groups"]:
        ids = group["invoice_ids"]
        if len(ids) != 2:
            collision_ok = False
            continue
        amounts = [float(invoices_by_id[i]["expected_amount"]) for i in ids]
        if abs(amounts[0] - amounts[1]) > 0.01:
            collision_ok = False
    check("duplicate-amount collision pairs have matching amounts", collision_ok)

    
    ambiguous_ok = True
    for e in entries:
        if e["failure_mode_injected"] != "ambiguous_subset_sum":
            continue
        sum_a = sum(float(invoices_by_id[i]["expected_amount"]) for i in e["true_match_ids"])
        sum_b = sum(float(invoices_by_id[i]["expected_amount"]) for i in e["competing_subset_ids"])
        if abs(sum_a - sum_b) > config.ROUNDING_VARIANCE_MAX + 1.0:
            ambiguous_ok = False
    check("ambiguous_subset_sum competing subsets are within tolerance of each other", ambiguous_ok)

    
    blanking_ok = True
    for e in entries:
        if e["failure_mode_injected"] != "ambiguous_subset_sum":
            continue
        s = settlements_by_id.get(e["settlement_id"])
        b = bank_by_id.get(e["bank_record_id"])
        if s and s["order_id"] != "":
            blanking_ok = False
        if b and b["utr_reference"] != "":
            blanking_ok = False
        if b and "UTR" in b["narration"]:
            blanking_ok = False
    check("ambiguous_subset_sum settlements have order_id/UTR blanked", blanking_ok)

    
    mode_counts = Counter(e["failure_mode_injected"] for e in entries)
    
    total_entries = sum(mode_counts.values())
    print("\nGround-truth entry distribution vs configured targets (proxy check, not identical granularity):")
    dist_ok = True
    for mode, target in config.FAILURE_MODE_TARGETS.items():
        observed = mode_counts.get(mode, 0)
        observed_pct = observed / total_entries if total_entries else 0
        print(f"  {mode:<28} observed={observed:>5} ({observed_pct:.1%})  target={target:.1%}")
        if mode in config.LOW_FREQUENCY_MODES and observed < config.MIN_HEALTHY_COUNT:
            dist_ok = False
    check("low-frequency modes present in healthy counts", dist_ok)

    print(f"\n{len(failures)} check(s) failed" if failures else "\nAll checks passed.")
    return not failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a generated reconciliation dataset.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()

    ok = run_checks(args.data_dir)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
