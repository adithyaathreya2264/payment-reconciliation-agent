"""CSV -> typed records.

This module (and grade.py alone) are the only places allowed to read repo data.
matcher must never `import generator.*` -- it only ever sees what a real downstream
consumer would see: the three CSVs, and (in grade.py only) the hidden
data/answer_key/ground_truth.json.
"""

from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path

from .models import BankRecordRow, InvoiceRecord, SettlementRecord

# Matches the generator's literal UTR<12 digits> format. Only NEFT/RTGS-style
# narration templates interpolate a UTR at all (UPI-style templates interpolate a
# random 12-digit {ref} with no "UTR" prefix), so this correctly returns no match on
# UPI-style narrations rather than a false hit.
_UTR_RE = re.compile(r"UTR\d{12}")

_warned_missing_settled_at = False


def parse_utr_from_narration(narration: str) -> str | None:
    m = _UTR_RE.search(narration)
    return m.group(0) if m else None


def load_invoices(path: Path) -> list[InvoiceRecord]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [
            InvoiceRecord(
                invoice_id=row["invoice_id"],
                counterparty_canonical=row["counterparty_canonical"],
                expected_amount=round(float(row["expected_amount"]), 2),
                expected_date=date.fromisoformat(row["expected_date"]),
                payment_method=row["payment_method"],
                status=row["status"],
            )
            for row in reader
        ]


def load_settlements(path: Path) -> list[SettlementRecord]:
    global _warned_missing_settled_at
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        records = []
        for row in reader:
            order_id_raw = row["order_id"]
            claimed_invoice_ids = order_id_raw.split("|") if order_id_raw else []
            settled_at_raw = row.get("settled_at")
            if settled_at_raw:
                settled_at = date.fromisoformat(settled_at_raw)
            else:
                settled_at = None
                if not _warned_missing_settled_at:
                    print(
                        "WARNING: settlement_report.csv has no settled_at column -- "
                        "Tier 1/2 date-lag checks will fail for all records. "
                        "Regenerate data/ with the current generator to fix this."
                    )
                    _warned_missing_settled_at = True
            records.append(
                SettlementRecord(
                    settlement_id=row["settlement_id"],
                    entity_id=row["entity_id"],
                    type=row["type"],
                    debit=round(float(row["debit"]), 2),
                    credit=round(float(row["credit"]), 2),
                    amount=round(float(row["amount"]), 2),
                    fee=round(float(row["fee"]), 2),
                    tax=round(float(row["tax"]), 2),
                    settlement_utr=row["settlement_utr"],
                    order_id_raw=order_id_raw,
                    claimed_invoice_ids=claimed_invoice_ids,
                    settled_at=settled_at,
                )
            )
        return records


def load_bank_statement(path: Path) -> list[BankRecordRow]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [
            BankRecordRow(
                bank_record_id=row["bank_record_id"],
                date=date.fromisoformat(row["date"]),
                narration=row["narration"],
                amount=round(float(row["amount"]), 2),
                bank_name=row["bank_name"],
                utr_reference=row["utr_reference"] or None,
                parsed_utr_from_narration=parse_utr_from_narration(row["narration"]),
            )
            for row in reader
        ]
