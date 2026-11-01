"""
Cross-AI-Family Model Routing, Effort Tiering & Panel of LLMs (PoLL) Jury Engine.

Implements the peer-reviewed defenses against LLM-as-a-Judge bias and multi-agent
sycophancy:
  * **Panickssery et al. (NeurIPS 2024)** — *LLM Evaluators Recognize and Favor Their
    Own Generations*: Hard disjoint-family invariant (`drafter_family != critic_family`).
  * **Verga et al. (TACL 2024 — PoLL)** — *Replacing Judges with Juries*: Panel of
    3 small/medium evaluators from pairwise-disjoint model families (`google`,
    `openai`, `anthropic` / `meta`) at ~1/7th the cost of a single frontier judge.
  * **Liang et al. (EMNLP 2024) & Wynn et al. (2025)** — Blind evaluation with zero
    peer visibility and a **PCAOB Falsification Challenger** system prompt to prevent
    Degeneration-of-Thought (DoT) and inter-agent sycophancy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Optional, Sequence

from swarm.schema import QA_PushbackSchema


class ModelFamily(StrEnum):
    """Canonical LLM weight-family taxonomy."""

    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OPENAI = "openai"
    META = "meta"
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    MISTRAL = "mistral"
    OTHER = "other"


class EffortLevel(StrEnum):
    """Reasoning / token-budget effort tier."""

    LOW = "low"  # Fast/cheap tier, 0 thinking budget
    MEDIUM = "medium"  # Standard analytical tier, ~2,048 thinking budget
    HIGH = "high"  # Deep falsification / Material Weakness tier, ~8,192 thinking budget


EFFORT_THINKING_BUDGET_TOKENS: dict[EffortLevel, int] = {
    EffortLevel.LOW: 0,
    EffortLevel.MEDIUM: 2048,
    EffortLevel.HIGH: 8192,
}


class SameFamilyBiasError(ValueError):
    """Raised when a critic shares the same model family as the drafter (Panickssery et al., NeurIPS 2024)."""


@dataclass(frozen=True)
class ModelSpec:
    """Complete model routing specification with family, effort, and cost metadata."""

    model_id: str
    family: ModelFamily
    effort: EffortLevel = EffortLevel.MEDIUM
    temperature: float = 0.0
    thinking_budget_tokens: int = 0
    cost_per_1k_input_usd: float = 0.0005
    cost_per_1k_output_usd: float = 0.0015


# Default cross-family catalog ordered by cost-efficiency
DEFAULT_FAMILY_MODELS: dict[ModelFamily, dict[EffortLevel, ModelSpec]] = {
    ModelFamily.GOOGLE: {
        EffortLevel.LOW: ModelSpec(
            "gemini/gemini-2.5-flash",
            ModelFamily.GOOGLE,
            EffortLevel.LOW,
            temperature=0.0,
            thinking_budget_tokens=0,
            cost_per_1k_input_usd=0.00015,
            cost_per_1k_output_usd=0.0006,
        ),
        EffortLevel.MEDIUM: ModelSpec(
            "gemini/gemini-2.5-flash",
            ModelFamily.GOOGLE,
            EffortLevel.MEDIUM,
            temperature=0.0,
            thinking_budget_tokens=2048,
            cost_per_1k_input_usd=0.00015,
            cost_per_1k_output_usd=0.0006,
        ),
        EffortLevel.HIGH: ModelSpec(
            "gemini/gemini-2.5-pro",
            ModelFamily.GOOGLE,
            EffortLevel.HIGH,
            temperature=0.0,
            thinking_budget_tokens=8192,
            cost_per_1k_input_usd=0.00125,
            cost_per_1k_output_usd=0.005,
        ),
    },
    ModelFamily.OPENAI: {
        EffortLevel.LOW: ModelSpec(
            "openai/gpt-4o-mini",
            ModelFamily.OPENAI,
            EffortLevel.LOW,
            temperature=0.0,
            thinking_budget_tokens=0,
            cost_per_1k_input_usd=0.00015,
            cost_per_1k_output_usd=0.0006,
        ),
        EffortLevel.MEDIUM: ModelSpec(
            "openai/gpt-4o",
            ModelFamily.OPENAI,
            EffortLevel.MEDIUM,
            temperature=0.0,
            thinking_budget_tokens=2048,
            cost_per_1k_input_usd=0.0025,
            cost_per_1k_output_usd=0.01,
        ),
        EffortLevel.HIGH: ModelSpec(
            "openai/o3-mini",
            ModelFamily.OPENAI,
            EffortLevel.HIGH,
            temperature=0.0,
            thinking_budget_tokens=8192,
            cost_per_1k_input_usd=0.0011,
            cost_per_1k_output_usd=0.0044,
        ),
    },
    ModelFamily.ANTHROPIC: {
        EffortLevel.LOW: ModelSpec(
            "anthropic/claude-3-5-haiku-latest",
            ModelFamily.ANTHROPIC,
            EffortLevel.LOW,
            temperature=0.0,
            thinking_budget_tokens=0,
            cost_per_1k_input_usd=0.0008,
            cost_per_1k_output_usd=0.004,
        ),
        EffortLevel.MEDIUM: ModelSpec(
            "anthropic/claude-3-7-sonnet-latest",
            ModelFamily.ANTHROPIC,
            EffortLevel.MEDIUM,
            temperature=0.0,
            thinking_budget_tokens=2048,
            cost_per_1k_input_usd=0.003,
            cost_per_1k_output_usd=0.015,
        ),
        EffortLevel.HIGH: ModelSpec(
            "anthropic/claude-3-7-sonnet-latest",
            ModelFamily.ANTHROPIC,
            EffortLevel.HIGH,
            temperature=0.0,
            thinking_budget_tokens=8192,
            cost_per_1k_input_usd=0.003,
            cost_per_1k_output_usd=0.015,
        ),
    },
    ModelFamily.META: {
        EffortLevel.LOW: ModelSpec(
            "groq/llama-3.3-70b-versatile",
            ModelFamily.META,
            EffortLevel.LOW,
            temperature=0.0,
            thinking_budget_tokens=0,
            cost_per_1k_input_usd=0.00059,
            cost_per_1k_output_usd=0.00079,
        ),
        EffortLevel.MEDIUM: ModelSpec(
            "groq/llama-3.3-70b-versatile",
            ModelFamily.META,
            EffortLevel.MEDIUM,
            temperature=0.0,
            thinking_budget_tokens=0,
            cost_per_1k_input_usd=0.00059,
            cost_per_1k_output_usd=0.00079,
        ),
        EffortLevel.HIGH: ModelSpec(
            "groq/llama-3.3-70b-versatile",
            ModelFamily.META,
            EffortLevel.HIGH,
            temperature=0.0,
            thinking_budget_tokens=0,
            cost_per_1k_input_usd=0.00059,
            cost_per_1k_output_usd=0.00079,
        ),
    },
}


def detect_model_family(model_id: str) -> ModelFamily:
    """Classify a provider/model string into its canonical ModelFamily."""
    m = (model_id or "").strip().lower()
    if any(k in m for k in ("claude", "anthropic")):
        return ModelFamily.ANTHROPIC
    if any(k in m for k in ("gemini", "google", "vertex")):
        return ModelFamily.GOOGLE
    if any(k in m for k in ("gpt-", "openai", "o1", "o3", "o4")):
        return ModelFamily.OPENAI
    if any(k in m for k in ("llama", "meta")):
        return ModelFamily.META
    if "deepseek" in m:
        return ModelFamily.DEEPSEEK
    if "qwen" in m:
        return ModelFamily.QWEN
    if any(k in m for k in ("mistral", "mixtral")):
        return ModelFamily.MISTRAL
    return ModelFamily.OTHER


def assert_disjoint_family(drafter_model: str, critic_model: str) -> None:
    """Enforce the hard Panickssery et al. (NeurIPS 2024) disjoint-family invariant."""
    d_fam = detect_model_family(drafter_model)
    c_fam = detect_model_family(critic_model)
    if d_fam != ModelFamily.OTHER and d_fam == c_fam:
        raise SameFamilyBiasError(
            f"Self-preference bias violation (Panickssery et al., NeurIPS 2024): "
            f"Drafter ('{drafter_model}', family={d_fam}) and Critic ('{critic_model}', "
            f"family={c_fam}) belong to the same model family."
        )


def select_cross_family_critic(
    drafter_model: str,
    *,
    effort: EffortLevel = EffortLevel.MEDIUM,
    available_families: Optional[Sequence[ModelFamily]] = None,
) -> ModelSpec:
    """Select a critic model guaranteed to be from a disjoint model family."""
    drafter_family = detect_model_family(drafter_model)
    preference_order = list(
        available_families
        or (
            ModelFamily.ANTHROPIC,
            ModelFamily.GOOGLE,
            ModelFamily.OPENAI,
            ModelFamily.META,
        )
    )
    for fam in preference_order:
        if fam != drafter_family and fam in DEFAULT_FAMILY_MODELS:
            spec = DEFAULT_FAMILY_MODELS[fam][effort]
            assert_disjoint_family(drafter_model, spec.model_id)
            return spec

    # Fallback if available_families was restricted
    for fam, tiers in DEFAULT_FAMILY_MODELS.items():
        if fam != drafter_family:
            return tiers[effort]

    raise SameFamilyBiasError(
        f"No disjoint model family available to critique drafter '{drafter_model}' ({drafter_family})."
    )


def select_effort_for_complexity(
    complexity_score: float, *, is_retry: bool = False
) -> EffortLevel:
    """Map RouteLLM pre-router complexity C(q) to EffortLevel."""
    if is_retry or complexity_score >= 0.70:
        return EffortLevel.HIGH
    if complexity_score >= 0.45:
        return EffortLevel.MEDIUM
    return EffortLevel.LOW


# ── PoLL (Panel of LLMs) Cross-Family Micro-Jury (Verga et al., TACL 2024) ──


@dataclass(frozen=True)
class PoLLJuryVote:
    """Single judge vote in a Cross-Family Panel of LLMs (PoLL)."""

    model_id: str
    family: ModelFamily
    approved: bool
    rejection_reason: Optional[str] = None
    weight: float = 1.0


def build_poll_jury_specs(
    drafter_model: str,
    *,
    effort: EffortLevel = EffortLevel.LOW,
    panel_size: int = 3,
) -> list[ModelSpec]:
    """Build a panel of K pairwise-disjoint model specs excluding the drafter's family."""
    drafter_family = detect_model_family(drafter_model)
    specs: list[ModelSpec] = []
    for fam in (
        ModelFamily.GOOGLE,
        ModelFamily.OPENAI,
        ModelFamily.ANTHROPIC,
        ModelFamily.META,
    ):
        if fam == drafter_family:
            continue
        specs.append(DEFAULT_FAMILY_MODELS[fam][effort])
        if len(specs) >= panel_size:
            break
    return specs


def aggregate_poll_jury(
    votes: Sequence[PoLLJuryVote],
    *,
    require_unanimity: bool = False,
) -> QA_PushbackSchema:
    """Aggregate K cross-family PoLL jury votes into a QA_PushbackSchema.

    Enforces pairwise family diversity across judges and applies either:
      * Unanimous approval (when ``require_unanimity=True``, e.g., Material Weakness / key ICFR)
      * Weighted 2/3 supermajority (`V_PoLL >= 2/3`) otherwise.
    """
    if not votes:
        raise ValueError("PoLL jury must contain at least one vote")

    families = [v.family for v in votes if v.family != ModelFamily.OTHER]
    if len(families) != len(set(families)):
        raise SameFamilyBiasError(
            f"PoLL jury violates pairwise family diversity: {[v.family for v in votes]}"
        )

    total_weight = sum(v.weight for v in votes) or 1.0
    approved_weight = sum(v.weight for v in votes if v.approved)
    v_poll = approved_weight / total_weight

    threshold = 0.999 if require_unanimity else (2.0 / 3.0 - 1e-6)
    approved = v_poll >= threshold

    if approved:
        return QA_PushbackSchema(approved=True, rejection_reason=None)

    reasons = [
        f"[{v.family.value}:{v.model_id}] {v.rejection_reason.strip()}"
        for v in votes
        if not v.approved and v.rejection_reason and v.rejection_reason.strip()
    ]
    combined_reason = (
        "; ".join(reasons)
        if reasons
        else f"PoLL cross-family jury rejected artifact (approval score {v_poll:.2f} < {threshold:.2f})."
    )
    return QA_PushbackSchema(approved=False, rejection_reason=combined_reason)


# ── Blind PCAOB Falsification Challenger Prompt (Liang et al. / Wynn et al.) ─


def build_blind_falsification_prompt(
    artifact_json: str,
    evidence_or_scope_context: str,
    *,
    phase: int,
) -> str:
    """Construct a blind, anti-sycophancy falsification prompt for the cross-family Critic.

    Based on Liang et al. (EMNLP 2024) and Wynn et al. (2025):
      1. Zero peer visibility (never shows prior critic votes or drafter self-confidence).
      2. Explicit falsification mandate: the reviewer is rewarded for finding
         concrete methodology, evidence-inversion, sample-size, or IPE gaps.
    """
    phase_focus = {
        1: (
            "Verify that every control has distinct Test of Design (ToD) and Test of "
            "Operating Effectiveness (ToE) steps, a valid AICPA sample size for its "
            "frequency, IPE completeness & accuracy procedures where applicable, and "
            "control-framework mappings (never auditing standards like PCAOB AS 2201)."
        ),
        2: (
            "Attempt to FALSIFY every 'Effective' / 'No exception' conclusion against "
            "the verbatim evidence quote. Reject immediately if: (a) the quote shows "
            "an exception or missing config, (b) a single point-in-time read is used "
            "to claim ToE='Effective' without an explicit ITGC or config-history basis, "
            "or (c) a numeric metric in the quote violates the RACM threshold."
        ),
        3: (
            "Verify that every preliminary deficiency from Fieldwork is evaluated in "
            "deficiency_evaluations, no untested or ineffective compensating control "
            "is used to downgrade a deficiency, the deficiency scale matches the scope, "
            "and the prose contains zero compliance certifications or AI slop."
        ),
    }.get(phase, "Verify full compliance with IIA GIAS 2024 and PCAOB AS 2201.")

    return (
        "ROLE: Blind Cross-Family PCAOB / IIA Quality Assurance Challenger.\n"
        "MANDATE: Do NOT rubber-stamp or defer to the drafter. Assume the draft contains "
        "at least one subtle audit methodology or evidence-entailment flaw until proven "
        "otherwise by the raw context below.\n\n"
        f"PHASE {phase} FALSIFICATION CHECKLIST:\n{phase_focus}\n\n"
        f"GROUNDING CONTEXT / EVIDENCE:\n{evidence_or_scope_context}\n\n"
        f"CANDIDATE ARTIFACT (JSON):\n{artifact_json}\n\n"
        "Respond strictly with JSON matching QA_PushbackSchema (`approved` boolean and "
        "specific `rejection_reason` naming exact control/finding/risk IDs if rejected)."
    )
