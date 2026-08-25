from __future__ import annotations

import random
from datetime import date, timedelta

from . import config, entities, failure_modes
from .models import Invoice


def _draw_amount(rng: random.Random) -> float:
    from . import reference_data

    raw = rng.lognormvariate(reference_data.AMOUNT_LOGNORMAL_MEAN, reference_data.AMOUNT_LOGNORMAL_SIGMA)
    clipped = min(max(raw, config.AMOUNT_MIN), config.AMOUNT_MAX)
    return round(clipped, 2)


def _draw_date(rng: random.Random, start: date, end: date) -> date:
    span_days = (end - start).days
    return start + timedelta(days=rng.randint(0, span_days))


def _draw_payment_method(rng: random.Random) -> str:
    methods = list(config.PAYMENT_METHOD_WEIGHTS.keys())
    weights = list(config.PAYMENT_METHOD_WEIGHTS.values())
    return rng.choices(methods, weights=weights, k=1)[0]


def generate_invoices(
    rng: random.Random,
    num_invoices: int,
    seed: int,
    start_date: date = config.DEFAULT_START_DATE,
    end_date: date = config.DEFAULT_END_DATE,
) -> tuple[list[Invoice], list[dict]]:
    
    invoices: list[Invoice] = []
    orphan_payment_specs: list[dict] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"INV-{seed}-{counter:06d}"

    def build_one(mode: str, *, forced_amount: float | None = None, forced_date: date | None = None) -> Invoice:
        canonical = entities.pick_canonical_counterparty(rng)
        amount = forced_amount if forced_amount is not None else _draw_amount(rng)
        inv_date = forced_date if forced_date is not None else _draw_date(rng, start_date, end_date)
        method = _draw_payment_method(rng)
        return Invoice(
            invoice_id=next_id(),
            counterparty_canonical=canonical,
            expected_amount=amount,
            expected_date=inv_date,
            payment_method=method,
            status="unpaid",  
            planned_failure_mode=mode,
        )

    while len(invoices) < num_invoices:
        mode = failure_modes.sample_failure_mode(rng)

        if mode == "orphan_payment":
            orphan_payment_specs.append(
                {
                    "counterparty_canonical": entities.pick_canonical_counterparty(rng),
                    "amount": _draw_amount(rng),
                    "date": _draw_date(rng, start_date, end_date),
                    "payment_method": _draw_payment_method(rng),
                }
            )
            continue

        if mode == "duplicate_amount_collision" and len(invoices) + 1 < num_invoices:
            first = build_one(mode)
            group_id = f"COLL-{seed}-{counter:06d}"
            first.collision_group_id = group_id
            partner_date = first.expected_date + timedelta(days=rng.randint(-3, 3))
            # clamp partner date into the configured window
            partner_date = min(max(partner_date, start_date), end_date)
            partner = build_one(mode, forced_amount=first.expected_amount, forced_date=partner_date)
            partner.collision_group_id = group_id
            invoices.append(first)
            invoices.append(partner)
            continue

        invoices.append(build_one(mode))

    return invoices[:num_invoices], orphan_payment_specs
