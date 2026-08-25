from __future__ import annotations

import argparse
import random
from collections import Counter
from datetime import date
from pathlib import Path

from . import bank_factory, batching, config, invoice_factory, settlement_factory, writers
from .models import GroundTruthEntry


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _print_expected_counts(num_invoices: int) -> None:
    print("Expected instance count per failure mode (target proportion x num-invoices):")
    for mode, weight in config.FAILURE_MODE_TARGETS.items():
        print(f"  {mode:<28} target={weight:>6.3f}  expected~={weight * num_invoices:>7.1f}")


def _print_realized_counts(mode_counter: Counter, num_invoices: int) -> None:
    print("\nRealized instance count per failure mode (as actually assigned):")
    warnings = []
    for mode, weight in config.FAILURE_MODE_TARGETS.items():
        realized = mode_counter.get(mode, 0)
        print(f"  {mode:<28} realized={realized:>6}  target~={weight * num_invoices:>7.1f}")
        if mode in config.LOW_FREQUENCY_MODES and realized < config.MIN_HEALTHY_COUNT:
            warnings.append(mode)
    if warnings:
        print(
            f"\nWARNING: the following low-frequency modes realized fewer than "
            f"{config.MIN_HEALTHY_COUNT} instances on this seed: {', '.join(warnings)}. "
            "Consider a larger --num-invoices or a higher weight for these modes so "
            "the 'hard case' isn't accidentally near-absent from this dataset."
        )


def run(
    seed: int,
    num_invoices: int,
    out_dir: Path,
    start_date: date,
    end_date: date,
) -> None:
    rng = random.Random(seed)

    _print_expected_counts(num_invoices)

    invoices, orphan_payment_specs = invoice_factory.generate_invoices(
        rng, num_invoices, seed, start_date, end_date
    )

    mode_counter = Counter(inv.planned_failure_mode for inv in invoices)
    mode_counter["orphan_payment"] += len(orphan_payment_specs)

    batches, invoices_by_id = batching.form_batches(rng, invoices)

    settlements = []
    bank_records = []
    ground_truth_entries: list[GroundTruthEntry] = []

    for i, batch in enumerate(batches, start=1):
        settlement = settlement_factory.build_settlement(rng, batch, invoices_by_id, seed, i)
        settlements.append(settlement)

        batch_invoices = [invoices_by_id[iid] for iid in batch.invoice_ids]
        bank_record = bank_factory.build_bank_record_for_settlement(rng, settlement, batch_invoices, seed, i)
        bank_records.append(bank_record)

        from . import failure_modes as fm

        ground_truth_entries.append(
            GroundTruthEntry(
                invoice_ids=list(batch.invoice_ids) + ([batch.dropped_invoice_id] if batch.dropped_invoice_id else []),
                bank_record_id=bank_record.bank_record_id,
                settlement_id=settlement.settlement_id,
                true_match_ids=list(batch.invoice_ids),
                failure_mode_injected=batch.failure_mode,
                true_reason=fm.true_reason(batch.failure_mode),
                detectable_at_tier=fm.detectable_tier(batch.failure_mode),
                identifiable_at_tier=fm.identifiable_tier(batch.failure_mode),
                competing_subset_ids=batch.competing_subset_ids,
                dropped_invoice_id=batch.dropped_invoice_id,
            )
        )

    from . import failure_modes as fm

    orphan_bank_counter = len(batches)
    for spec in orphan_payment_specs:
        orphan_bank_counter += 1
        record = bank_factory.build_orphan_bank_record(rng, spec, seed, orphan_bank_counter)
        bank_records.append(record)
        ground_truth_entries.append(
            GroundTruthEntry(
                invoice_ids=[],
                bank_record_id=record.bank_record_id,
                settlement_id=None,
                true_match_ids=[],
                failure_mode_injected="orphan_payment",
                true_reason=fm.true_reason("orphan_payment"),
                detectable_at_tier=fm.detectable_tier("orphan_payment"),
                identifiable_at_tier=fm.identifiable_tier("orphan_payment"),
            )
        )

    for inv in invoices:
        if inv.planned_failure_mode == "orphan_invoice":
            ground_truth_entries.append(
                GroundTruthEntry(
                    invoice_ids=[inv.invoice_id],
                    bank_record_id=None,
                    settlement_id=None,
                    true_match_ids=[],
                    failure_mode_injected="orphan_invoice",
                    true_reason=fm.true_reason("orphan_invoice"),
                    detectable_at_tier=fm.detectable_tier("orphan_invoice"),
                    identifiable_at_tier=fm.identifiable_tier("orphan_invoice"),
                )
            )

    collision_groups: dict[str, list[str]] = {}
    for inv in invoices:
        if inv.collision_group_id:
            collision_groups.setdefault(inv.collision_group_id, []).append(inv.invoice_id)
    collision_group_list = [
        {"collision_group_id": gid, "invoice_ids": ids} for gid, ids in collision_groups.items()
    ]

    _print_realized_counts(mode_counter, num_invoices)

    out_dir.mkdir(parents=True, exist_ok=True)
    answer_key_dir = out_dir / "answer_key"
    answer_key_dir.mkdir(parents=True, exist_ok=True)

    writers.write_invoices_csv(out_dir / "invoices.csv", invoices)
    writers.write_settlement_report_csv(out_dir / "settlement_report.csv", settlements)
    writers.write_bank_statement_csv(out_dir / "bank_statement.csv", bank_records)
    writers.write_ground_truth(answer_key_dir / "ground_truth.json", ground_truth_entries, collision_group_list)

    print(f"\nWrote {len(invoices)} invoices, {len(settlements)} settlements, "
          f"{len(bank_records)} bank records to {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic reconciliation test data.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num-invoices", type=int, default=config.DEFAULT_NUM_INVOICES)
    parser.add_argument("--out-dir", type=Path, default=Path("data"))
    parser.add_argument("--start-date", type=_parse_date, default=config.DEFAULT_START_DATE)
    parser.add_argument("--end-date", type=_parse_date, default=config.DEFAULT_END_DATE)
    args = parser.parse_args()

    run(args.seed, args.num_invoices, args.out_dir, args.start_date, args.end_date)


if __name__ == "__main__":
    main()
