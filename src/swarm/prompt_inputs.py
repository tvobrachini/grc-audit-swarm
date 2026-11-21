"""Pure prompt and context projection functions for the three audit phases.

Translates audit artifacts (RACM, working papers, active reviewer decisions,
scope metadata) into the compact text and structured input dictionaries that
each crew's YAML prompts interpolate.

This module is intentionally pure: it has no state-machine, crew, or I/O
dependencies.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from swarm.schema import (
    DeficiencyScale,
    DesignConclusion,
    FindingResult,
    OperatingConclusion,
    WorkingPaperSchema,
)

if TYPE_CHECKING:
    from swarm.review_decisions import EffectiveView

__all__ = [
    "deficiency_scale_for_scope",
    "deficiency_scale_guidance",
    "effective_working_papers",
    "fieldwork_inputs",
    "findings_index",
    "planning_inputs",
    "racm_summary",
    "racm_test_plan",
    "reporting_inputs",
    "scope_string",
]


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


def effective_working_papers(
    papers: WorkingPaperSchema | None,
    view: EffectiveView | None,
) -> WorkingPaperSchema | None:
    """Working papers overlaid with active Gate 2 reviewer decisions (`D5`).

    Never mutates ``papers`` (preserving ADR-011's append-only draft
    immutability). When no reviewer ``challenge`` is active on any finding,
    returns ``papers`` directly.
    """
    if papers is None or view is None:
        return papers
    changed = {fv.control_id: fv for fv in view.findings if fv.differs_from_draft}
    if not changed:
        return papers
    overlaid_findings = []
    for f in papers.findings:
        fv = changed.get(f.control_id)
        if fv is None:
            overlaid_findings.append(f)
        else:
            eff = fv.effective
            overlaid_findings.append(
                f.model_copy(
                    update={
                        "tod_conclusion": DesignConclusion(eff.tod_conclusion),
                        "toe_conclusion": OperatingConclusion(eff.toe_conclusion),
                        "result": FindingResult(eff.result),
                        "preliminary_deficiency": eff.preliminary_deficiency,
                    }
                )
            )
    return papers.model_copy(update={"findings": overlaid_findings})


def scope_string(theme: str, business_context: str, frameworks: list[str]) -> str:
    return (
        f"Theme: {theme}. "
        f"Business context: {business_context}. "
        f"Frameworks: {', '.join(frameworks) or 'n/a'}."
    )


def planning_inputs(
    theme: str, business_context: str, frameworks: list[str]
) -> dict[str, Any]:
    return {
        "theme": theme,
        "business_context": business_context,
        "frameworks": ", ".join(frameworks),
        "qa_feedback": "",
    }


def fieldwork_inputs(theme: str, racm_plan: Any) -> dict[str, Any]:
    return {
        # The working papers' theme field.
        "theme": theme,
        # Compact per-control test plan (attributes, steps, population,
        # sample, period) shared by the collector, evaluator and QA.
        "test_plan": racm_test_plan(racm_plan),
        "qa_feedback": "",
    }


def reporting_inputs(
    theme: str,
    business_context: str,
    frameworks: list[str],
    racm_plan: Any,
    papers: WorkingPaperSchema | None,
) -> dict[str, Any]:
    return {
        "theme": theme,
        "scope_string": scope_string(theme, business_context, frameworks),
        # Compact RACM (risks → controls, no test steps): the writer needs
        # the control universe, not the procedures, and the full RACM plus
        # the working papers would overflow low-TPM providers.
        "racm_summary": racm_summary(racm_plan),
        "working_papers_string": (
            papers.model_dump_json(exclude_none=True) if papers else ""
        ),
        # Small control_id → vault_id index for the OSCAL mapper, which
        # otherwise only sees the narrative draft via task context.
        "findings_index": findings_index(papers),
        # Which classification scale the engagement-level deficiency
        # evaluation uses (ICFR vs risk rating), decided from the scope.
        "deficiency_scale_guidance": deficiency_scale_guidance(
            deficiency_scale_for_scope(theme, business_context, frameworks)
        ),
        "tone_qa_feedback": "",
    }
