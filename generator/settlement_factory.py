from __future__ import annotations

import random
from collections import Counter
from datetime import date, timedelta

from . import config, failure_modes
from .batching import Batch
from .models import Invoice, Settlement

ENTITY_ID = "acc_genfin_demo01"


def _dominant_payment_method(batch_invoices: list[Invoice]) -> str:
    counts = Counter(inv.payment_method for inv in batch_invoices)
    return counts.most_common(1)[0][0]


def _compute_fee(gross_amount: float, payment_method: str) -> float:
    if payment_method in config.MDR_FLAT_FEES:
        return config.MDR_FLAT_FEES[payment_method]
    rate = config.MDR_RATES[payment_method]
    return round(gross_amount * rate, 2)


def _settled_at(rng: random.Random, latest_invoice_date: date, is_late: bool) -> date:
    normal_lag = rng.choices([1, 2], weights=[0.6, 0.4], k=1)[0]
    settled = latest_invoice_date + timedelta(days=normal_lag)
    if is_late:
        settled += timedelta(days=failure_modes.late_settlement_extra_days(rng))
        if settled.weekday() not in (5, 6):  
            days_to_saturday = (5 - settled.weekday()) % 7
            settled += timedelta(days=days_to_saturday)
    return settled


def build_settlement(
    rng: random.Random,
    batch: Batch,
    invoices_by_id: dict[str, Invoice],
    seed: int,
    counter: int,
) -> Settlement:
    batch_invoices = [invoices_by_id[iid] for iid in batch.invoice_ids]
    gross_amount = round(sum(inv.expected_amount for inv in batch_invoices), 2)
    method = _dominant_payment_method(batch_invoices)
    fee = _compute_fee(gross_amount, method)
    tax = round(fee * config.GST_RATE, 2)
    true_net_amount = round(gross_amount - fee - tax, 2)

    net_amount = true_net_amount
    displayed_true_net: float | None = None
    if batch.failure_mode == "rounding_fee_variance":
        net_amount, _delta = failure_modes.apply_rounding_variance(true_net_amount, rng)
        displayed_true_net = true_net_amount

    latest_date = max(inv.expected_date for inv in batch_invoices)
    settled_at = _settled_at(rng, latest_date, is_late=(batch.failure_mode == "late_settlement"))

    settlement_id = f"SETL-{seed}-{counter:06d}"
    settlement_utr = f"UTR{rng.randint(10**11, 10**12 - 1)}"
    
    order_id = "" if batch.failure_mode == "ambiguous_subset_sum" else "|".join(batch.invoice_ids)

    for inv in batch_invoices:
        inv.status = "paid"

    return Settlement(
        settlement_id=settlement_id,
        entity_id=ENTITY_ID,
        type="settlement",
        debit=0.0,
        credit=gross_amount,
        amount=gross_amount,
        fee=fee,
        tax=tax,
        net_amount=net_amount,
        settlement_utr=settlement_utr,
        settled_at=settled_at,
        order_id=order_id,
        invoice_ids=list(batch.invoice_ids),
        failure_mode=batch.failure_mode,
        true_net_amount=displayed_true_net,
    )
