"""
Convert an audit session into an OSCAL Assessment Results (AR) document.

The reporting crew's ``oscal_sar`` object (``swarm.schema.OSCAL_SAR_Schema``)
is LLM output in Python-style field names. It is not OSCAL. This module builds
a real OSCAL AR document (JSON, ``oscal-version`` :data:`OSCAL_VERSION`) from
the session's gate-reviewed artifacts: the RACM, the working papers, the final
report's deficiency evaluation and the approval trail. From ``oscal_sar`` it
takes only the document title and the per-observation narrative, so model
output can never add controls, evidence IDs or conclusions to the export.

Mapping (see DECISIONS.md, ADR-005):

* ``metadata`` — title, last-modified (the latest approval-trail entry, so
  exports are reproducible), version (derived from the artifacts' digests),
  roles / parties / responsible-parties for the declared preparer, the gate
  approvers and the Gate 3 (report) approver.
* ``import-ap`` — there is no OSCAL assessment plan. ``href`` points at a
  back-matter resource describing the RACM, which served as the plan.
* ``results[0]`` —
  ``reviewed-controls`` (the RACM control IDs),
  ``observations`` (one per working-paper finding; ToD / ToE conclusions as
  namespaced props; evidence as back-matter resources),
  ``findings`` (one per *tested* control: satisfied / not-satisfied),
  ``risks`` (one per deficiency evaluation; the classification prop is the
  conclusion of record — the reviewer's ``classify`` decision with
  ``decided-by`` / ``decided-at`` / ``identity-source`` props, or the AI
  draft marked as such — and the AI draft is always kept in
  ``ai-draft-classification``; the reviewer's five-part write-up and
  recommendation, and management's response as a planned remediation, a
  deadline and a risk-log entry),
  ``attestations`` (the reviewer's engagement conclusion),
  ``assessment-log`` (the approval trail, entry by entry, including each
  reviewer decision's id and digest).

Observations and findings carry the reviewer's per-finding sign-off status
(``reviewer-sign-off``, ``reviewed-by``) and use the conclusions of record; a
conclusion a reviewer withdrew keeps the draft in an ``ai-draft-*`` prop
(ADR-011).

All UUIDs are version-5 UUIDs derived from the session ID and the element, so
exporting the same session twice gives the same document.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, NamedTuple, Optional, Sequence

from swarm.review_decisions import DecisionRef, EffectiveView, FindingView
from swarm.schema import (
    AuditFindingSchema,
    DesignConclusion,
    FinalReportSchema,
    FindingResult,
    OperatingConclusion,
    RiskControlMatrixSchema,
    WorkingPaperSchema,
)

OSCAL_VERSION = "1.2.1"
PROJECT_NS = "https://github.com/tvobrachini/grc-audit-swarm/ns/oscal"
# Root of every UUID in the export (uuid5 under the URL namespace).
_UUID_ROOT = uuid.uuid5(uuid.NAMESPACE_URL, PROJECT_NS)

_METHOD_DESIGN = "EXAMINE"
_METHOD_OPERATING = "TEST"

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_VAULT_ID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_NOT_TOKEN_CHAR = re.compile(r"[^A-Za-z0-9._-]")


# ── Small value helpers ──────────────────────────────────────────────────────


def element_uuid(session_id: str, *parts: str) -> str:
    """Deterministic RFC 4122 version-5 UUID for one element of a session."""
    return str(uuid.uuid5(_UUID_ROOT, "/".join((session_id, *parts))))


def _line(value: Any) -> str:
    """Single line, no edge whitespace (OSCAL string / markup-line)."""
    return "" if value is None else " ".join(str(value).split())


def token(value: str) -> str:
    """Map an identifier onto an OSCAL token (an XML NCName).

    Characters outside ``[A-Za-z0-9._-]`` become ``_``, and a leading
    character that cannot start a token gets a ``_`` prefix.
    """
    text = _NOT_TOKEN_CHAR.sub("_", _line(value)) or "_"
    if not (text[0].isalpha() or text[0] == "_"):
        text = "_" + text
    return text


def _prop(name: str, value: Any, *, remarks: Optional[str] = None) -> dict:
    prop = {"name": name, "ns": PROJECT_NS, "value": _line(value)}
    if remarks:
        prop["remarks"] = remarks
    return prop


def _props(*pairs: tuple[str, Any]) -> list[dict]:
    """Namespaced props for the pairs whose value is not empty."""
    return [_prop(n, v) for n, v in pairs if _line(v)]


def _yes_no(value: Optional[bool]) -> str:
    return "" if value is None else ("true" if value else "false")


def oscal_datetime(value: Any) -> Optional[str]:
    """ISO 8601 with a timezone (UTC assumed when missing), or None."""
    text = _line(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    # UTC throughout: OSCAL's date-time pattern allows only real-world offsets.
    return parsed.astimezone(UTC).isoformat()


def _digest(obj: Any) -> str:
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump(mode="json")
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# ── Evidence vault metadata ──────────────────────────────────────────────────


class EvidenceMeta(NamedTuple):
    collected: Optional[str]
    source: str


def vault_metadata(vault_id: str) -> Optional[EvidenceMeta]:
    """Collection time and source operation of a vault record (read-only).

    Never returns payload or digest: the export cites evidence, it does not
    carry it.
    """
    from swarm.evidence import EvidenceAssuranceProtocol

    if not _VAULT_ID_RE.fullmatch(vault_id or ""):
        return None
    base = os.path.realpath(EvidenceAssuranceProtocol._evidence_dir())
    path = os.path.realpath(os.path.join(base, f"{vault_id}.json"))
    if os.path.commonpath([base, path]) != base:
        return None
    try:
        with open(path) as f:
            record = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    return EvidenceMeta(
        collected=oscal_datetime(record.get("timestamp")),
        source=_line(record.get("mcp_source")),
    )


# ── Builder ──────────────────────────────────────────────────────────────────


class _Parties:
    """metadata.parties keyed by declared name, in order of appearance."""

    def __init__(self, session_id: str):
        self._sid = session_id
        self.by_name: dict[str, str] = {}

    def uuid_for(self, name: str) -> str:
        name = _line(name)
        if name not in self.by_name:
            self.by_name[name] = element_uuid(self._sid, "party", name)
        return self.by_name[name]

    def as_oscal(self) -> list[dict]:
        return [
            {
                "uuid": party_uuid,
                "type": "person",
                "name": name,
                "remarks": (
                    "Declared identity as entered in the application. The API "
                    "does not authenticate individual reviewers; see the "
                    "hash-chained assessment log for what this party did."
                ),
            }
            for name, party_uuid in self.by_name.items()
        ]


def _occurrence_keys(findings: Sequence[AuditFindingSchema]) -> list[str]:
    """``CTRL-01#1``-style keys, stable per control even if order changes."""
    seen: dict[str, int] = {}
    keys = []
    for f in findings:
        seen[f.control_id] = seen.get(f.control_id, 0) + 1
        keys.append(f"{f.control_id}#{seen[f.control_id]}")
    return keys


def _llm_narratives(report: FinalReportSchema) -> dict[str, str]:
    """Reporting-crew observation text per control ID (first one wins)."""
    out: dict[str, str] = {}
    sar = report.oscal_sar
    for result in sar.results if sar else []:
        for obs in result.observations:
            for subject in obs.subjects:
                if subject not in out and _line(obs.description):
                    out[subject] = obs.description
    return out


def _methods(tod: str, toe: str) -> list[str]:
    methods = []
    if tod != DesignConclusion.NOT_TESTED:
        methods.append(_METHOD_DESIGN)
    if toe != OperatingConclusion.NOT_TESTED:
        methods.append(_METHOD_OPERATING)
    # Not tested at all: the auditor examined what evidence was available and
    # found none covering the control (recorded as a scope limitation).
    return methods or [_METHOD_DESIGN]


def build_assessment_results(
    *,
    session_id: str,
    session_name: str,
    session_status: str,
    report_state: str,
    theme: str,
    report: FinalReportSchema,
    papers: WorkingPaperSchema,
    racm: Optional[RiskControlMatrixSchema],
    trail: Sequence[Mapping[str, Any]],
    prepared_by: str = "",
    markup: Callable[[str], str] = lambda s: s,
    verify_quote: Optional[Callable[[str, str], bool]] = None,
    evidence_lookup: Callable[[str], Optional[EvidenceMeta]] = vault_metadata,
    view: Optional[EffectiveView] = None,
    decisions: Sequence[Any] = (),
) -> dict:
    """Build the OSCAL AR document (a JSON-ready dict).

    ``markup`` is applied to every Markdown (markup-line / markup-multiline)
    field; the API passes its image-stripping sanitiser. ``verify_quote``
    re-checks each cited quote against the vault, as the working-papers
    export does. ``view`` is the session's effective view (AI drafts plus
    active reviewer decisions); ``decisions`` are all recorded decisions,
    folded into the document version.
    """
    sid = session_id
    approved = session_status == "COMPLETED"
    parties = _Parties(sid)
    reviewers: list[str] = []

    def _decided_props(ref: Optional[DecisionRef], prefix: str = "") -> list[dict]:
        if ref is None:
            return []
        reviewers.append(_line(ref.decided_by))
        return _props(
            (f"{prefix}decided-by", ref.decided_by),
            (f"{prefix}decided-at", oscal_datetime(ref.decided_at) or ""),
            (f"{prefix}identity-source", ref.identity_source),
            (f"{prefix}decision-id", ref.decision_id),
        )

    def _reviewer_origin(ref: Optional[DecisionRef]) -> list[dict]:
        if ref is None:
            return []
        return [
            {
                "actors": [
                    {
                        "type": "party",
                        "actor-uuid": parties.uuid_for(ref.decided_by),
                        "role-id": "reviewer",
                    }
                ]
            }
        ]

    # ── Timestamps from recorded facts, never the clock ──────────────────────
    trail_times = [oscal_datetime(e.get("timestamp")) for e in trail]
    known_times = [t for t in trail_times if t]
    sar = report.oscal_sar
    last_modified = (
        max(known_times, key=datetime.fromisoformat)
        if known_times
        else (oscal_datetime(sar.metadata.last_modified) if sar else None)
    ) or datetime.now(UTC).isoformat()
    gate2_time = next(
        (
            oscal_datetime(e.get("timestamp"))
            for e in reversed(trail)
            if e.get("action") == "gate_approval"
            and e.get("artifact") == "working_papers"
        ),
        None,
    )

    # ── Evidence → back-matter resources ─────────────────────────────────────
    resources: list[dict] = []
    evidence_uuid: dict[str, str] = {}
    evidence_meta: dict[str, Optional[EvidenceMeta]] = {}
    for f in papers.findings:
        vid = _line(f.vault_id_reference)
        if not vid or vid in evidence_uuid:
            continue
        meta = evidence_lookup(vid)
        evidence_meta[vid] = meta
        evidence_uuid[vid] = element_uuid(sid, "evidence", vid)
        resources.append(
            {
                "uuid": evidence_uuid[vid],
                "title": markup(f"Evidence vault record {vid}"),
                "description": markup(
                    "Raw evidence collected by a read-only tool and stored in "
                    "the project's evidence vault with an integrity digest. "
                    "The payload is not embedded in this document."
                    if meta
                    else "The working paper cites this vault ID, but no such "
                    "record was found in the evidence vault at export time."
                ),
                "props": _props(
                    ("vault-id", vid),
                    ("evidence-source", meta.source if meta else ""),
                    ("collected", meta.collected if meta else ""),
                    ("vault-record-found", _yes_no(meta is not None)),
                ),
            }
        )
    evidence_times = sorted(
        (m.collected for m in evidence_meta.values() if m and m.collected),
        key=datetime.fromisoformat,
    )

    # ── The RACM stands in for the (absent) assessment plan ──────────────────
    plan_uuid = element_uuid(sid, "assessment-plan-reference")
    resources.insert(
        0,
        {
            "uuid": plan_uuid,
            "title": markup(f"Audit programme (RACM) — {_line(theme) or 'audit'}"),
            "description": markup(
                "No OSCAL assessment plan exists for this engagement. The Risk "
                "and Control Matrix approved at Gate 1 (Planning) served as "
                "the plan: the risks, the controls in scope and their test "
                "procedures. It is available as an .xlsx export, not as OSCAL."
            ),
            "rlinks": [
                {
                    "href": f"/api/sessions/{sid}/export/racm.xlsx",
                    "media-type": _XLSX,
                }
            ],
        },
    )

    # ── Subject and platform (result-local definitions) ──────────────────────
    component_uuid = element_uuid(sid, "component", "audit-scope")
    platform_uuid = element_uuid(sid, "assessment-platform")
    local_definitions = {
        "components": [
            {
                "uuid": component_uuid,
                "type": "this-system",
                "title": markup(f"Audit scope: {_line(theme) or 'not stated'}"),
                "description": markup(
                    "The environment in scope for this audit, as described by "
                    "the audit theme. No system security plan was imported, so "
                    "the component is described only by the audit scope."
                ),
                "status": {
                    "state": "other",
                    "remarks": "Operational status was not recorded by the audit.",
                },
            }
        ],
        "assessment-assets": {
            "assessment-platforms": [
                {
                    "uuid": platform_uuid,
                    "title": markup("GRC Audit Swarm"),
                    "props": _props(
                        (
                            "platform-description",
                            "LLM agent crews draft the RACM, working papers and "
                            "report; a human approves each phase at a gate",
                        ),
                    ),
                }
            ]
        },
    }

    # ── Deficiency evaluations → risks ───────────────────────────────────────
    keys = _occurrence_keys(papers.findings)
    obs_uuid = [element_uuid(sid, "observation", k) for k in keys]
    obs_by_control: dict[str, list[str]] = {}
    for f, u in zip(papers.findings, obs_uuid):
        obs_by_control.setdefault(f.control_id, []).append(u)

    scale = _line(report.deficiency_scale)
    risks: list[dict] = []
    risks_by_control: dict[str, list[str]] = {}
    seen_def: dict[str, int] = {}
    for e in report.deficiency_evaluations:
        seen_def[e.deficiency_id] = seen_def.get(e.deficiency_id, 0) + 1
        risk_uuid = element_uuid(
            sid, "risk", f"{e.deficiency_id}#{seen_def[e.deficiency_id]}"
        )
        dv = view.deficiency(e.deficiency_id) if view else None
        ref = dv.classification_decision if dv else None
        classification = dv.effective.classification if dv else str(e.classification)
        likelihood = dv.effective.likelihood if dv else str(e.likelihood)
        magnitude = dv.effective.magnitude if dv else str(e.magnitude)
        if ref is not None:
            state = (
                f"Classification of record: decided by {_line(ref.decided_by)} "
                f"({ref.identity_source} identity) on {_line(ref.decided_at)[:10]}"
                + (
                    "; the report was approved at Gate 3."
                    if approved
                    else "; the report awaits Gate 3 approval."
                )
            )
            if dv is not None and dv.differs_from_draft:
                state += (
                    f" AI draft: {e.classification} (likelihood {e.likelihood}, "
                    f"magnitude {e.magnitude})."
                )
            statement = (
                f"Likelihood {likelihood}, magnitude {magnitude}. Classification "
                f"of record: {classification} ({scale or 'scale not stated'}), "
                f"reviewer decision. Reviewer rationale: "
                f"{ref.rationale or 'agrees with the AI draft.'} AI draft "
                f"({e.classification}) rationale: {e.rationale}"
            )
            classification_state = "reviewer-decision"
        else:
            state = (
                "Proposed by the reporting crew and approved with the report at "
                "Gate 3; see the assessment log."
                if approved
                else "DRAFT proposed by the reporting crew for the auditor's "
                "judgement at Gate 3 — not a final conclusion."
            )
            statement = (
                f"Likelihood {e.likelihood}, magnitude {e.magnitude}. "
                f"Proposed classification: {e.classification} ({scale or 'scale not stated'}). "
                f"Rationale: {e.rationale}"
            )
            classification_state = "approved-with-report" if approved else "draft"
        related_obs = [u for c in e.related_findings for u in obs_by_control.get(c, [])]
        for c in e.related_findings:
            risks_by_control.setdefault(c, []).append(risk_uuid)
        description = (
            f"{state}\n\nFindings evaluated together: "
            f"{', '.join(e.related_findings)}. RACM risks affected: "
            f"{', '.join(e.related_risks) or 'none listed'}.\n\n"
            f"Compensating controls considered: {e.compensating_controls}"
        )
        writeup = dv.writeup if dv else None
        if writeup is not None:
            parts = "\n".join(
                f"- {k.capitalize()}: {_line(writeup.values.get(k))}"
                for k in ("criteria", "condition", "cause", "effect")
            )
            description += (
                f"\n\nFinding write-up by {_line(writeup.decided_by)} "
                f"({writeup.identity_source} identity):\n\n{parts}"
            )
        props = _props(
            ("deficiency-id", e.deficiency_id),
            ("classification", classification),
            ("deficiency-scale", scale),
            ("classification-state", classification_state),
            ("likelihood", likelihood),
            ("magnitude", magnitude),
        )
        if dv is not None:
            props += _props(
                (
                    "classification-source",
                    "reviewer" if ref is not None else "ai-draft",
                ),
                ("ai-draft-classification", e.classification),
            )
            if dv.differs_from_draft:
                props += _props(
                    ("ai-draft-likelihood", e.likelihood),
                    ("ai-draft-magnitude", e.magnitude),
                )
            props += _decided_props(ref)
        risk: dict[str, Any] = {
            "uuid": risk_uuid,
            "title": markup(_line(e.title) or e.deficiency_id),
            "description": markup(description),
            "statement": markup(statement),
            "props": props
            + [_prop("racm-risk-id", r) for r in e.related_risks if _line(r)],
            "status": "open",
        }
        if dv is not None:
            risk["origins"] = [
                {
                    "actors": [
                        {"type": "assessment-platform", "actor-uuid": platform_uuid}
                    ]
                }
            ] + _reviewer_origin(ref)
        response = dv.management_response if dv else None
        if response is not None and response.values.get("target_date"):
            deadline = oscal_datetime(response.values["target_date"])
            if deadline:
                risk["deadline"] = deadline
        remediations: list[dict] = []
        if writeup is not None and _line(writeup.values.get("recommendation")):
            remediations.append(
                {
                    "uuid": element_uuid(sid, "recommendation", risk_uuid),
                    "lifecycle": "recommendation",
                    "title": markup("Auditor's recommendation"),
                    "description": markup(writeup.values["recommendation"]),
                    "props": _decided_props(writeup),
                    "origins": _reviewer_origin(writeup),
                }
            )
        if response is not None:
            v = response.values
            response_props = _props(
                ("management-agreement", v.get("agreement")),
                ("action-owner-role", v.get("action_owner_role")),
                ("target-date", v.get("target_date")),
                ("received-from", v.get("received_from")),
                ("received-on", v.get("received_on")),
            ) + _props(
                ("transcribed-by", response.decided_by),
                ("transcribed-at", oscal_datetime(response.decided_at) or ""),
                ("identity-source", response.identity_source),
                ("decision-id", response.decision_id),
            )
            if v.get("agreement") in ("agree", "partial"):
                remediations.append(
                    {
                        "uuid": element_uuid(sid, "action-plan", risk_uuid),
                        "lifecycle": "planned",
                        "title": markup("Management action plan"),
                        "description": markup(v.get("text", "")),
                        "props": response_props,
                    }
                )
            log_description = (
                f"Management response ({v.get('agreement')}), received from "
                f"{_line(v.get('received_from'))} on {_line(v.get('received_on'))} "
                f"and transcribed by {_line(response.decided_by)} "
                f"({response.identity_source} identity):\n\n{v.get('text', '')}"
            )
            if response.rationale:
                log_description += (
                    f"\n\nAuditor's {'rebuttal' if v.get('agreement') == 'disagree' else 'note'}: "
                    f"{response.rationale}"
                )
            risk["risk-log"] = {
                "entries": [
                    {
                        "uuid": element_uuid(sid, "management-response", risk_uuid),
                        "title": markup("Management response"),
                        "description": markup(log_description),
                        "start": oscal_datetime(v.get("received_on"))
                        or oscal_datetime(response.decided_at)
                        or last_modified,
                        "props": response_props,
                    }
                ]
            }
        if remediations:
            risk["remediations"] = [
                {k: val for k, val in r.items() if val} for r in remediations
            ]
        if related_obs:
            risk["related-observations"] = [
                {"observation-uuid": u} for u in dict.fromkeys(related_obs)
            ]
        risks.append(risk)

    # ── Working papers → observations and findings ───────────────────────────
    narratives = _llm_narratives(report)
    observations: list[dict] = []
    findings: list[dict] = []
    for f, key, o_uuid in zip(papers.findings, keys, obs_uuid):
        vid = _line(f.vault_id_reference)
        meta = evidence_meta.get(vid) if vid else None
        collected = (
            (meta.collected if meta else None)
            or gate2_time
            or (evidence_times[-1] if evidence_times else None)
            or last_modified
        )
        fv: Optional[FindingView] = view.finding(f.control_id) if view else None
        tod = fv.effective.tod_conclusion if fv else str(f.tod_conclusion)
        toe = fv.effective.toe_conclusion if fv else str(f.toe_conclusion)
        result_of_record = fv.effective.result if fv else str(f.result)
        preliminary = (
            fv.effective.preliminary_deficiency if fv else f.preliminary_deficiency
        )
        props = _props(
            ("control-id", f.control_id),
            ("tod-conclusion", tod),
            ("toe-conclusion", toe),
            ("result", result_of_record),
            ("items-tested", f.items_tested),
            ("exceptions-noted", f.exceptions_noted),
            ("preliminary-deficiency", _yes_no(preliminary)),
            ("legacy-severity", f.legacy_severity),
        )
        review_props: list[dict] = []
        if fv is not None:
            if fv.differs_from_draft:
                props += _props(
                    ("ai-draft-tod-conclusion", f.tod_conclusion),
                    ("ai-draft-toe-conclusion", f.toe_conclusion),
                    ("ai-draft-result", f.result),
                )
            review_props = _props(
                ("reviewer-sign-off", fv.review_status.replace("_", "-"))
            )
            if fv.review is not None:
                review_props += _props(
                    ("reviewed-by", fv.review.decided_by),
                    ("reviewed-at", oscal_datetime(fv.review.decided_at) or ""),
                    ("reviewer-identity-source", fv.review.identity_source),
                )
                reviewers.append(_line(fv.review.decided_by))
            if fv.scope_limitation is not None:
                review_props += _props(("scope-limitation", "true"))
                reviewers.append(_line(fv.scope_limitation.decided_by))
            props += review_props
        remarks = []
        if fv is not None and fv.review is not None and fv.review.rationale:
            label = (
                "Reviewer challenge"
                if fv.review_status == "challenged"
                else "Reviewer sign-off"
            )
            remarks.append(
                f"{label} by {_line(fv.review.decided_by)} "
                f"({fv.review.identity_source} identity): {fv.review.rationale}"
            )
        if fv is not None and fv.scope_limitation is not None:
            remarks.append(
                "Reported as a scope limitation by "
                f"{_line(fv.scope_limitation.decided_by)} "
                f"({fv.scope_limitation.identity_source} identity): "
                f"{fv.scope_limitation.rationale}"
            )
        if _line(f.toe_basis):
            remarks.append(f"Basis for the ToE conclusion: {f.toe_basis}")
        if result_of_record == FindingResult.NOT_TESTED:
            remarks.append(
                "Not tested: no evidence covered this control. OSCAL finding "
                "targets are only 'satisfied' or 'not-satisfied', so this "
                "observation has no finding; the result prop records it."
            )
        if f.control_id in narratives:
            remarks.append(
                "Reporting-crew narrative (model-drafted): " + narratives[f.control_id]
            )
        obs: dict[str, Any] = {
            "uuid": o_uuid,
            "title": markup(f"{_line(f.control_id)} — ToD {tod}; ToE {toe}"),
            "description": markup(f.test_conclusion),
            "props": props,
            "methods": _methods(tod, toe),
            "types": ["control-objective"],
            "origins": [
                {
                    "actors": [
                        {"type": "assessment-platform", "actor-uuid": platform_uuid}
                    ]
                }
            ],
            "subjects": [{"subject-uuid": component_uuid, "type": "component"}],
            "collected": collected,
        }
        if vid:
            verified = (
                verify_quote(vid, f.exact_quote_from_evidence)
                if verify_quote and f.exact_quote_from_evidence
                else None
            )
            quote = _line(f.exact_quote_from_evidence)
            obs["relevant-evidence"] = [
                {
                    "href": f"#{evidence_uuid[vid]}",
                    "description": markup(
                        f"Evidence vault record {vid}"
                        + (f" ({meta.source})" if meta and meta.source else "")
                        + (f". Quoted in the working paper: “{quote}”" if quote else "")
                    ),
                    "props": _props(
                        ("vault-id", vid),
                        ("quote-verified-in-vault", _yes_no(verified)),
                    ),
                }
            ]
        if remarks:
            obs["remarks"] = markup("\n\n".join(remarks))
        observations.append(obs)

        if result_of_record in (FindingResult.NOT_TESTED, "None", ""):
            continue
        satisfied = result_of_record == FindingResult.NO_EXCEPTION
        status: dict[str, Any] = {
            "state": "satisfied" if satisfied else "not-satisfied",
            "reason": "pass" if satisfied else "fail",
        }
        if satisfied and toe == OperatingConclusion.NOT_TESTED:
            status["remarks"] = markup(
                "Design and implementation only: operating effectiveness over "
                "the period was not tested."
            )
        finding: dict[str, Any] = {
            "uuid": element_uuid(sid, "finding", key),
            "title": markup(f"{_line(f.control_id)}: {result_of_record}"),
            "description": markup(f.test_conclusion),
            "props": _props(
                ("result", result_of_record),
                ("preliminary-deficiency", _yes_no(preliminary)),
            )
            + review_props,
            "target": {
                "type": "objective-id",
                "target-id": token(f.control_id),
                "title": markup(f"RACM control {_line(f.control_id)}"),
                "status": status,
            },
            "related-observations": [{"observation-uuid": o_uuid}],
        }
        if risks_by_control.get(f.control_id):
            finding["related-risks"] = [
                {"risk-uuid": u} for u in dict.fromkeys(risks_by_control[f.control_id])
            ]
        findings.append(finding)

    # ── Reviewed controls ────────────────────────────────────────────────────
    control_ids = [
        c.control_id for r in (racm.risks if racm else []) for c in r.controls
    ]
    control_ids += [f.control_id for f in papers.findings]
    include = [{"control-id": t} for t in dict.fromkeys(token(c) for c in control_ids)]
    if not include:
        raise ValueError("no controls to report: the RACM and working papers are empty")
    reviewed_controls = {
        "description": markup(
            "Controls in the engagement's Risk and Control Matrix (RACM). The "
            "control IDs are the engagement's own identifiers, not IDs from an "
            "OSCAL catalog; no catalog or profile is imported."
        ),
        "control-selections": [{"include-controls": include}],
    }

    # ── Approval trail → assessment log, roles and parties ──────────────────
    preparer = _line(prepared_by) or next(
        (_line(e.get("human")) for e in trail if e.get("action") == "audit_created"),
        "",
    )
    if preparer:
        parties.uuid_for(preparer)
    log_entries: list[dict] = []
    gate_approvers: list[str] = []
    report_approvers: list[str] = []
    for i, e in enumerate(trail):
        human = _line(e.get("human"))
        start = trail_times[i] or last_modified
        action = _line(e.get("action"))
        entry: dict[str, Any] = {
            "uuid": element_uuid(sid, "log", str(i)),
            "title": markup(_line(e.get("gate")) or action or "Trail entry"),
            "start": start,
            "props": _props(
                ("trail-action", action),
                ("artifact", e.get("artifact")),
                ("artifact-digest", e.get("artifact_digest")),
                ("trail-hash-alg", e.get("hash_alg")),
                ("trail-entry-hash", e.get("entry_hash")),
                ("trail-prev-hash", e.get("prev_hash")),
                ("decision-id", e.get("decision_id")),
                ("decision-digest", e.get("decision_digest")),
                ("decision-type", e.get("decision_type")),
                ("decision-subject", e.get("subject")),
                ("supersedes-decision", e.get("supersedes")),
                ("decisions-digest", e.get("decisions_digest")),
            ),
        }
        details = [
            f"{label}: {e[k]}"
            for k, label in (
                ("notes", "Review notes"),
                ("reason", "Reason"),
                ("qa_rejection_reason", "QA rejection overridden"),
                ("previous_status", "Previous status"),
                ("unverified_controls", "Unverified controls accepted"),
            )
            if _line(e.get(k))
        ]
        entry["description"] = markup(
            "\n\n".join([f"{action or 'action'} by {human or 'unknown'}", *details])
        )
        if human:
            entry["logged-by"] = [{"party-uuid": parties.uuid_for(human)}]
            if action == "gate_approval":
                gate_approvers.append(human)
                if e.get("artifact") == "final_report":
                    report_approvers.append(human)
        log_entries.append(entry)

    roles: list[dict] = []
    responsible: list[dict] = []

    def _role(role_id: str, title: str, names: list[str]) -> None:
        names = list(dict.fromkeys(n for n in names if n))
        if not names:
            return
        roles.append({"id": role_id, "title": title})
        responsible.append(
            {"role-id": role_id, "party-uuids": [parties.uuid_for(n) for n in names]}
        )

    _role("prepared-by", "Preparer (created the audit)", [preparer])
    _role(
        "gate-approver",
        "Gate approver (Planning, Fieldwork or Reporting)",
        gate_approvers,
    )
    _role("content-approver", "Report approver (Gate 3)", report_approvers)
    conclusion = view.engagement_conclusion if view else None
    if conclusion is not None:
        reviewers.append(_line(conclusion.decided_by))
    _role(
        "reviewer",
        "Recorded reviewer decisions (or transcribed a management response)",
        reviewers,
    )

    # ── Result ───────────────────────────────────────────────────────────────
    result_props = _props(("deficiency-scale", scale))
    if conclusion is not None:
        result_props += _props(
            ("engagement-conclusion", conclusion.values.get("conclusion"))
        ) + _decided_props(conclusion, "engagement-conclusion-")
    result: dict[str, Any] = {
        "uuid": element_uuid(sid, "result"),
        "title": markup(f"Audit results — {_line(theme) or _line(session_name)}"),
        "description": markup(report.executive_summary),
        "start": (evidence_times[0] if evidence_times else None)
        or next(iter(known_times), None)
        or last_modified,
    }
    if evidence_times:
        result["end"] = evidence_times[-1]
    if result_props:
        result["props"] = result_props
    result["local-definitions"] = local_definitions
    result["reviewed-controls"] = reviewed_controls
    if conclusion is not None:
        result["attestations"] = [
            {
                "responsible-parties": [
                    {
                        "role-id": "reviewer",
                        "party-uuids": [parties.uuid_for(conclusion.decided_by)],
                    }
                ],
                "parts": [
                    {
                        "uuid": element_uuid(sid, "engagement-conclusion"),
                        "name": "engagement-conclusion",
                        "ns": PROJECT_NS,
                        "title": markup(
                            "Engagement conclusion: "
                            + _line(conclusion.values.get("conclusion"))
                        ),
                        "props": _decided_props(conclusion),
                        "prose": markup(
                            f"{conclusion.values.get('conclusion')} — the "
                            f"reviewer's conclusion on the engagement, recorded "
                            f"by {_line(conclusion.decided_by)} "
                            f"({conclusion.identity_source} identity) on "
                            f"{_line(conclusion.decided_at)[:10]}.\n\n"
                            f"{conclusion.rationale}"
                        ),
                    }
                ],
            }
        ]
    if log_entries:
        result["assessment-log"] = {"entries": log_entries}
    result["observations"] = observations
    if risks:
        result["risks"] = risks
    if findings:
        result["findings"] = findings
    result["remarks"] = markup(
        "Observations: one per working-paper finding. Findings: one per "
        "tested control; a control concluded 'Not tested' has an observation "
        "but no finding. Test-of-design / operating-effectiveness conclusions "
        f"and deficiency classifications are props in the namespace {PROJECT_NS}. "
        "Methods: EXAMINE marks a design and implementation conclusion, TEST an "
        "operating-effectiveness conclusion over the period."
        + (
            " Reviewer decisions: each risk's classification prop is the "
            "conclusion of record (classification-source says whether it is the "
            "reviewer's decision or the AI draft; ai-draft-classification keeps "
            "the draft); observations and findings carry reviewer-sign-off."
            if view is not None
            else ""
        )
    )

    # ── Metadata ─────────────────────────────────────────────────────────────
    title = _line(sar.metadata.title) if sar else ""
    metadata: dict[str, Any] = {
        "title": markup(title or f"Audit results — {_line(session_name)}"),
        "last-modified": last_modified,
        "version": "sha256-"
        + _digest(
            [report, papers, racm]
            + (
                [
                    d.model_dump(mode="json") if hasattr(d, "model_dump") else d
                    for d in decisions
                ]
                if decisions
                else []
            )
        )[:16],
        "oscal-version": OSCAL_VERSION,
        "props": _props(
            ("session-id", sid),
            ("session-status", session_status),
            ("report-state", report_state),
        ),
    }
    if roles:
        metadata["roles"] = roles
    if parties.by_name:
        metadata["parties"] = parties.as_oscal()
    if responsible:
        metadata["responsible-parties"] = responsible
    metadata["remarks"] = markup(
        f"Report state: {report_state}. Generated by GRC Audit Swarm from the "
        "gate-reviewed RACM, working papers and report of this session. The "
        "content was drafted by LLM agents and reviewed by the people recorded "
        "in the assessment log. last-modified is the time of the latest "
        "approval-trail entry, not the export time; version is a digest of the "
        "source artifacts."
    )

    return {
        "assessment-results": {
            "uuid": element_uuid(sid, "assessment-results"),
            "metadata": metadata,
            "import-ap": {
                "href": f"#{plan_uuid}",
                "remarks": markup(
                    "No OSCAL assessment plan document exists. This reference "
                    "resolves to a back-matter resource describing the RACM, "
                    "which served as the assessment plan."
                ),
            },
            "results": [result],
            "back-matter": {"resources": resources},
        }
    }
