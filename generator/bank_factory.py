from __future__ import annotations

import random
import string
from collections import Counter
from datetime import timedelta

from . import config, entities, reference_data
from .models import BankRecord, Invoice, Settlement


def _random_ref(rng: random.Random, length: int = 12) -> str:
    return "".join(rng.choices(string.digits, k=length))


def _build_narration(rng: random.Random, payment_method: str, name: str, bank: str, utr: str) -> str:
    if payment_method in ("NEFT", "RTGS"):
        template = rng.choice(reference_data.NARRATION_TEMPLATES_NEFT_RTGS)
    else:
        template = rng.choice(reference_data.NARRATION_TEMPLATES_UPI)
    return template.format(ref=_random_ref(rng), name=name, bank=bank, utr=utr)


def build_bank_record_for_settlement(
    rng: random.Random,
    settlement: Settlement,
    batch_invoices: list[Invoice],
    seed: int,
    counter: int,
) -> BankRecord:
    # Counter (a dict subclass) preserves first-insertion order for tie-breaking,
    # unlike a set/frozenset whose iteration order is hash-randomized per process —
    # using a set here previously broke reproducibility across runs with the same seed.
    method = Counter(inv.payment_method for inv in batch_invoices).most_common(1)[0][0]
    use_variant = settlement.failure_mode == "entity_name_variant"
    is_ambiguous = settlement.failure_mode == "ambiguous_subset_sum"
    # pick one representative counterparty name for the narration (the settlement is
    # an aggregate credit, but real bank narrations still show a single payer/name)
    representative = rng.choice(batch_invoices)
    display_name = entities.pick_display_name(rng, representative.counterparty_canonical, use_variant=use_variant)
    bank = rng.choice(reference_data.BANKS)
    # ambiguous_subset_sum: force a UPI-style narration (uses a random {ref}, never
    # the real UTR) regardless of payment method, and drop utr_reference below --
    # models a bank-side UTR loss so this failure mode can't be trivially resolved
    # by reading the UTR, mirroring order_id being blanked in settlement_factory.
    narration_method = "UPI" if is_ambiguous else method
    narration = _build_narration(rng, narration_method, display_name, bank, settlement.settlement_utr)

    lag = rng.randint(0, config.BANK_POSTING_LAG_MAX)
    record_date = settlement.settled_at + timedelta(days=lag)

    return BankRecord(
        bank_record_id=f"BANK-{seed}-{counter:06d}",
        date=record_date,
        narration=narration,
        amount=settlement.net_amount,
        bank_name=bank,
        utr_reference=None if is_ambiguous else settlement.settlement_utr,
        settlement_id=settlement.settlement_id,
        payment_method=method,
    )


def build_orphan_bank_record(
    rng: random.Random,
    spec: dict,
    seed: int,
    counter: int,
) -> BankRecord:
    bank = rng.choice(reference_data.BANKS)
    ref = _random_ref(rng)
    narration = _build_narration(rng, spec["payment_method"], spec["counterparty_canonical"], bank, ref)
    lag = rng.randint(0, config.BANK_POSTING_LAG_MAX)
    record_date = spec["date"] + timedelta(days=lag)
    return BankRecord(
        bank_record_id=f"BANK-{seed}-{counter:06d}",
        date=record_date,
        narration=narration,
        amount=spec["amount"],
        bank_name=bank,
        utr_reference=None,
        settlement_id=None,
        payment_method=spec["payment_method"],
    )
