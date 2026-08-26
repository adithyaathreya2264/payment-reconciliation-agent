from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Stage(Enum):
    EXACT = "exact"
    TOLERANCE = "tolerance"
    SUBSET_SUM = "subset_sum"
    ZERO_CANDIDATE_RULE = "zero_candidate_rule"
    LLM_ESCALATION = "llm_escalation"
    RESOLVED = "resolved"
    EXCEPTION = "exception"



ORDERED_STAGES = (
    Stage.EXACT,
    Stage.TOLERANCE,
    Stage.SUBSET_SUM,
    Stage.ZERO_CANDIDATE_RULE,
    Stage.LLM_ESCALATION,
)


@dataclass
class Transition:
    from_stage: Stage
    to_stage: Stage
    reason: str
    observed: dict


@dataclass
class DecisionTrace:
    bank_record_id: str
    transitions: list[Transition] = field(default_factory=list)
    final_stage: Stage | None = None
    final_decision: dict | None = None


def _resolving_stage(trace: DecisionTrace) -> Stage | None:
    """The single from_stage whose Transition actually terminated the trace (its
    to_stage is RESOLVED or EXCEPTION), or None if no transition was recorded."""
    for t in trace.transitions:
        if t.to_stage in (Stage.RESOLVED, Stage.EXCEPTION):
            return t.from_stage
    return None


def skipped_stages(trace: DecisionTrace) -> list[Stage]:

    resolving = _resolving_stage(trace)
    return [t.from_stage for t in trace.transitions if t.from_stage != resolving]


def not_attempted_stages(trace: DecisionTrace) -> list[Stage]:
    """Stages in ORDERED_STAGES the controller never called a tool for at all --
    later than wherever this record's trace actually terminated."""
    attempted = {t.from_stage for t in trace.transitions}
    return [s for s in ORDERED_STAGES if s not in attempted]
