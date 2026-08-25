from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import BankRecord, GroundTruthEntry, Invoice, Settlement

INVOICE_FIELDS = [
    "invoice_id",
    "counterparty_canonical",
    "expected_amount",
    "expected_date",
    "payment_method",
    "status",
]

SETTLEMENT_FIELDS = [
    "entity_id",
    "type",
    "debit",
    "credit",
    "amount",
    "fee",
    "tax",
    "settlement_id",
    "settlement_utr",
    "order_id",
    "settled_at",
]

BANK_FIELDS = [
    "bank_record_id",
    "date",
    "narration",
    "amount",
    "bank_name",
    "utr_reference",
]


def write_invoices_csv(path: Path, invoices: list[Invoice]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=INVOICE_FIELDS)
        writer.writeheader()
        for inv in invoices:
            writer.writerow(
                {
                    "invoice_id": inv.invoice_id,
                    "counterparty_canonical": inv.counterparty_canonical,
                    "expected_amount": inv.expected_amount,
                    "expected_date": inv.expected_date.isoformat(),
                    "payment_method": inv.payment_method,
                    "status": inv.status,
                }
            )


def write_settlement_report_csv(path: Path, settlements: list[Settlement]) -> None:
    
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SETTLEMENT_FIELDS)
        writer.writeheader()
        for s in settlements:
            writer.writerow(
                {
                    "entity_id": s.entity_id,
                    "type": s.type,
                    "debit": s.debit,
                    "credit": s.net_amount,
                    "amount": s.amount,
                    "fee": s.fee,
                    "tax": s.tax,
                    "settlement_id": s.settlement_id,
                    "settlement_utr": s.settlement_utr,
                    "order_id": s.order_id,
                    "settled_at": s.settled_at.isoformat(),
                }
            )


def write_bank_statement_csv(path: Path, bank_records: list[BankRecord]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=BANK_FIELDS)
        writer.writeheader()
        for b in bank_records:
            writer.writerow(
                {
                    "bank_record_id": b.bank_record_id,
                    "date": b.date.isoformat(),
                    "narration": b.narration,
                    "amount": b.amount,
                    "bank_name": b.bank_name,
                    "utr_reference": b.utr_reference or "",
                }
            )


def write_ground_truth(path: Path, entries: list[GroundTruthEntry], collision_groups: list[dict]) -> None:
    payload = {
        "entries": [
            {
                "invoice_ids": e.invoice_ids,
                "bank_record_id": e.bank_record_id,
                "settlement_id": e.settlement_id,
                "true_match_ids": e.true_match_ids,
                "failure_mode_injected": e.failure_mode_injected,
                "true_reason": e.true_reason,
                "detectable_at_tier": e.detectable_at_tier,
                "identifiable_at_tier": e.identifiable_at_tier,
                "competing_subset_ids": e.competing_subset_ids,
                "collision_group_id": e.collision_group_id,
                "dropped_invoice_id": e.dropped_invoice_id,
            }
            for e in entries
        ],
        "collision_groups": collision_groups,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
