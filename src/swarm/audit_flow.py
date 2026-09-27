import hashlib
import logging
import re
import uuid
from datetime import datetime, UTC
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Any, Callable, Optional

from swarm.state.schema import (  # noqa: F401 (re-exported for backwards compat)
    AuditState,
    GenerationRun,
)
from swarm.state.machine import (
    AuditStatus,
    AuditStateMachine,
    InvalidTransitionError,
)
from swarm.crews.planning_crew import PlanningCrew
from swarm.crews.fieldwork_crew import FieldworkCrew
from swarm.crews.reporting_crew import ReportingCrew
from swarm.crews.result_adapter import CrewResultAdapter
from swarm.demo import (
    DemoCrew,
    demo_mode_enabled,
    demo_mode_requested,
    demo_reject_phase,
)
from swarm.evidence import (
    app_version as evidence_app_version,
    evidence_session,
    unverified_findings,
)
from swarm.review_policy import (
    MissingReviewDecisionsError,
    ReviewBlockedError,
    SegregationOfDutiesError,
    UnverifiedEvidenceError,
    sod_violation,
)
from swarm.review_decisions import (
    DecisionConflictError,
    DecisionContext,
    DecisionValidationError,
    EffectiveView,
    build_decision,
    decision_states,
    effective_view,
)
from swarm import trail as audit_trail
from swarm.schema import DeficiencyScale, IdentitySource, ReviewDecision

logger = logging.getLogger(__name__)

__all__ = [
    "AuditFlow",
    "AuditState",
    "DecisionConflictError",
    "DecisionValidationError",
    "InvalidTransitionError",
    "MissingReviewDecisionsError",
    "PhaseArtifactMissingError",
    "QA_UNPARSEABLE_REASON",
    "ReviewBlockedError",
    "SegregationOfDutiesError",
    "UnverifiedEvidenceError",
]

# One automatic QA-driven retry per phase run (i.e. two crew attempts in total).
_MAX_QA_ATTEMPTS = 2

QA_UNPARSEABLE_REASON = (
    "QA output could not be parsed into the QA schema — treated as a rejection "
    "(QA gates fail closed)."
)
_QA_NO_REASON = "QA rejected the artifact without giving a reason."

_PHASE_LABELS = {1: "Planning", 2: "Fieldwork", 3: "Reporting"}
_GATE_LABELS = {
    1: "Gate 1 (Planning)",
    2: "Gate 2 (Fieldwork)",
    3: "Gate 3 (Reporting)",
}
_ARTIFACT_FIELDS = {1: "racm_plan", 2: "working_papers", 3: "final_report"}

# Per phase: the crew input that carries QA feedback into the drafting prompt,
# and the text used to fill it (auto-retry and human-initiated retry alike).
_QA_FEEDBACK = {
    1: (
        "qa_feedback",
        " IMPORTANT: A previous draft was rejected for the following "
        "reason — fix all issues before re-drafting: {reason}",
    ),
    2: (
        "qa_feedback",
        " IMPORTANT: A previous evaluation was rejected — fix the ToD/ToE "
        "conclusions and evidence issues: {reason}",
    ),
    3: (
        "tone_qa_feedback",
        " IMPORTANT: A previous draft was rejected for tone — fix all issues: {reason}",
    ),
}


# Text carrying a reviewer's return-for-rework notes into the same per-phase
# feedback input the QA retries use.
_REWORK_FEEDBACK = (
    " IMPORTANT: The human reviewer returned the previous draft for rework. "
    "Address every review note before re-drafting: {notes}"
)

_EVIDENCE_REJECTION = (
    "Deterministic evidence check failed for {controls}: the quoted evidence was "
    "not found verbatim in the evidence vault record it cites (or that record "
    "failed its integrity check). Every tested finding needs a verifiable quote; "
    "a finding without a quote must be concluded 'Not tested'."
)


class PhaseArtifactMissingError(RuntimeError):
    """Raised when an action needs a phase artifact that does not exist."""


def _qa_rejection(qa_output: Any) -> Optional[str]:
    """Return None if QA approved, else the rejection reason.

    Fails closed: a missing/unparseable QA result (CrewAI sets ``.pydantic`` to
    None when the LLM output can't be parsed) is a rejection, never a pass.
    """
    if qa_output is None:
        return QA_UNPARSEABLE_REASON
    approved = getattr(qa_output, "approved", None)
    if not isinstance(approved, bool):
        return QA_UNPARSEABLE_REASON
    if approved:
        return None
    reason = getattr(qa_output, "rejection_reason", None)
    if isinstance(reason, str) and reason.strip():
        return reason
    return _QA_NO_REASON


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── Generation provenance (see DECISIONS.md, ADR-010) ──────────────────────

_CONFIG_DIR = Path(__file__).parent / "config"
# Per phase: the agent/task YAML files whose content is fingerprinted.
_PHASE_CONFIG_FILES: dict[int, tuple[str, str]] = {
    1: ("planning_agents.yaml", "planning_tasks.yaml"),
    2: ("fieldwork_agents.yaml", "fieldwork_tasks.yaml"),
    3: ("reporting_agents.yaml", "reporting_tasks.yaml"),
}


def prompt_fingerprint(phase: int, skill_context: list[Any]) -> str:
    """Deterministic SHA-256 fingerprint of the prompts a phase run used.

    Covers the phase's agent and task YAML config files (raw bytes, as
    loaded by the crew) and the specialist prompt text injected into an
    agent's backstory for any active domain skill (``skill_loader
    .get_specialist_prompt``), in that order. The same config files plus the
    same active skills always hash to the same value; a change to either
    changes it. Each part is length-prefixed before hashing so that
    concatenating differently split content can never collide.

    Only the *prompt* fingerprint changes on a skill toggle or a config edit;
    a difference does not by itself mean the model call itself was
    different — see ADR-010 for what this can and cannot prove.
    """
    parts: list[bytes] = [
        (_CONFIG_DIR / name).read_bytes() for name in _PHASE_CONFIG_FILES[phase]
    ]
    if skill_context:
        from swarm.skill_loader import get_specialist_prompt

        parts.append(get_specialist_prompt(skill_context).encode("utf-8"))
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def _crewai_version() -> str:
    try:
        return _pkg_version("crewai")
    except PackageNotFoundError:
        return "unknown"


# Fixed at import time: it cannot change while the process is running.
_CREWAI_VERSION = _crewai_version()
_APP_VERSION = evidence_app_version()

# Crew / QA temperatures, matching the crews' Agent construction
# (swarm.crews.*_crew): base agents at 0.1, QA reviewers at 0.0. Recorded
# here so a generation run states the temperature it used without having to
# re-derive it from the crew classes.
_CREW_TEMPERATURE = 0.1
_QA_TEMPERATURE = 0.0


def _require_text(value: str, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} is required")
    return value.strip()


def _identity_source(value: str) -> str:
    """Validated ``identity_source`` (declared | authenticated; ADR-012)."""
    try:
        return IdentitySource(value).value
    except ValueError as exc:
        raise ValueError(
            "identity_source must be one of: "
            + ", ".join(s.value for s in IdentitySource)
        ) from exc


def _control_attributes(control: Any) -> str:
    """Compact ``key; Automated; Preventive; Daily; owner …`` tag for a control."""
    parts: list[str] = []
    if control.key_control is not None:
        parts.append("key" if control.key_control else "non-key")
    for value in (control.nature, control.control_type, control.frequency):
        if value is not None:
            parts.append(str(value))
    if control.control_owner:
        parts.append(f"owner {control.control_owner}")
    return "; ".join(parts)


def racm_summary(racm: Any) -> str:
    """One line per risk and control — IDs, ratings, attributes and mappings;
    no test procedures."""
    if racm is None:
        return "(no RACM available)"
    lines: list[str] = []
    for risk in racm.risks:
        mapping = ", ".join(risk.regulatory_mapping)
        rating = ""
        if risk.likelihood or risk.impact:
            rating = (
                f" (likelihood {risk.likelihood or '?'}, impact {risk.impact or '?'})"
            )
        lines.append(f"{risk.risk_id}: {risk.description}{rating} [{mapping}]")
        for control in risk.controls:
            attrs = _control_attributes(control)
            tag = f" [{attrs}]" if attrs else ""
            lines.append(f"  - {control.control_id}{tag}: {control.description}")
    return "\n".join(lines) or "(RACM contains no risks)"


def _numbered_steps(steps: Any) -> str:
    return " ".join(
        f"{i}. {s.step_description} -> expect: {s.expected_result}"
        for i, s in enumerate(steps or [], 1)
    )


def racm_test_plan(racm: Any) -> str:
    """Compact per-control test plan for Fieldwork: attributes, ToD / ToE /
    substantive steps and the ToE test design (population, sample, period).

    Plain text rather than the RACM JSON: every fieldwork agent needs it, and
    JSON keys and nulls would roughly double its size in each prompt.
    """
    if racm is None:
        return "(no RACM available)"
    lines: list[str] = []
    for risk in racm.risks:
        for c in risk.controls:
            tp = c.testing_procedures
            lines.append(
                f"Control {c.control_id} (risk {risk.risk_id}): {c.description}"
            )
            attrs = _control_attributes(c)
            extra = []
            if c.assertions:
                extra.append("objectives: " + ", ".join(c.assertions))
            if c.ipe:
                extra.append("IPE: " + ", ".join(c.ipe))
            detail = "; ".join(x for x in [attrs, *extra] if x)
            if detail:
                lines.append(f"  Attributes: {detail}")
            lines.append(f"  ToD: {_numbered_steps(tp.test_of_design) or '(none)'}")
            design: list[str] = []
            if tp.population is not None:
                design.append(
                    f"population: {tp.population.source} "
                    f"(completeness: {tp.population.completeness_procedure})"
                )
            if tp.sample_size is not None or tp.sampling_method is not None:
                size = tp.sample_size if tp.sample_size is not None else "?"
                method = f", {tp.sampling_method}" if tp.sampling_method else ""
                design.append(f"sample: {size}{method}")
            if tp.period_of_reliance:
                design.append(f"period: {tp.period_of_reliance}")
            toe = _numbered_steps(tp.test_of_effectiveness) or "(none)"
            lines.append(
                f"  ToE: {toe}" + (f" | {'; '.join(design)}" if design else "")
            )
            if tp.substantive_testing:
                lines.append(
                    f"  Substantive: {_numbered_steps(tp.substantive_testing)}"
                )
    return "\n".join(lines) or "(RACM contains no controls)"


def findings_index(papers: Any) -> str:
    """``control_id | result (ToD …; ToE …) | vault_id`` per finding, for OSCAL
    mapping."""
    if papers is None or not papers.findings:
        return "(no findings)"
    return "\n".join(
        f"{f.control_id} | {f.result} (ToD {f.tod_conclusion}; ToE "
        f"{f.toe_conclusion}) | {f.vault_id_reference or '(no evidence)'}"
        for f in papers.findings
    )


# Scope terms that make an engagement an ICFR / SOX one, so deficiencies are
# classified on the control deficiency / significant deficiency / material
# weakness scale instead of a risk rating. Deliberately not matched: "PCAOB"
# and "COSO" (in the API's default framework list for every session) and
# SOC 1 / ISAE 3402 (service-auditor reports, which use a different model).
_ICFR_SCOPE = re.compile(
    r"\b(sox|sarbanes|icfr|internal control over financial reporting"
    r"|financial reporting|financial statements?|section 404)\b",
    re.IGNORECASE,
)


def deficiency_scale_for_scope(
    theme: str, business_context: str, frameworks: list[str]
) -> DeficiencyScale:
    """ICFR scale for SOX / financial-reporting scopes, else a risk rating."""
    text = " ".join([theme or "", business_context or "", *frameworks])
    if _ICFR_SCOPE.search(text):
        return DeficiencyScale.ICFR
    return DeficiencyScale.RISK_RATING


def deficiency_scale_guidance(scale: DeficiencyScale) -> str:
    """Prompt text telling the deficiency evaluator which scale to use."""
    if scale == DeficiencyScale.ICFR:
        return (
            f"deficiency_scale = '{scale}' (the scope is ICFR / SOX). Classify "
            "each deficiency as 'Control Deficiency', 'Significant Deficiency' "
            "or 'Material Weakness' (or 'Not a deficiency'). A Material Weakness "
            "needs a reasonable possibility (likelihood Medium/High) that a "
            "material misstatement (magnitude High) is not prevented or detected "
            "on a timely basis; a Significant Deficiency is less severe but "
            "merits the attention of those charged with governance; otherwise "
            "it is a Control Deficiency."
        )
    return (
        f"deficiency_scale = '{scale}' (the scope is not ICFR). Rate each "
        "deficiency 'Low', 'Medium' or 'High' from likelihood x magnitude (or "
        "'Not a deficiency'). Do NOT use SOX terms (significant deficiency, "
        "material weakness) for this scope."
    )


class AuditFlow:
    """
    Orchestrates the three-phase GRC audit: Planning → Fieldwork → Reporting.
    Each phase is gated by a human approval step enforced by AuditStateMachine.

    Every status change goes through ``self.machine``; ``state.status`` is only
    ever written by ``_commit_status`` as a mirror of the machine.
    """

    def __init__(self, initial_status: str = "WAITING_FOR_SCOPE") -> None:
        self.state = AuditState()
        self.machine = AuditStateMachine(AuditStatus(initial_status))
        self.state.status = self.machine.status.value
        self._skill_context: list[Any] = []
        # Session this flow belongs to (set by the API when the flow is cached
        # or loaded). Evidence registered during its crews is bound to it, and
        # the Gate 2 quote check only accepts evidence bound to it (or
        # unbound legacy records). None outside the API: no session check.
        self.session_id: Optional[str] = None

    def _commit_status(self) -> None:
        self.state.status = self.machine.status.value

    # ── Skill context ────────────────────────────────────────────────────────

    def _detect_skills(self) -> None:
        from swarm.skill_loader import detect_skills_from_scope

        scope = f"{self.state.theme} {self.state.business_context}"
        self._skill_context = detect_skills_from_scope(scope)
        # Persist the ids so a reloaded flow runs later phases with the same
        # domain skills (the skill dicts themselves are not serialised).
        self.state.active_skill_ids = [
            s["id"] for s in self._skill_context if isinstance(s.get("id"), str)
        ]

    def restore_skill_context(self) -> None:
        """Rebuild the in-memory skill context from persisted state.

        Uses ``state.active_skill_ids`` when present; snapshots from before the
        ids were persisted fall back to deterministic re-detection from the
        persisted theme and business context.
        """
        from swarm.skill_loader import get_skill_by_id

        resolved: list[Any] = []
        for skill_id in self.state.active_skill_ids:
            skill = get_skill_by_id(skill_id)
            if skill is None:
                logger.warning(
                    "Persisted skill %r no longer exists — skipped", skill_id
                )
                continue
            resolved.append(skill)
        if resolved:
            self._skill_context = resolved
            return
        if self.state.theme or self.state.business_context:
            self._detect_skills()

    def _ensure_skill_context(self) -> None:
        if not self._skill_context:
            self.restore_skill_context()

    # ── Gate helpers (call synchronously before the phase thread) ────────────

    def _stamp_trail(
        self,
        gate: str,
        human_id: str,
        action: str,
        *,
        identity_source: str = IdentitySource.DECLARED.value,
        **extra: str,
    ) -> None:
        """Append a hash-chained entry to the approval trail (append-only).

        ``identity_source`` says whether ``human`` was typed by the caller
        (``declared``) or taken from a reviewer token (``authenticated``,
        ADR-012); like every field, it is covered by the entry's hash.
        """
        entry = {
            "gate": gate,
            "human": human_id,
            "timestamp": _now(),
            "action": action,
            "identity_source": _identity_source(identity_source),
        }
        entry.update(extra)
        audit_trail.append_entry(self.state.approval_trail, entry)

    # ── Generation provenance ────────────────────────────────────────────────

    def _start_generation_run(self, phase: int) -> GenerationRun:
        """Begin recording generation provenance for one phase run.

        Appended to ``state.generation_runs`` immediately (``outcome`` starts
        as ``"running"``) so a run is recorded even if the crew never
        returns; ``_run_crew_with_qa`` fills in ``attempts``, ``ended_at``
        and the final ``outcome`` as the run progresses.

        Uses ``demo_mode_requested`` (never raises) rather than
        ``demo_mode_enabled`` (raises when DEMO_MODE is set in a blocked
        environment): that guard's raise must surface from inside the crew
        build/kickoff try/except in ``_run_crew_with_qa`` exactly as before,
        not here, before a run record even exists.
        """
        demo = demo_mode_requested()
        if demo:
            crew_info = {"provider": "demo", "model": "fixed-content"}
            qa_info = {"provider": "demo", "model": "fixed-content"}
        else:
            from swarm.llm_factory import describe_crew_llm, describe_qa_llm

            try:
                crew_info = describe_crew_llm()
            except Exception:
                # No provider configured: the crew build that follows raises
                # the same error and is handled there (_fail_phase). This
                # run record still gets stamped so the failed attempt has one.
                crew_info = {"provider": "unknown", "model": "unknown"}
            try:
                qa_info = describe_qa_llm()
            except Exception:
                qa_info = {"provider": "unknown", "model": "unknown"}
        run = GenerationRun(
            run_id=str(uuid.uuid4()),
            phase=phase,
            provider=crew_info["provider"],
            model=crew_info["model"],
            qa_provider=qa_info["provider"],
            qa_model=qa_info["model"],
            temperature=0.0 if demo else _CREW_TEMPERATURE,
            qa_temperature=0.0 if demo else _QA_TEMPERATURE,
            crewai_version=_CREWAI_VERSION,
            app_version=_APP_VERSION,
            prompt_fingerprint=prompt_fingerprint(phase, self._skill_context),
            started_at=_now(),
            demo_mode=demo,
        )
        self.state.generation_runs.append(run)
        return run

    def _latest_run_for_phase(self, phase: int) -> Optional[GenerationRun]:
        for run in reversed(self.state.generation_runs):
            if run.phase == phase:
                return run
        return None

    def _run_provenance_extra(self, phase: int) -> dict[str, str]:
        """Trail-entry fields tying an action to the generation run it acted
        on: the run id, its prompt fingerprint, and its provider/model —
        enough to say exactly what was reviewed (see ADR-010). Empty when no
        run has been recorded yet for this phase (e.g. a snapshot from
        before this field existed)."""
        run = self._latest_run_for_phase(phase)
        if run is None:
            return {}
        return {
            "generation_run_id": run.run_id,
            "generation_prompt_fingerprint": run.prompt_fingerprint,
            "generation_model": f"{run.provider}/{run.model}",
        }

    def verify_trail(self, anchor: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Verify the approval trail's hash chain (see :mod:`swarm.trail`).

        Also reports gate-approved artifacts that changed after approval.
        """
        return audit_trail.verify_trail(
            self.state.approval_trail,
            anchor=anchor,
            artifacts={f: getattr(self.state, f) for f in _ARTIFACT_FIELDS.values()},
            decisions=self.state.review_decisions,
        )

    def record_preparer(
        self,
        prepared_by: str,
        *,
        require_review_decisions: bool = False,
        identity_source: str = IdentitySource.DECLARED.value,
    ) -> None:
        """Set the audit's preparer and record it as the trail's first entry.

        Call once, when the audit is created (before ``begin_phase_1``).
        ``require_review_decisions`` turns on the reviewer-decision gate
        preconditions for this audit (ADR-011); the API always sets it. It
        is recorded in the chained ``audit_created`` entry, so editing the
        stored flag does not lift it. ``identity_source`` records whether
        the preparer's name was typed or taken from a reviewer token.

        Raises:
            ValueError: if ``prepared_by`` is blank or a preparer is already set.
        """
        prepared_by = _require_text(prepared_by, "prepared_by")
        identity_source = _identity_source(identity_source)
        if self.state.prepared_by:
            raise ValueError("prepared_by is already set for this audit")
        self.state.prepared_by = prepared_by
        extra = (
            {"review_decisions_required": "true"} if require_review_decisions else {}
        )
        self.state.review_decisions_required = require_review_decisions
        self._stamp_trail(
            "Audit created",
            prepared_by,
            "audit_created",
            identity_source=identity_source,
            **extra,
        )

    # ── Reviewer decisions (ADR-011) ─────────────────────────────────────────

    def review_decisions_required(self) -> bool:
        """Whether gate approvals need reviewer decisions on this audit.

        True if the stored flag or the chained ``audit_created`` entry says
        so (either is enough, like the preparer check).
        """
        return self.state.review_decisions_required or any(
            e.get("action") == "audit_created"
            and e.get("review_decisions_required") == "true"
            for e in self.state.approval_trail
        )

    def decision_context(self) -> DecisionContext:
        return DecisionContext(
            racm=self.state.racm_plan,
            papers=self.state.working_papers,
            report=self.state.final_report,
        )

    def effective_view(self) -> EffectiveView:
        """AI drafts plus the active reviewer decisions (the conclusion of record)."""
        return effective_view(
            self.decision_context(),
            self.state.review_decisions,
            decisions_required=self.review_decisions_required(),
        )

    def decision_states(self) -> dict[str, str]:
        """``decision_id`` -> active / superseded / stale."""
        return dict(
            decision_states(self.decision_context(), self.state.review_decisions)
        )

    def record_decision(
        self,
        *,
        decision_type: str,
        subject_id: str,
        decided_by: str,
        values: Optional[dict[str, Any]] = None,
        rationale: str = "",
        subject_type: Optional[str] = None,
        supersedes: Optional[str] = None,
        identity_source: str = IdentitySource.DECLARED.value,
    ) -> ReviewDecision:
        """Append one reviewer decision and stamp it in the trail.

        Validation and policy are in
        :func:`swarm.review_decisions.build_decision`; nothing is stored or
        stamped when it raises. The AI drafts are never changed.
        ``identity_source`` (declared | authenticated) is stored on the
        decision and on its trail entry.

        Raises:
            DecisionValidationError: bad input (HTTP 422).
            DecisionConflictError: wrong status / policy / supersede conflict
                (HTTP 409).
            SegregationOfDutiesError: the preparer may not record this type.
        """
        decision = build_decision(
            self.decision_context(),
            status=self.machine.status.value,
            preparers=self._preparers(),
            decisions=self.state.review_decisions,
            decision_type=decision_type,
            subject_id=subject_id,
            decided_by=decided_by,
            values=values,
            rationale=rationale,
            subject_type=subject_type,
            supersedes=supersedes,
            identity_source=identity_source,
            now=_now(),
        )
        self.state.review_decisions.append(decision)
        extra = {
            "phase": str(decision.phase),
            "decision_id": decision.decision_id,
            "decision_digest": audit_trail.decision_digest(decision),
            "decision_type": str(decision.decision_type),
            "subject": f"{decision.subject_type}:{decision.subject_id}",
            "artifact": decision.artifact,
        }
        if decision.supersedes:
            extra["supersedes"] = decision.supersedes
        self._stamp_trail(
            f"Review decision ({_PHASE_LABELS[decision.phase]})",
            decision.decided_by,
            "review_decision",
            identity_source=str(decision.identity_source),
            **extra,
        )
        return decision

    def _phase_decisions(self, phase: int) -> list[ReviewDecision]:
        return [d for d in self.state.review_decisions if d.phase == phase]

    def _check_review_decisions(self, gate: int) -> None:
        if gate not in (2, 3) or not self.review_decisions_required():
            return
        missing = self.effective_view().missing_for_gate.get(str(gate), [])
        if missing:
            listed = "; ".join(
                f"{m.subject_type} {m.subject_id}: {' or '.join(m.required)}"
                for m in missing
            )
            raise MissingReviewDecisionsError(
                f"{_GATE_LABELS[gate]} needs reviewer decisions first: {listed}.",
                [m.model_dump() for m in missing],
            )

    def _preparers(self) -> list[str]:
        """The declared preparer, from state and from the chained trail entry.

        Both are checked, so editing ``prepared_by`` in a stored snapshot does
        not lift the preparer restriction while the trail still records them.
        """
        names = [self.state.prepared_by]
        names += [
            e.get("human", "")
            for e in self.state.approval_trail
            if e.get("action") == "audit_created"
        ]
        return [n for n in names if n] or [""]

    def _check_sod(self, action: str, human_id: str, gate: Optional[int] = None):
        for preparer in self._preparers():
            violation = sod_violation(
                action, human_id, preparer, self.state.approval_trail, gate=gate
            )
            if violation:
                raise SegregationOfDutiesError(violation)

    def _require_transition(self, target: AuditStatus, source: AuditStatus) -> None:
        if not self.machine.can(target, source):
            raise InvalidTransitionError(self.machine.status, target)

    def unverified_evidence(self) -> list[str]:
        """Control IDs of working-paper findings whose quote does not verify."""
        papers = self.state.working_papers
        if papers is None:
            return []
        return unverified_findings(
            getattr(papers, "findings", None) or [], session_id=self.session_id
        )

    def _accepted_unverified(self) -> set[str]:
        """Unverified controls a supervisor accepted for the *current* papers.

        Only a Fieldwork QA override whose recorded artifact digest matches
        the working papers as they are now counts, so an override can never
        carry over to a re-run or edited set of working papers.
        """
        papers = self.state.working_papers
        if papers is None:
            return set()
        digest = audit_trail.artifact_digest(papers)
        for e in reversed(self.state.approval_trail):
            if (
                e.get("action") == "qa_override"
                and e.get("gate") == f"QA Override ({_PHASE_LABELS[2]})"
                and e.get("artifact_digest") == digest
            ):
                raw = e.get("unverified_controls", "")
                return {c for c in raw.split(",") if c}
        return set()

    def _check_evidence_for_gate_2(self) -> None:
        unverified = self.unverified_evidence()
        accepted = self._accepted_unverified() if unverified else set()
        not_accepted = [c for c in unverified if c not in accepted]
        if not_accepted:
            raise UnverifiedEvidenceError(
                "Gate 2 cannot be approved: evidence quotes for "
                + ", ".join(not_accepted)
                + " do not verify against the evidence vault. Return the "
                "fieldwork for rework, or have the evidence restored."
            )

    def _approve_gate(
        self,
        gate: int,
        human_id: str,
        identity_source: str = IdentitySource.DECLARED.value,
    ) -> None:
        """Validate, apply policy, transition, then stamp — in that order.

        Nothing is transitioned or stamped when any check fails.
        """
        human_id = _require_text(human_id, "human_id")
        identity_source = _identity_source(identity_source)
        target = {
            1: AuditStatus.RUNNING_PHASE_2,
            2: AuditStatus.RUNNING_PHASE_3,
            3: AuditStatus.COMPLETED,
        }[gate]
        self._require_transition(target, AuditStatus(f"WAITING_HUMAN_GATE_{gate}"))
        self._check_sod("gate_approval", human_id, gate=gate)
        if gate == 2:
            self._check_evidence_for_gate_2()
        field = _ARTIFACT_FIELDS[gate]
        artifact = getattr(self.state, field)
        if artifact is None:
            raise PhaseArtifactMissingError(
                f"{_GATE_LABELS[gate]} has no {field} to approve."
            )
        self._check_review_decisions(gate)

        # The approval seals the artifact and the decisions of this phase
        # recorded so far (a later change to either is reported by
        # verify_trail as a change since approval).
        sealed: dict[str, str] = {}
        if gate in (2, 3):
            phase_decisions = self._phase_decisions(gate)
            sealed = {
                "decisions_digest": audit_trail.decisions_digest(phase_decisions),
                "decisions_count": str(len(phase_decisions)),
            }

        transition = {
            1: self.machine.approve_gate_1,
            2: self.machine.approve_gate_2,
            3: self.machine.approve_gate_3,
        }[gate]
        transition()
        self._commit_status()
        self._stamp_trail(
            _GATE_LABELS[gate],
            human_id,
            "gate_approval",
            identity_source=identity_source,
            artifact=field,
            artifact_digest=audit_trail.artifact_digest(artifact),
            **sealed,
            **self._run_provenance_extra(gate),
        )
        if gate == 3:
            self.state.current_human_dossier = (
                f"Audit completed: the final report was approved at "
                f"{_GATE_LABELS[3]} by {human_id}."
            )
        else:
            self.state.current_human_dossier = (
                f"{_GATE_LABELS[gate]} approved by {human_id}. "
                f"{_PHASE_LABELS[gate + 1]} is running."
            )

    def begin_phase_1(self) -> None:
        """Transition to RUNNING_PHASE_1 — call before spawning the phase 1 thread."""
        self.machine.start_phase_1()
        self._commit_status()
        self.state.current_human_dossier = "Planning is running."

    def begin_phase_2(
        self, human_id: str, identity_source: str = IdentitySource.DECLARED.value
    ) -> None:
        """Gate 1 approval: transition to RUNNING_PHASE_2, then stamp the trail.

        Raises:
            InvalidTransitionError: if the flow is not at WAITING_HUMAN_GATE_1
                (nothing is transitioned or stamped).
            SegregationOfDutiesError: if ``human_id`` prepared the audit.
            PhaseArtifactMissingError: if there is no RACM to approve.
            ValueError: if ``human_id`` is blank.
        """
        self._approve_gate(1, human_id, identity_source)

    def begin_phase_3(
        self, human_id: str, identity_source: str = IdentitySource.DECLARED.value
    ) -> None:
        """Gate 2 approval: transition to RUNNING_PHASE_3, then stamp the trail.

        Re-runs the deterministic evidence check first, then (when the audit
        requires them) checks the reviewer decisions Gate 2 needs.

        Raises:
            InvalidTransitionError: if the flow is not at WAITING_HUMAN_GATE_2.
            SegregationOfDutiesError: if ``human_id`` prepared the audit.
            UnverifiedEvidenceError: if a finding's quote no longer verifies
                (and was not accepted by a supervisor override of these
                exact working papers).
            MissingReviewDecisionsError: if required sign-offs are missing.
            ValueError: if ``human_id`` is blank.
        """
        self._approve_gate(2, human_id, identity_source)

    def finalize_audit(
        self, human_id: str, identity_source: str = IdentitySource.DECLARED.value
    ) -> None:
        """Gate 3 approval: mark the audit COMPLETED, then stamp the trail.

        Raises:
            InvalidTransitionError: if the flow is not at WAITING_HUMAN_GATE_3.
            SegregationOfDutiesError: if ``human_id`` prepared the audit or
                approved Gate 2 (see ``review_policy.DISTINCT_APPROVER_GATES``).
            MissingReviewDecisionsError: if required classifications, scope
                limitations or the engagement conclusion are missing.
            ValueError: if ``human_id`` is blank.
        """
        self._approve_gate(3, human_id, identity_source)

    def retry_phase(
        self,
        phase: int,
        human_id: str,
        identity_source: str = IdentitySource.DECLARED.value,
    ) -> None:
        """Re-open a QA-rejected or errored phase: → RUNNING_PHASE_n.

        The caller then runs the matching ``generate_*`` method. The retry is
        recorded in the approval trail. Anyone, including the preparer, may
        retry: a retry re-runs the preparation, it does not review it.

        Raises:
            InvalidTransitionError: unless the flow is in QA_REJECTED_PHASE_n
                or ERROR_PHASE_n.
            ValueError: if ``human_id`` is blank or ``phase`` is not 1-3.
        """
        if phase not in _PHASE_LABELS:
            raise ValueError("phase must be 1, 2, or 3")
        human_id = _require_text(human_id, "human_id")
        identity_source = _identity_source(identity_source)
        previous = self.machine.status.value
        self.machine.retry_phase(phase)
        self._commit_status()
        self._stamp_trail(
            f"Retry ({_PHASE_LABELS[phase]})",
            human_id,
            "retry",
            identity_source=identity_source,
            previous_status=previous,
            previous_reason=self.state.qa_rejection_reason or "",
            **self._run_provenance_extra(phase),
        )
        self.state.current_human_dossier = (
            f"{_PHASE_LABELS[phase]} retry requested by {human_id}; the phase is "
            "running again."
        )

    def return_for_rework(
        self,
        phase: int,
        human_id: str,
        notes: str,
        identity_source: str = IdentitySource.DECLARED.value,
    ) -> None:
        """Reviewer returns the phase at its gate: WAITING_HUMAN_GATE_n → RUNNING_PHASE_n.

        The review notes are recorded in the approval trail and fed to the
        re-run as feedback (same crew input the QA retries use). The caller
        then runs the matching ``generate_*`` method.

        Raises:
            InvalidTransitionError: unless the flow is at WAITING_HUMAN_GATE_n.
            SegregationOfDutiesError: if ``human_id`` prepared the audit.
            ValueError: if ``human_id``/``notes`` are blank or ``phase`` invalid.
        """
        if phase not in _PHASE_LABELS:
            raise ValueError("phase must be 1, 2, or 3")
        human_id = _require_text(human_id, "human_id")
        notes = _require_text(notes, "notes")
        identity_source = _identity_source(identity_source)
        self._require_transition(
            AuditStatus(f"RUNNING_PHASE_{phase}"),
            AuditStatus(f"WAITING_HUMAN_GATE_{phase}"),
        )
        self._check_sod("return_for_rework", human_id)
        field = _ARTIFACT_FIELDS[phase]
        artifact = getattr(self.state, field)
        self.machine.return_for_rework(phase)
        self._commit_status()
        extra = {"notes": notes, "artifact": field}
        if artifact is not None:
            extra["artifact_digest"] = audit_trail.artifact_digest(artifact)
        extra.update(self._run_provenance_extra(phase))
        self._stamp_trail(
            f"Return for rework ({_PHASE_LABELS[phase]})",
            human_id,
            "return_for_rework",
            identity_source=identity_source,
            **extra,
        )
        self.state.qa_rejection_reason = None
        self.state.current_human_dossier = (
            f"{_PHASE_LABELS[phase]} returned for rework by {human_id}: {notes} "
            "The phase is re-running with these review notes."
        )

    def override_qa_rejection(
        self,
        phase: int,
        human_id: str,
        reason: str,
        identity_source: str = IdentitySource.DECLARED.value,
    ) -> None:
        """Supervisor override: accept a QA-rejected artifact as-is.

        Moves QA_REJECTED_PHASE_n → WAITING_HUMAN_GATE_n, so the normal human
        gate approval still follows. The override, the approver and the
        justification are recorded in the approval trail together with the QA
        rejection being overridden, the digest of the accepted artifact and
        (Fieldwork) the controls whose evidence quotes did not verify.

        Raises:
            InvalidTransitionError: unless the flow is in QA_REJECTED_PHASE_n.
            SegregationOfDutiesError: if ``human_id`` prepared the audit.
            PhaseArtifactMissingError: if the phase produced no artifact to accept.
            ValueError: if ``human_id``/``reason`` are blank or ``phase`` invalid.
        """
        if phase not in _PHASE_LABELS:
            raise ValueError("phase must be 1, 2, or 3")
        human_id = _require_text(human_id, "human_id")
        reason = _require_text(reason, "reason")
        identity_source = _identity_source(identity_source)
        target = AuditStatus(f"WAITING_HUMAN_GATE_{phase}")
        self._require_transition(target, AuditStatus(f"QA_REJECTED_PHASE_{phase}"))
        self._check_sod("qa_override", human_id)
        artifact = getattr(self.state, _ARTIFACT_FIELDS[phase])
        if artifact is None:
            raise PhaseArtifactMissingError(
                f"Phase {phase} has no {_ARTIFACT_FIELDS[phase]} to accept — "
                "retry the phase instead."
            )
        overridden = self.state.qa_rejection_reason or ""
        extra = {
            "reason": reason,
            "qa_rejection_reason": overridden,
            "artifact": _ARTIFACT_FIELDS[phase],
            "artifact_digest": audit_trail.artifact_digest(artifact),
        }
        unverified = self.unverified_evidence() if phase == 2 else []
        if unverified:
            extra["unverified_controls"] = ",".join(unverified)
        extra.update(self._run_provenance_extra(phase))
        self.machine.override_qa(phase)
        self._commit_status()
        self._stamp_trail(
            f"QA Override ({_PHASE_LABELS[phase]})",
            human_id,
            "qa_override",
            identity_source=identity_source,
            **extra,
        )
        self.state.qa_rejection_reason = None
        note = (
            f" Accepted with unverified evidence for: {', '.join(unverified)}."
            if unverified
            else ""
        )
        self.state.current_human_dossier = (
            f"{_PHASE_LABELS[phase]} QA rejection overridden by {human_id}: {reason}."
            f"{note} Review the artifact before approving the gate."
        )

    # ── Shared phase runner ──────────────────────────────────────────────────

    def _carried_feedback(self, phase: int) -> Optional[str]:
        """Feedback text a human-initiated re-run of ``phase`` starts with.

        * a retry out of QA_REJECTED_PHASE_n carries the QA rejection reason
          (a retry after a crew error carries an error message, not feedback);
        * a return for rework carries the reviewer's notes.

        Only the most recent trail entry counts, so an old retry or return
        never leaks into a later run. The trail is persisted, so this also
        survives an API restart between the action and the run.
        """
        if not self.state.approval_trail:
            return None
        last = self.state.approval_trail[-1]
        label = _PHASE_LABELS[phase]
        if (
            last.get("action") == "return_for_rework"
            and last.get("gate") == f"Return for rework ({label})"
        ):
            notes = (last.get("notes") or "").strip()
            return _REWORK_FEEDBACK.format(notes=notes) if notes else None
        if (
            last.get("action") != "retry"
            or last.get("gate") != f"Retry ({label})"
            or last.get("previous_status") != f"QA_REJECTED_PHASE_{phase}"
        ):
            return None
        reason = (last.get("previous_reason") or "").strip()
        if not reason:
            return None
        return _QA_FEEDBACK[phase][1].format(reason=reason)

    def _seed_feedback(self, phase: int, inputs: dict[str, Any]) -> None:
        feedback = self._carried_feedback(phase)
        if feedback:
            inputs[_QA_FEEDBACK[phase][0]] = feedback

    def _fail_phase(self, phase: int, reason: str) -> None:
        self.machine.error_phase(phase)
        self._commit_status()
        self.state.qa_rejection_reason = reason
        self.state.current_human_dossier = (
            f"{_PHASE_LABELS[phase]} stopped with an error: {reason} "
            "Retry the phase once the cause is fixed."
        )

    def _evidence_rejection(self, phase: int, artifact: Any) -> Optional[str]:
        """Deterministic check of the Fieldwork artifact's evidence quotes.

        None when it passes (or does not apply). Fails closed: an unexpected
        error while checking is a rejection.
        """
        if phase != 2 or artifact is None:
            return None
        try:
            unverified = unverified_findings(
                getattr(artifact, "findings", None) or [], session_id=self.session_id
            )
        except Exception as exc:
            logger.exception("Evidence check failed to run")
            return f"Deterministic evidence check could not run ({exc}); fails closed."
        if not unverified:
            return None
        return _EVIDENCE_REJECTION.format(controls=", ".join(unverified))

    def _run_crew_with_qa(
        self,
        phase: int,
        build_crew: Callable[[], Any],
        inputs: dict[str, Any],
        *,
        qa_task: str,
        artifact_task: str,
    ) -> bool:
        """Run a phase crew with one automatic QA-driven retry.

        After the QA agent, Fieldwork artifacts also go through the
        deterministic evidence check; a failure there counts as a QA
        rejection (same retry-once path, same supervisor override).

        On success stores the artifact in state and returns True, leaving the
        machine in RUNNING_PHASE_n for the caller to complete. On failure it
        has already transitioned to QA_REJECTED_PHASE_n / ERROR_PHASE_n and
        returns False.
        """
        label = _PHASE_LABELS[phase]
        field = _ARTIFACT_FIELDS[phase]
        feedback_key, feedback_template = _QA_FEEDBACK[phase]
        # Feedback the run started with (reviewer notes / earlier QA reason)
        # is kept when an auto-retry adds the new QA reason.
        seeded_feedback = str(inputs.get(feedback_key, "") or "")
        rejection: Optional[str] = None
        qa_rejected = False
        artifact: Any = None
        # Generation provenance for this attempted run (ADR-010): started
        # now, filled in as the run progresses, and never removed even if
        # every attempt fails.
        gen_run = self._start_generation_run(phase)

        for attempt in range(1, _MAX_QA_ATTEMPTS + 1):
            gen_run.attempts = attempt
            run = "crew" if attempt == 1 else "crew retry"
            try:
                with evidence_session(self.session_id):
                    result = build_crew().kickoff(inputs=inputs)
                adapter = CrewResultAdapter(result)
                qa_output = adapter.get(qa_task).pydantic
                artifact = adapter.get(artifact_task).pydantic
            except Exception as exc:
                logger.exception("%s %s failed", label, run)
                gen_run.ended_at = _now()
                gen_run.outcome = "error"
                self._fail_phase(phase, f"{label} {run} error: {exc}")
                return False

            qa_reason = _qa_rejection(qa_output)
            evidence_reason = self._evidence_rejection(phase, artifact)
            qa_rejected = qa_reason is not None
            reasons = [r for r in (qa_reason, evidence_reason) if r]
            rejection = " ".join(reasons) if reasons else None
            if rejection is None:
                break
            if attempt < _MAX_QA_ATTEMPTS:
                logger.warning(
                    "%s QA rejected (attempt %d) — auto-retrying with feedback: %s",
                    label,
                    attempt,
                    rejection,
                )
                inputs[feedback_key] = seeded_feedback + feedback_template.format(
                    reason=rejection
                )
        else:
            logger.error(
                "%s QA rejected again after auto-retry — no further retries; "
                "phase %d halted in QA_REJECTED_PHASE_%d: %s",
                label,
                phase,
                phase,
                rejection,
            )
            # Keep the rejected draft so a supervisor can review / override it.
            # Replace (or clear) any older draft so an override can never
            # accept an artifact from a previous run of this phase.
            try:
                setattr(self.state, field, artifact)
            except Exception:
                logger.warning("Rejected %s draft failed validation", field)
                setattr(self.state, field, None)
            gen_run.ended_at = _now()
            gen_run.outcome = "qa_rejected"
            self.machine.reject_phase(phase)
            self._commit_status()
            self.state.qa_rejection_reason = rejection
            by = (
                "rejected by the QA reviewer"
                if qa_rejected
                else "failed the deterministic evidence check"
            )
            self.state.current_human_dossier = (
                f"{label} draft {by} after one automatic retry: {rejection} "
                "Review the draft, then retry the phase or approve it with a "
                "written justification."
            )
            return False

        if artifact is None:
            gen_run.ended_at = _now()
            gen_run.outcome = "error"
            self._fail_phase(
                phase,
                f"{label} crew produced no {field}: the output could not be "
                "parsed into the required schema.",
            )
            return False
        try:
            setattr(self.state, field, artifact)
        except Exception as exc:
            logger.exception("%s artifact failed validation", label)
            gen_run.ended_at = _now()
            gen_run.outcome = "error"
            self._fail_phase(phase, f"{label} crew produced an invalid {field}: {exc}")
            return False
        gen_run.ended_at = _now()
        gen_run.outcome = "qa_approved"
        return True

    # ── Crew construction ────────────────────────────────────────────────────

    def _build_crew(self, phase: int, event_callback: Any = None) -> Any:
        """The phase crew, or its fixed-output stand-in when DEMO_MODE is on.

        Only the crew is swapped in demo mode; QA gating, the state machine
        and the approval trail run exactly as for a real crew.
        """
        if demo_mode_enabled():  # raises in production/staging
            return DemoCrew(
                phase,
                event_callback=event_callback,
                reject=demo_reject_phase() == phase and not self._was_retried(phase),
            )
        crew_cls = {1: PlanningCrew, 2: FieldworkCrew, 3: ReportingCrew}[phase]
        return crew_cls(
            event_callback=event_callback, skill_context=self._skill_context
        ).crew()

    def _was_retried(self, phase: int) -> bool:
        gate = f"Retry ({_PHASE_LABELS[phase]})"
        return any(
            e.get("action") == "retry" and e.get("gate") == gate
            for e in self.state.approval_trail
        )

    # ── Crew inputs ──────────────────────────────────────────────────────────
    # Every ``{placeholder}`` in the crews' YAML task/agent configs must be a
    # key here (CrewAI raises on a missing one; tests/test_prompt_inputs.py
    # checks all three crews).

    def _scope_string(self) -> str:
        return (
            f"Theme: {self.state.theme}. "
            f"Business context: {self.state.business_context}. "
            f"Frameworks: {', '.join(self.state.frameworks) or 'n/a'}."
        )

    def _planning_inputs(self) -> dict[str, Any]:
        return {
            "theme": self.state.theme,
            "business_context": self.state.business_context,
            "frameworks": ", ".join(self.state.frameworks),
            "qa_feedback": "",
        }

    def _fieldwork_inputs(self) -> dict[str, Any]:
        return {
            # The working papers' theme field.
            "theme": self.state.theme,
            # Compact per-control test plan (attributes, steps, population,
            # sample, period) shared by the collector, evaluator and QA.
            "test_plan": racm_test_plan(self.state.racm_plan),
            "qa_feedback": "",
        }

    def _reporting_inputs(self) -> dict[str, Any]:
        papers = self.state.working_papers
        return {
            "theme": self.state.theme,
            "scope_string": self._scope_string(),
            # Compact RACM (risks → controls, no test steps): the writer needs
            # the control universe, not the procedures, and the full RACM plus
            # the working papers would overflow low-TPM providers.
            "racm_summary": racm_summary(self.state.racm_plan),
            "working_papers_string": (
                papers.model_dump_json(exclude_none=True) if papers else ""
            ),
            # Small control_id → vault_id index for the OSCAL mapper, which
            # otherwise only sees the narrative draft via task context.
            "findings_index": findings_index(papers),
            # Which classification scale the engagement-level deficiency
            # evaluation uses (ICFR vs risk rating), decided from the scope.
            "deficiency_scale_guidance": deficiency_scale_guidance(
                deficiency_scale_for_scope(
                    self.state.theme,
                    self.state.business_context,
                    self.state.frameworks,
                )
            ),
            "tone_qa_feedback": "",
        }

    # ── Phase runners ────────────────────────────────────────────────────────

    def generate_planning(self, event_callback=None):
        """Phase 1 — Run the Planning Crew to produce a RACM."""
        # Handle a direct call where begin_phase_1 wasn't called first
        if self.machine.status == AuditStatus.WAITING_FOR_SCOPE:
            self.machine.start_phase_1()
            self._commit_status()
        if self.machine.status != AuditStatus.RUNNING_PHASE_1:
            raise RuntimeError(
                f"Cannot start Planning (status={self.machine.status.value})"
            )

        self._detect_skills()
        logger.info("Starting Planning Phase...")
        self.state.qa_rejection_reason = None

        inputs = self._planning_inputs()
        # A human retry of a QA-rejected run starts with that rejection as
        # feedback, so the crew does not repeat the same mistake.
        self._seed_feedback(1, inputs)

        ok = self._run_crew_with_qa(
            1,
            lambda: self._build_crew(1, event_callback),
            inputs,
            qa_task="qa_gate_task",
            artifact_task="racm_drafting_task",
        )
        if not ok:
            return

        self.machine.complete_phase_1()
        self._commit_status()
        self.state.current_human_dossier = (
            "Planning QA Loop complete. No major structural flaws found. "
            "Please review the RACM below for final Standard 12.3 (formerly IIA 2340) approval."
        )

    def generate_fieldwork(self, event_callback=None):
        """Phase 2 — Run the Fieldwork Crew to produce Working Papers.

        Requires begin_phase_2(human_id) (or retry_phase(2, …)) to have already
        transitioned the machine to RUNNING_PHASE_2 — this method does not
        auto-approve the gate itself.
        """
        if self.machine.status != AuditStatus.RUNNING_PHASE_2:
            raise RuntimeError(
                f"Cannot start Fieldwork: Gate 1 not approved "
                f"(status={self.machine.status.value})"
            )

        self._ensure_skill_context()
        logger.info("Starting Fieldwork Execution Phase...")
        self.state.qa_rejection_reason = None

        if self.state.racm_plan is None:
            self._fail_phase(2, "Cannot run Fieldwork: no Phase 1 RACM in state.")
            return
        inputs = self._fieldwork_inputs()
        # A human retry of a QA-rejected run starts with that rejection as
        # feedback, so the crew does not repeat the same mistake.
        self._seed_feedback(2, inputs)

        ok = self._run_crew_with_qa(
            2,
            lambda: self._build_crew(2, event_callback),
            inputs,
            qa_task="eval_qa_gate_task",
            artifact_task="execution_evaluation_task",
        )
        if not ok:
            return

        self.machine.complete_phase_2()
        self._commit_status()
        papers = self.state.working_papers
        count = len(getattr(papers, "findings", None) or [])
        self.state.current_human_dossier = (
            f"Fieldwork complete: ToD/ToE conclusions recorded for {count} "
            "finding(s), and every cited evidence quote was verified against the "
            "evidence vault. Review the working papers before approving Gate 2 "
            "(supervision step inspired by IIA Standard 12.3, formerly 2340)."
        )
        if self.review_decisions_required():
            self.state.current_human_dossier += (
                " Record a sign-off or a challenge for every exception and every "
                "key-control finding before approving."
            )

    def generate_reporting(self, event_callback=None):
        """Phase 3 — Run the Reporting Crew to produce the Final Report.

        Requires begin_phase_3(human_id) (or retry_phase(3, …)) to have already
        transitioned the machine to RUNNING_PHASE_3 — this method does not
        auto-approve the gate itself. On success the machine moves to
        WAITING_HUMAN_GATE_3; call finalize_audit(human_id) to complete the
        audit (Gate 3).
        """
        if self.machine.status != AuditStatus.RUNNING_PHASE_3:
            raise RuntimeError(
                f"Cannot start Reporting: Gate 2 not approved "
                f"(status={self.machine.status.value})"
            )

        self._ensure_skill_context()
        logger.info("Starting Reporting Phase...")
        self.state.qa_rejection_reason = None

        if self.state.working_papers is None:
            self._fail_phase(
                3, "Cannot run Reporting: no Phase 2 working papers in state."
            )
            return
        inputs = self._reporting_inputs()
        # A human retry of a QA-rejected run starts with that rejection as
        # feedback, so the crew does not repeat the same mistake.
        self._seed_feedback(3, inputs)

        ok = self._run_crew_with_qa(
            3,
            lambda: self._build_crew(3, event_callback),
            inputs,
            qa_task="tone_qa_task",
            artifact_task="final_report_assembly_task",
        )
        if not ok:
            return

        self.machine.complete_phase_3()
        self._commit_status()
        self.state.current_human_dossier = (
            "Reporting complete: the final report passed the tone QA review. "
            "Review the report and the proposed deficiency classifications (a "
            "draft for your judgement), then approve Gate 3 to complete the "
            "audit. Gate 3 must be approved by someone other than the Gate 2 "
            "approver."
        )
        if self.review_decisions_required():
            self.state.current_human_dossier += (
                " Before approving, record your classification of each "
                "deficiency, a scope limitation for each untested key control, "
                "and the engagement conclusion."
            )
