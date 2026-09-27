"""
Download formats for audit artifacts: RACM and working papers as .xlsx, the
final report as Markdown, and an OSCAL Assessment Results document as JSON.

Artifact text is model output (and, for the scope, user input), so:

* every spreadsheet cell goes through :func:`sanitize_cell`, which defuses
  spreadsheet formula injection (CSV/DDE injection) and strips characters
  that are illegal in XLSX XML;
* the Markdown report has image links removed (a rendered image link is a
  blind data-exfiltration / SSRF channel).

Each export also states the artifact's review state (e.g. a draft rejected by
QA) so a downloaded file cannot be mistaken for an approved one.

Given the session's effective view (:mod:`swarm.review_decisions`), exports
render the **conclusion of record**: a reviewer's decision where one is
active, the AI draft (labelled "proposed") where none is, and the AI draft
alongside the decision when they differ (ADR-011).
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, Optional

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font

from swarm import review_policy
from swarm.review_decisions import DecisionRef, EffectiveView, FindingView
from swarm.schema import (
    FinalReportSchema,
    ReviewDecision,
    RiskControlMatrixSchema,
    WorkingPaperSchema,
)

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# A cell starting with one of these is interpreted as a formula by Excel /
# LibreOffice / Sheets (tab and CR as well, per OWASP CSV-injection guidance).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

_PHASE_LABELS = {1: "Planning", 2: "Fieldwork", 3: "Reporting"}


def sanitize_cell(value: Any) -> str:
    """Render ``value`` as inert spreadsheet text."""
    text = "" if value is None else str(value)
    text = ILLEGAL_CHARACTERS_RE.sub("", text)
    if text.startswith(_FORMULA_PREFIXES):
        text = "'" + text
    return text


_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)|!\[[^\]]*\]\[[^\]]*\]")
_HTML_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


def sanitize_report(text: str) -> str:
    """Strip Markdown / HTML images from model-written report text."""
    text = _MD_IMAGE.sub("[Image removed for security]", text)
    return _HTML_IMG.sub("[Image removed for security]", text)


@dataclass(frozen=True)
class ExportContext:
    session_id: str
    session_name: str
    status: str

    def artifact_state(self, phase: int) -> str:
        """Human-readable review state of the phase-``phase`` artifact."""
        status = self.status
        if status == f"QA_REJECTED_PHASE_{phase}":
            return "DRAFT REJECTED BY QA — not approved"
        if status in (f"RUNNING_PHASE_{phase}", f"ERROR_PHASE_{phase}"):
            return "Previous draft — this phase is being re-run or failed"
        if status == f"WAITING_HUMAN_GATE_{phase}":
            return f"Awaiting human approval at Gate {phase}"
        return f"Approved at Gate {phase}"


def _slug(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9-]", "", session_id)[:36] or "session"


def export_filename(ctx: ExportContext, stem: str, ext: str) -> str:
    return f"{stem}-{_slug(ctx.session_id)[:8]}.{ext}"


def _write_sheet(wb: Workbook, title: str, headers: list[str], rows: Iterable[list]):
    ws = wb.create_sheet(title)
    ws.append([sanitize_cell(h) for h in headers])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append([sanitize_cell(v) for v in row])
    ws.freeze_panes = "A2"
    return ws


def _info_rows(ctx: ExportContext, phase: int, notes: list[str]) -> list[list[str]]:
    rows = [
        ["Session", ctx.session_name],
        ["Session ID", ctx.session_id],
        ["Session status", ctx.status],
        [f"{_PHASE_LABELS[phase]} artifact", ctx.artifact_state(phase)],
        ["Exported at (UTC)", datetime.now(UTC).isoformat(timespec="seconds")],
    ]
    rows.extend(["Note", n] for n in notes)
    return rows


def _to_bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _steps(steps: Optional[list[Any]]) -> str:
    return "; ".join(
        f"{s.step_description} (Expect: {s.expected_result})" for s in steps or []
    )


def _yes_no(value: Optional[bool]) -> str:
    return "" if value is None else ("Yes" if value else "No")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


RACM_HEADERS = [
    "Risk ID",
    "Risk Description",
    "Likelihood",
    "Impact",
    "Rating Rationale",
    "Regulatory Mapping",
    "Control ID",
    "Control Description",
    "Control Owner",
    "Frequency",
    "Nature",
    "Type",
    "Key Control",
    "Assertions / Objectives",
    "IPE",
    "ToD Steps",
    "ToE Steps",
    "Substantive Steps",
    "Population Source",
    "Population Completeness",
    "Sample Size",
    "Sampling Method",
    "Period of Reliance",
]


def racm_xlsx(racm: RiskControlMatrixSchema, ctx: ExportContext) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)  # type: ignore[arg-type]
    rows = []
    for risk in racm.risks:
        mapping = ", ".join(risk.regulatory_mapping)
        for c in risk.controls:
            tp = c.testing_procedures
            pop = tp.population
            rows.append(
                [
                    risk.risk_id,
                    risk.description,
                    _text(risk.likelihood),
                    _text(risk.impact),
                    _text(risk.rating_rationale),
                    mapping,
                    c.control_id,
                    c.description,
                    _text(c.control_owner),
                    _text(c.frequency),
                    _text(c.nature),
                    _text(c.control_type),
                    _yes_no(c.key_control),
                    "; ".join(c.assertions),
                    "; ".join(c.ipe),
                    _steps(tp.test_of_design),
                    _steps(tp.test_of_effectiveness),
                    _steps(tp.substantive_testing),
                    pop.source if pop else "",
                    pop.completeness_procedure if pop else "",
                    _text(tp.sample_size),
                    _text(tp.sampling_method),
                    _text(tp.period_of_reliance),
                ]
            )
    _write_sheet(wb, "Controls", RACM_HEADERS, rows)
    _write_sheet(
        wb,
        "Export Info",
        ["Field", "Value"],
        _info_rows(ctx, 1, [f"Theme: {racm.theme}"]),
    )
    return _to_bytes(wb)


WORKING_PAPER_HEADERS = [
    "Control ID",
    "ToD Conclusion",
    "ToE Conclusion",
    "ToE Basis",
    "Items Tested",
    "Exceptions Noted",
    "Result",
    "Preliminary Deficiency",
    "Conclusion",
    "Evidence Quote",
    "Vault ID",
    "Quote Verified in Vault",
    "Legacy Severity",
    "Reviewer Decision",
    "Reviewed By (declared)",
    "Reviewed At (UTC)",
    "Reviewer Rationale",
    "AI Draft (where the record differs)",
    "Scope Limitation",
]

_REVIEW_STATUS_TEXT = {
    "signed_off": "Signed off",
    "challenged": "Challenged",
    "not_reviewed": "Not reviewed",
}


def decided_by_text(ref: DecisionRef) -> str:
    """``Name (declared identity), 2026-09-27``."""
    return (
        f"{_one_line(ref.decided_by)} ({_one_line(ref.identity_source)} "
        f"identity), {_one_line(ref.decided_at)[:10]}"
    )


def _draft_text(view: FindingView) -> str:
    if not (view.differs_from_draft and review_policy.SHOW_AI_DRAFT_WHEN_DIFFERENT):
        return ""
    d = view.draft
    return f"ToD {d.tod_conclusion}; ToE {d.toe_conclusion}; result {d.result}"


def working_papers_xlsx(
    papers: WorkingPaperSchema,
    ctx: ExportContext,
    view: Optional[EffectiveView] = None,
) -> bytes:
    from swarm.evidence import EvidenceAssuranceProtocol

    wb = Workbook()
    wb.remove(wb.active)  # type: ignore[arg-type]
    rows = []
    for f in papers.findings:
        if f.vault_id_reference and f.exact_quote_from_evidence:
            verified = EvidenceAssuranceProtocol.verify_exact_quote(
                f.vault_id_reference, f.exact_quote_from_evidence
            )
            verified_text = "Yes" if verified else "No"
        else:
            verified_text = "n/a (no evidence)"
        fv = view.finding(f.control_id) if view else None
        record = fv.effective if fv else None
        review = fv.review if fv else None
        scope = fv.scope_limitation if fv else None
        rows.append(
            [
                f.control_id,
                record.tod_conclusion if record else _text(f.tod_conclusion),
                record.toe_conclusion if record else _text(f.toe_conclusion),
                _text(f.toe_basis),
                _text(f.items_tested),
                _text(f.exceptions_noted),
                record.result if record else _text(f.result),
                _yes_no(
                    record.preliminary_deficiency
                    if record
                    else f.preliminary_deficiency
                ),
                f.test_conclusion,
                f.exact_quote_from_evidence,
                f.vault_id_reference,
                verified_text,
                _text(f.legacy_severity),
                _REVIEW_STATUS_TEXT[fv.review_status] if fv else "Not reviewed",
                review.decided_by if review else "",
                review.decided_at if review else "",
                review.rationale if review else "",
                _draft_text(fv) if fv else "",
                scope.rationale if scope else "",
            ]
        )
    notes = [
        f"Theme: {papers.theme}",
        "Quote Verified in Vault is re-checked at export time: the quote "
        "must appear verbatim in the stored evidence and the record's "
        "digest must match.",
        "Preliminary Deficiency is a fieldwork flag only. Deficiencies are "
        "classified at engagement level in the report's deficiency evaluation.",
        "ToD / ToE / Result show the conclusion of record: the AI draft, unless "
        "a reviewer's challenge withdrew a conclusion (the draft is then shown "
        "under 'AI Draft'). Reviewer identities are declared, not "
        "authenticated.",
    ]
    if any(f.legacy_severity for f in papers.findings):
        notes.append(
            "Legacy Severity: these findings were recorded before ToD/ToE "
            "conclusions existed; their conclusions were derived from the old "
            "severity label on load."
        )
    _write_sheet(wb, "Findings", WORKING_PAPER_HEADERS, rows)
    _write_sheet(
        wb,
        "Export Info",
        ["Field", "Value"],
        _info_rows(ctx, 2, notes),
    )
    return _to_bytes(wb)


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _md_cell(value: Any) -> str:
    return sanitize_report(_one_line(value).replace("|", "\\|"))


def _with_draft(record: Any, draft: Any) -> str:
    if str(record) == str(draft) or not review_policy.SHOW_AI_DRAFT_WHEN_DIFFERENT:
        return str(record)
    return f"{record} (AI draft: {draft})"


def _deficiency_section(
    report: FinalReportSchema,
    ctx: ExportContext,
    view: Optional[EffectiveView] = None,
) -> list[str]:
    decided = (
        [d for d in view.deficiencies if d.classification_decision] if view else []
    )
    if view is None or not decided:
        return _proposed_deficiency_section(report, ctx)
    scale = _one_line(report.deficiency_scale) or "not stated"
    complete = len(decided) == len(view.deficiencies)
    heading = (
        "## Deficiency Evaluation (conclusion of record)"
        if complete
        else "## Deficiency Evaluation (conclusion of record where decided)"
    )
    note = (
        "Classifications are the reviewer's decisions (identities are declared, "
        "not authenticated). Where the reviewer departed from the AI-drafted "
        "evaluation, the draft is shown alongside."
    )
    if not complete:
        note += (
            " Rows marked 'proposed' have no reviewer decision yet: they show the "
            "AI draft, for the auditor's judgement at Gate 3."
        )
    lines = [heading, "", f"> {note} Scale: {scale}.", ""]
    lines += [
        "| ID | Title | Findings | Risks | Likelihood | Magnitude "
        "| Classification | Compensating controls | Rationale |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    by_id = {e.deficiency_id: e for e in report.deficiency_evaluations}
    for d in view.deficiencies:
        e = by_id[d.deficiency_id]
        ref = d.classification_decision
        if ref is not None:
            classification = f"{d.effective.classification} — reviewer: " + (
                decided_by_text(ref)
            )
            if d.effective.classification != d.draft.classification:
                classification += f"; AI draft: {d.draft.classification}"
            rationale = f"Reviewer: {ref.rationale or 'agrees with the draft.'}"
            if d.differs_from_draft:
                rationale += f" AI draft rationale: {e.rationale}"
        else:
            classification = f"{d.draft.classification} (proposed)"
            rationale = e.rationale
        cells = [
            e.deficiency_id,
            e.title,
            ", ".join(e.related_findings),
            ", ".join(e.related_risks),
            _with_draft(d.effective.likelihood, d.draft.likelihood),
            _with_draft(d.effective.magnitude, d.draft.magnitude),
            classification,
            e.compensating_controls,
            rationale,
        ]
        lines.append("| " + " | ".join(_md_cell(c) for c in cells) + " |")
    lines.append("")
    return lines


def _proposed_deficiency_section(
    report: FinalReportSchema, ctx: ExportContext
) -> list[str]:
    scale = _one_line(report.deficiency_scale) or "not stated"
    if ctx.status == "COMPLETED":
        note = (
            "Engagement-level evaluation proposed by the reporting crew; the "
            "report containing it was approved at Gate 3 (see Approval Trail)."
        )
    else:
        note = (
            "Draft engagement-level evaluation proposed by the reporting crew, "
            "for the auditor's judgement at Gate 3."
        )
    lines = [
        "## Deficiency Evaluation (proposed)",
        "",
        f"> {note} Scale: {scale}.",
        "",
    ]
    if not report.deficiency_evaluations:
        lines += ["No deficiencies were proposed.", ""]
        return lines
    lines += [
        "| ID | Title | Findings | Risks | Likelihood | Magnitude "
        "| Classification | Compensating controls | Rationale |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for e in report.deficiency_evaluations:
        cells = [
            e.deficiency_id,
            e.title,
            ", ".join(e.related_findings),
            ", ".join(e.related_risks),
            e.likelihood,
            e.magnitude,
            e.classification,
            e.compensating_controls,
            e.rationale,
        ]
        lines.append("| " + " | ".join(_md_cell(c) for c in cells) + " |")
    lines.append("")
    return lines


def _narrative_note(view: Optional[EffectiveView]) -> list[str]:
    if view is None or not any(d.classification_decision for d in view.deficiencies):
        return []
    return [
        "> AI-drafted narrative. Where it calls a classification proposed, the "
        "conclusion of record is the reviewer's decision in the Deficiency "
        "Evaluation section.",
        "",
    ]


def _engagement_section(view: EffectiveView) -> list[str]:
    ref = view.engagement_conclusion
    if ref is None:
        return []
    return [
        "## Engagement Conclusion",
        "",
        sanitize_report(
            f"**{_one_line(ref.values.get('conclusion'))}** — decided by "
            f"{decided_by_text(ref)}."
        ),
        "",
        sanitize_report(_one_line(ref.rationale)),
        "",
    ]


def _finding_review_section(view: EffectiveView) -> list[str]:
    reviewed = [f for f in view.findings if f.review or f.scope_limitation]
    if not reviewed:
        return []
    lines = [
        "## Reviewer Sign-off of Findings",
        "",
        "> Per-finding review recorded before Gate 2 (and scope limitations "
        "recorded before Gate 3). Identities are declared, not authenticated.",
        "",
        "| Control | Result of record | Review | Reviewer | Rationale |",
        "|---|---|---|---|---|",
    ]
    for f in view.findings:
        ref = f.review
        result = f.effective.result
        if f.differs_from_draft and review_policy.SHOW_AI_DRAFT_WHEN_DIFFERENT:
            result += f" (AI draft: {f.draft.result}; ToD {f.draft.tod_conclusion}, "
            result += f"ToE {f.draft.toe_conclusion})"
        cells = [
            f.control_id,
            result,
            _REVIEW_STATUS_TEXT[f.review_status],
            decided_by_text(ref) if ref else "",
            ref.rationale if ref else "",
        ]
        lines.append("| " + " | ".join(_md_cell(c) for c in cells) + " |")
    lines.append("")
    limited = [f for f in view.findings if f.scope_limitation]
    if limited:
        lines += ["## Scope Limitations", ""]
        for f in limited:
            ref = f.scope_limitation
            if ref is None:  # narrowed for the type checker
                continue
            lines.append(
                sanitize_report(
                    f"- **{_one_line(f.control_id)}** — {_one_line(ref.rationale)} "
                    f"(recorded by {decided_by_text(ref)})"
                )
            )
        lines.append("")
    return lines


_WRITEUP_PARTS = ("criteria", "condition", "cause", "effect", "recommendation")
_AGREEMENT_TEXT = {
    "agree": "Management agrees",
    "partial": "Management partly agrees",
    "disagree": "Management disagrees",
}


def _findings_detail_section(view: EffectiveView) -> list[str]:
    shown = [d for d in view.deficiencies if d.writeup or d.management_response]
    if not shown:
        return []
    lines = ["## Findings and Management Responses", ""]
    for d in shown:
        lines += [
            sanitize_report(f"### {_one_line(d.deficiency_id)} — {_one_line(d.title)}"),
            "",
        ]
        if d.writeup:
            for part in _WRITEUP_PARTS:
                lines.append(
                    sanitize_report(
                        f"- **{part.capitalize()}:** "
                        f"{_one_line(d.writeup.values.get(part))}"
                    )
                )
            lines += [
                "",
                sanitize_report(f"_Written by {decided_by_text(d.writeup)}._"),
                "",
            ]
        ref = d.management_response
        if ref:
            v = ref.values
            agreement = _AGREEMENT_TEXT.get(v.get("agreement", ""), "Response")
            lines.append(
                sanitize_report(
                    f"**Management response** ({agreement.lower()}; received from "
                    f"{_one_line(v.get('received_from'))} on "
                    f"{_one_line(v.get('received_on'))}, transcribed by "
                    f"{decided_by_text(ref)}):"
                )
            )
            lines += ["", "> " + sanitize_report(_one_line(v.get("text"))), ""]
            if v.get("action_owner_role") or v.get("target_date"):
                lines += [
                    sanitize_report(
                        f"- Action owner: {_one_line(v.get('action_owner_role'))}; "
                        f"target date: {_one_line(v.get('target_date'))}"
                    ),
                    "",
                ]
            if ref.rationale:
                label = (
                    "Auditor's rebuttal"
                    if review_policy.management_response_needs_rebuttal(
                        v.get("agreement", "")
                    )
                    else "Auditor's note"
                )
                lines += [
                    sanitize_report(f"**{label}:** {_one_line(ref.rationale)}"),
                    "",
                ]
    return lines


def _trail_line(e: dict[str, str]) -> str:
    entry = (
        f"- **{_one_line(e.get('gate'))}** — {_one_line(e.get('human'))} "
        f"at {_one_line(e.get('timestamp'))}"
    )
    if e.get("action"):
        entry += f" ({_one_line(e['action'])})"
    if e.get("action") == "review_decision":
        entry += (
            f" — {_one_line(e.get('decision_type'))} on {_one_line(e.get('subject'))}"
        )
        if e.get("supersedes"):
            entry += f", superseding {_one_line(e['supersedes'])}"
    if e.get("reason"):
        entry += f" — reason: {_one_line(e['reason'])}"
    if e.get("qa_rejection_reason"):
        entry += f" — overrode QA: {_one_line(e['qa_rejection_reason'])}"
    return sanitize_report(entry)


def report_markdown(
    report: FinalReportSchema,
    trail: list[dict[str, str]],
    ctx: ExportContext,
    view: Optional[EffectiveView] = None,
) -> str:
    lines = [
        sanitize_report(f"# GRC Audit Report — {_one_line(ctx.session_name)}"),
        "",
        f"> Report status: {ctx.artifact_state(3)} (session status {ctx.status}).",
        "",
        "## Executive Summary",
        "",
        sanitize_report(report.executive_summary),
        "",
        "## Detailed Report",
        "",
        *_narrative_note(view),
        sanitize_report(report.detailed_report),
        "",
        *(_engagement_section(view) if view else []),
        *_deficiency_section(report, ctx, view),
        *(_findings_detail_section(view) if view else []),
        *(_finding_review_section(view) if view else []),
        "## Approval Trail",
        "",
    ]
    if not trail:
        lines.append("No approvals recorded.")
    lines += [_trail_line(e) for e in trail]
    return "\n".join(lines) + "\n"


def oscal_json(
    report: FinalReportSchema,
    papers: WorkingPaperSchema,
    racm: Optional[RiskControlMatrixSchema],
    trail: list[dict[str, str]],
    ctx: ExportContext,
    *,
    theme: str = "",
    prepared_by: str = "",
    view: Optional[EffectiveView] = None,
    decisions: Iterable[ReviewDecision] = (),
) -> bytes:
    """The session as an OSCAL Assessment Results document (JSON).

    Built by :mod:`swarm.oscal_ar` from the working papers, RACM, report and
    approval trail; it validates against NIST's official OSCAL
    assessment-results JSON schema (see tests/test_oscal_ar.py).
    """
    from swarm.evidence import EvidenceAssuranceProtocol
    from swarm.oscal_ar import build_assessment_results

    document = build_assessment_results(
        session_id=ctx.session_id,
        session_name=ctx.session_name,
        session_status=ctx.status,
        report_state=ctx.artifact_state(3),
        theme=theme,
        report=report,
        papers=papers,
        racm=racm,
        trail=trail,
        prepared_by=prepared_by,
        view=view,
        decisions=list(decisions),
        markup=sanitize_report,
        verify_quote=EvidenceAssuranceProtocol.verify_exact_quote,
    )
    return json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")
