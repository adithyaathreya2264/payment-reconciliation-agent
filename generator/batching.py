from __future__ import annotations

import itertools
import random
from dataclasses import dataclass

from . import config
from .models import Invoice

_SETTLEMENT_LEVEL_MODES = {"rounding_fee_variance", "late_settlement", "entity_name_variant"}

_MIN_CLUSTER_SIZE = 6
_MAX_CLUSTER_SIZE = 10


@dataclass
class Batch:
    invoice_ids: list[str]
    failure_mode: str
    dropped_invoice_id: str | None = None
    competing_subset_ids: list[str] | None = None


def _pick_batch_mode(rng: random.Random, batch_invoices: list[Invoice]) -> str:
    driver = rng.choice(batch_invoices)
    if driver.planned_failure_mode in _SETTLEMENT_LEVEL_MODES:
        return driver.planned_failure_mode
    return "clean_match"


def _build_ambiguous_subset_sum_batch(
    rng: random.Random,
    anchor: Invoice,
    unconsumed: list[Invoice],
    consumed_ids: set[str],
) -> Batch | None:
    candidates = [
        inv
        for inv in unconsumed
        if inv.invoice_id not in consumed_ids
        and abs((inv.expected_date - anchor.expected_date).days) <= 5
        and inv.planned_failure_mode not in ("ambiguous_subset_sum", "partial_batch_failure", "orphan_invoice")
    ]
    if anchor not in candidates:
        candidates.append(anchor)
    rng.shuffle(candidates)
    cluster = candidates[:_MAX_CLUSTER_SIZE]
    if anchor not in cluster:
        cluster = cluster[: _MAX_CLUSTER_SIZE - 1] + [anchor]
    if len(cluster) < _MIN_CLUSTER_SIZE:
        return None

    k1 = rng.randint(2, min(5, len(cluster) - 1))
    subset_a = rng.sample(cluster, k1)
    subset_a_ids = frozenset(inv.invoice_id for inv in subset_a)
    sum_a = round(sum(inv.expected_amount for inv in subset_a), 2)

    best_subset_b: list[Invoice] | None = None
    best_delta = float("inf")
    for r in range(1, len(cluster) + 1):
        for combo in itertools.combinations(cluster, r):
            combo_ids = frozenset(inv.invoice_id for inv in combo)
            if combo_ids == subset_a_ids:
                continue
            sum_b = round(sum(inv.expected_amount for inv in combo), 2)
            delta = abs(sum_b - sum_a)
            if delta < best_delta:
                best_delta = delta
                best_subset_b = list(combo)

    if best_subset_b is None or best_delta > config.ROUNDING_VARIANCE_MAX:
        return None

    return Batch(
        invoice_ids=[inv.invoice_id for inv in subset_a],
        failure_mode="ambiguous_subset_sum",
        competing_subset_ids=[inv.invoice_id for inv in best_subset_b],
    )


def form_batches(
    rng: random.Random,
    invoices: list[Invoice],
) -> tuple[list[Batch], dict[str, Invoice]]:
    invoices_by_id = {inv.invoice_id: inv for inv in invoices}

    pool = [inv for inv in invoices if inv.planned_failure_mode != "orphan_invoice"]
    rng.shuffle(pool)

    consumed_ids: set[str] = set()
    batches: list[Batch] = []

    for inv in list(pool):
        if inv.invoice_id in consumed_ids or inv.planned_failure_mode != "ambiguous_subset_sum":
            continue
        unconsumed = [i for i in pool if i.invoice_id not in consumed_ids]
        batch = _build_ambiguous_subset_sum_batch(rng, inv, unconsumed, consumed_ids)
        if batch is None:
            
            inv.planned_failure_mode = "clean_match"
            continue
        batches.append(batch)
        consumed_ids.update(batch.invoice_ids)

    for inv in list(pool):
        if inv.invoice_id in consumed_ids or inv.planned_failure_mode != "partial_batch_failure":
            continue
        unconsumed = [i for i in pool if i.invoice_id not in consumed_ids]
        target_size = rng.randint(config.MIN_BATCH_SIZE, config.MAX_BATCH_SIZE)
        near = [
            i for i in unconsumed
            if abs((i.expected_date - inv.expected_date).days) <= 7 and i.invoice_id != inv.invoice_id
        ]
        rng.shuffle(near)
        batch_invoices = [inv] + near[: max(target_size - 1, config.MIN_BATCH_SIZE - 1)]
        if len(batch_invoices) < config.MIN_BATCH_SIZE:
            
            inv.planned_failure_mode = "clean_match"
            continue
        kept = [i for i in batch_invoices if i.invoice_id != inv.invoice_id]
        batches.append(
            Batch(
                invoice_ids=[i.invoice_id for i in kept],
                failure_mode="partial_batch_failure",
                dropped_invoice_id=inv.invoice_id,
            )
        )
        consumed_ids.update(i.invoice_id for i in kept)
        consumed_ids.add(inv.invoice_id)  # dropped invoice is consumed too: never settled elsewhere

    
    remaining = [i for i in pool if i.invoice_id not in consumed_ids]
    rng.shuffle(remaining)
    i = 0
    while i < len(remaining):
        size = rng.randint(config.MIN_BATCH_SIZE, config.MAX_BATCH_SIZE)
        chunk = remaining[i : i + size]
        i += size
        if len(chunk) < config.MIN_BATCH_SIZE and batches:
            
            last = batches[-1]
            if last.failure_mode != "ambiguous_subset_sum" and last.dropped_invoice_id is None:
                last.invoice_ids.extend(inv.invoice_id for inv in chunk)
                consumed_ids.update(inv.invoice_id for inv in chunk)
                continue
        mode = _pick_batch_mode(rng, chunk)
        batches.append(Batch(invoice_ids=[inv.invoice_id for inv in chunk], failure_mode=mode))
        consumed_ids.update(inv.invoice_id for inv in chunk)

    return batches, invoices_by_id
