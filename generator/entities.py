"""Counterparty registry helpers: weighted canonical selection and variant lookup."""

from __future__ import annotations

import random

from . import reference_data

_CANONICALS = [c["canonical"] for c in reference_data.COUNTERPARTIES]
_WEIGHTS = [c["weight"] for c in reference_data.COUNTERPARTIES]
_VARIANTS_BY_CANONICAL = {c["canonical"]: c["variants"] for c in reference_data.COUNTERPARTIES}


def pick_canonical_counterparty(rng: random.Random) -> str:
    return rng.choices(_CANONICALS, weights=_WEIGHTS, k=1)[0]


def pick_display_name(rng: random.Random, canonical: str, *, use_variant: bool) -> str:
    """Return the name to show in a bank narration: canonical, or a registered variant."""
    if not use_variant:
        return canonical
    variants = _VARIANTS_BY_CANONICAL.get(canonical)
    if not variants:
        return canonical
    return rng.choice(variants)
