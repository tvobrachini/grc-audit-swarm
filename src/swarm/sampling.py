"""
Deterministic AICPA / PCAOB AS 2201 / AS 1215 Audit Sampling & IPE Hash Engine.

Provides 0-token, cryptographically reproducible sample sizing and selection:
  1. ``recommended_sample_size``: AICPA Audit Guide / Big Four sample-size lookup
     by control frequency, control nature, and inherent risk rating.
  2. ``deterministic_sample_selection``: Computes the SHA-256 digest of the IPE
     population (`population_sha256`) and selects a reproducible random sample
     seeded by ``HMAC-SHA256(session_id, control_id)`` so any reviewer can
     re-perform the exact sample selection (PCAOB AS 1215).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
from dataclasses import dataclass
from typing import Sequence

from swarm.schema import (
    ControlFrequency,
    ControlNature,
    RiskRating,
    SamplingMethod,
)

# (low_or_medium_risk_n, high_risk_n) per AICPA / Big Four standard audit tables
_SAMPLE_TABLE: dict[ControlFrequency, tuple[int, int]] = {
    ControlFrequency.ANNUAL: (1, 1),
    ControlFrequency.QUARTERLY: (2, 2),
    ControlFrequency.MONTHLY: (2, 5),
    ControlFrequency.WEEKLY: (5, 15),
    ControlFrequency.DAILY: (20, 40),
    ControlFrequency.MULTIPLE_TIMES_PER_DAY: (25, 60),
    ControlFrequency.CONTINUOUS: (25, 45),
    ControlFrequency.EVENT_DRIVEN: (5, 15),
}


@dataclass(frozen=True)
class SampleSelectionReceipt:
    """Reproducible PCAOB AS 1215 / IIA 14.3 sampling & IPE verification receipt."""

    session_id: str
    control_id: str
    population_count: int
    population_sha256: str
    sample_size: int
    sampling_method: str
    seed_hmac_sha256: str
    selected_indices: list[int]
    selected_items: list[str]


def recommended_sample_size(
    frequency: ControlFrequency | str,
    nature: ControlNature | str,
    *,
    risk_rating: RiskRating | str = RiskRating.MEDIUM,
) -> int:
    """Return the standard AICPA / Big Four sample size for ToE testing."""
    nat = ControlNature(nature)
    if nat == ControlNature.AUTOMATED:
        return 1
    freq = ControlFrequency(frequency)
    low_med_n, high_n = _SAMPLE_TABLE.get(freq, (5, 15))
    rating = RiskRating(risk_rating)
    return high_n if rating == RiskRating.HIGH else low_med_n


def compute_ipe_population_digest(population_items: Sequence[str]) -> str:
    """Compute canonical SHA-256 digest of an IPE population listing (`D10`)."""
    canonical = json.dumps(
        [str(item) for item in population_items],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def deterministic_sample_selection(
    population_items: Sequence[str],
    sample_size: int,
    *,
    session_id: str,
    control_id: str,
) -> SampleSelectionReceipt:
    """Select a cryptographically reproducible sample seeded by HMAC(session_id, control_id)."""
    items = [str(x) for x in population_items]
    pop_count = len(items)
    pop_hash = compute_ipe_population_digest(items)
    seed_hex = hmac.new(
        session_id.encode("utf-8"),
        control_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if sample_size <= 0 or pop_count == 0:
        return SampleSelectionReceipt(
            session_id=session_id,
            control_id=control_id,
            population_count=pop_count,
            population_sha256=pop_hash,
            sample_size=0,
            sampling_method=SamplingMethod.NOT_APPLICABLE.value,
            seed_hmac_sha256=seed_hex,
            selected_indices=[],
            selected_items=[],
        )

    if sample_size >= pop_count:
        return SampleSelectionReceipt(
            session_id=session_id,
            control_id=control_id,
            population_count=pop_count,
            population_sha256=pop_hash,
            sample_size=pop_count,
            sampling_method=SamplingMethod.FULL_POPULATION.value,
            seed_hmac_sha256=seed_hex,
            selected_indices=list(range(pop_count)),
            selected_items=list(items),
        )

    # Seeded from HMAC-SHA256 for deterministic audit replay
    rng = random.Random(int(seed_hex, 16))  # nosec B311
    indices = sorted(rng.sample(range(pop_count), sample_size))
    selected = [items[i] for i in indices]
    method = (
        SamplingMethod.TEST_OF_ONE.value
        if sample_size == 1
        else SamplingMethod.RANDOM.value
    )
    return SampleSelectionReceipt(
        session_id=session_id,
        control_id=control_id,
        population_count=pop_count,
        population_sha256=pop_hash,
        sample_size=sample_size,
        sampling_method=method,
        seed_hmac_sha256=seed_hex,
        selected_indices=indices,
        selected_items=selected,
    )
