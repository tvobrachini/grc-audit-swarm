"""
Deterministic Audit Methodology, Framework, Anti-Distortion & Anti-Slop Linters.

Implements the Tier-0 (0-token, <1ms) deterministic Poka-Yoke verification engine
for the Audit Factory AI architecture, grounded in:

* Authoritative Audit Standards:
  - IIA Global Internal Audit Standards (GIAS 2024/2025) Domains 13, 14 & 15
  - PCAOB AS 2201 (ICFR), AS 1105 (Audit Evidence), AS 1215 (Audit Documentation)
  - AICPA AT-C Section 205 (SOC 2 Type I/II) & Audit Guide Sampling Tables
  - ISACA ITAF 4th Edition
* Authoritative AI Research:
  - Gao et al. (EMNLP 2023 — ALCE), Min et al. (EMNLP 2023 — FActScore),
    Tang et al. (EMNLP 2024 — MiniCheck): 5 Audit Distortion Traps
  - Princeton SWE-agent ACI & Aider synchronous write-linter feedback loops.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional, Sequence

from swarm.evidence import EvidenceAssuranceProtocol
from swarm.oscal_ar import vault_metadata
from swarm.schema import (
    SCALE_CLASSIFICATIONS,
    AuditFindingSchema,
    Control,
    ControlFrequency,
    ControlNature,
    DeficiencyClassification,
    DeficiencyEvaluationSchema,
    DeficiencyScale,
    DesignConclusion,
    FinalReportSchema,
    FindingResult,
    OperatingConclusion,
    RiskControlMatrixSchema,
    RiskRating,
    SamplingMethod,
    WorkingPaperSchema,
)


class EvidenceHierarchyTier(StrEnum):
    """PCAOB AS 1105 / IIA Standard 14.3 Evidence Reliability Hierarchy."""

    TIER_1_SYSTEM_GENERATED = "Tier 1: System-generated / Cryptographically sealed"
    TIER_2_EXTERNAL_DOCUMENTARY = "Tier 2: External / Independent documentary"
    TIER_3_INTERNAL_DOCUMENTARY = "Tier 3: Internal documentary / Policy / Ticket"
    TIER_4_INQUIRY_OBSERVATION = "Tier 4: Management inquiry / Uncorroborated statement"


# AICPA / Big Four standard minimum sample sizes by control frequency (PCAOB AS 2201 / AICPA Audit Guide)
AICPA_MIN_SAMPLE_SIZE_BY_FREQUENCY: dict[ControlFrequency, int] = {
    ControlFrequency.ANNUAL: 1,
    ControlFrequency.QUARTERLY: 2,
    ControlFrequency.MONTHLY: 2,
    ControlFrequency.WEEKLY: 5,
    ControlFrequency.DAILY: 20,
    ControlFrequency.MULTIPLE_TIMES_PER_DAY: 25,
    ControlFrequency.CONTINUOUS: 1,
    ControlFrequency.EVENT_DRIVEN: 1,
}

# Auditing standards govern the auditor, not the audited entity's controls (D13)
_AUDITING_STANDARD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bPCAOB\b", re.IGNORECASE),
    re.compile(r"\bAS\s*(?:1105|1215|2101|2110|2201|2301|2315)\b", re.IGNORECASE),
    re.compile(r"\bIIA\s+(?:Standard|IPPF|GIAS)\b", re.IGNORECASE),
    re.compile(r"\bISA\s+\d{3}\b", re.IGNORECASE),
    re.compile(r"\bAT-C\s+\d{3}\b", re.IGNORECASE),
    re.compile(r"\bSSAE\s*18\b", re.IGNORECASE),
    re.compile(r"\bISAE\s*3402\b", re.IGNORECASE),
)

# Recognised control frameworks & canonical clause patterns (D13)
_KNOWN_CONTROL_FRAMEWORK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bNIST\s+(?:SP\s+)?800-53\b", re.IGNORECASE),
    re.compile(r"\bNIST\s+CSF\b", re.IGNORECASE),
    re.compile(r"\bNIST\s+(?:SP\s+)?800-66\b", re.IGNORECASE),
    re.compile(r"\bCIS\b", re.IGNORECASE),
    re.compile(r"\bISO(?:/IEC)?\s*2700[12]\b", re.IGNORECASE),
    re.compile(r"\bSOC\s*2\b|\bTSC\b|\bCC[1-9]\.\d+\b", re.IGNORECASE),
    re.compile(r"\bCOSO\b", re.IGNORECASE),
    re.compile(r"\bCOBIT\b", re.IGNORECASE),
    re.compile(r"\bPCI(?:[-\s]?DSS)?\b", re.IGNORECASE),
    re.compile(r"\bHIPAA\b|\b45\s*CFR\b", re.IGNORECASE),
    re.compile(r"\bGDPR\b|\bArt(?:icle|\.)?\s*\d+\b", re.IGNORECASE),
    re.compile(r"\bSCF\b", re.IGNORECASE),
    re.compile(r"\bOWASP\b", re.IGNORECASE),
    re.compile(r"\bAWS\s+Well-Architected\b", re.IGNORECASE),
)

# Forbidden AI-slop phrases (D14)
FORBIDDEN_SLOP_PHRASES: tuple[str, ...] = (
    "delve",
    "delving",
    "tapestry",
    "testament to",
    "robust framework",
    "holistic approach",
    "synergy",
    "paradigm shift",
    "beacon of",
    "underscores the importance",
    "in today's rapidly evolving",
    "ever-changing landscape",
    "seamlessly integrated",
)

# Forbidden over-claiming / legal certification phrases in audit reports (D14)
FORBIDDEN_CERTIFICATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bfully\s+compliant\b", re.IGNORECASE),
    re.compile(r"\b100%\s+compliant\b", re.IGNORECASE),
    re.compile(r"\bcertified\s+compliant\b", re.IGNORECASE),
    re.compile(r"\bguarantees?\s+compliance\b", re.IGNORECASE),
    re.compile(
        r"\bcertif(?:y|ies)\s+that\s+the\s+(?:entity|organisation|organization|system)\s+is\s+compliant\b",
        re.IGNORECASE,
    ),
)

# Change-management / ITGC keywords for cross-control reliance verification (D8)
_ITGC_RELIANCE_RE = re.compile(
    r"change[-\s]?management|itgc|general\s+it\s+control",
    re.IGNORECASE,
)
_CONFIG_HISTORY_RE = re.compile(
    r"aws\s+config|config\s+history|cloudtrail\s+history|historical\s+log",
    re.IGNORECASE,
)
_CHANGE_MGMT_CONTROL_RE = re.compile(
    r"change[-\s]?management|program\s+change|cc8\.1|cm-\d+|bai06|chg",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LintViolation:
    """Single deterministic audit methodology, distortion, or slop violation."""

    rule_id: str
    subject_id: str
    message: str
    severity: str = "error"  # "error" | "warning"


@dataclass
class LintReport:
    """Aggregated result of deterministic linting across an artifact."""

    violations: list[LintViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(v.severity == "error" for v in self.violations)

    @property
    def errors(self) -> list[LintViolation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def warnings(self) -> list[LintViolation]:
        return [v for v in self.violations if v.severity == "warning"]

    def affected_subjects(self) -> list[str]:
        return list(dict.fromkeys(v.subject_id for v in self.errors if v.subject_id))

    def to_feedback(self) -> str:
        """Compact, surgical feedback string for agent or single-control retry."""
        if not self.errors:
            return ""
        lines = [f"[{v.rule_id}] ({v.subject_id}): {v.message}" for v in self.errors]
        return "Deterministic Audit Linter Violations:\n" + "\n".join(lines)


# ── Phase 1: RACM Linter (Rules D6, D7, D10, D13, D14) ───────────────────────


def lint_racm(
    racm: RiskControlMatrixSchema,
    *,
    strict_attributes: bool = True,
) -> LintReport:
    """Deterministically validate a Phase 1 RACM against IIA/PCAOB/AICPA rules."""
    report = LintReport()
    seen_control_ids: set[str] = set()

    if not racm.risks:
        report.violations.append(
            LintViolation("D6", "RACM", "RACM must contain at least one risk.")
        )
        return report

    for risk in racm.risks:
        # D13: Anti-auditing-standard linter on regulatory_mapping
        for mapping in risk.regulatory_mapping or []:
            for pat in _AUDITING_STANDARD_PATTERNS:
                if pat.search(str(mapping)):
                    report.violations.append(
                        LintViolation(
                            "D13",
                            risk.risk_id,
                            f"regulatory_mapping '{mapping}' cites an auditing standard "
                            "(which governs the auditor, not the entity's controls). "
                            "Map to a control framework (e.g., NIST SP 800-53, CIS, "
                            "ISO/IEC 27001:2022, SOC 2 TSC, COSO 2013, PCI-DSS v4.0.1).",
                        )
                    )
                    break

        if not risk.controls:
            report.violations.append(
                LintViolation(
                    "D6",
                    risk.risk_id,
                    f"Risk {risk.risk_id} has no mitigating controls.",
                )
            )

        for ctrl in risk.controls:
            cid = ctrl.control_id
            if cid in seen_control_ids:
                report.violations.append(
                    LintViolation(
                        "D1",
                        cid,
                        f"Duplicate control_id '{cid}' in RACM; every control ID must be unique.",
                    )
                )
            seen_control_ids.add(cid)

            tp = ctrl.testing_procedures
            if not tp.test_of_design:
                report.violations.append(
                    LintViolation("D6", cid, "Control is missing test_of_design steps.")
                )
            if not tp.test_of_effectiveness:
                report.violations.append(
                    LintViolation(
                        "D6", cid, "Control is missing test_of_effectiveness steps."
                    )
                )

            if strict_attributes:
                for attr_name in (
                    "control_owner",
                    "frequency",
                    "nature",
                    "control_type",
                    "key_control",
                ):
                    if getattr(ctrl, attr_name, None) is None:
                        report.violations.append(
                            LintViolation(
                                "D6",
                                cid,
                                f"Control is missing mandatory attribute '{attr_name}'.",
                            )
                        )
                if not tp.substantive_testing:
                    report.violations.append(
                        LintViolation(
                            "D6",
                            cid,
                            "Control is missing substantive_testing / follow-up steps.",
                        )
                    )
                if not tp.period_of_reliance:
                    report.violations.append(
                        LintViolation(
                            "D6",
                            cid,
                            "Control testing_procedures is missing 'period_of_reliance'.",
                        )
                    )
                if not tp.sampling_method:
                    report.violations.append(
                        LintViolation(
                            "D6",
                            cid,
                            "Control testing_procedures is missing 'sampling_method'.",
                        )
                    )

            # D10: IPE Completeness & Accuracy chain
            if ctrl.nature == ControlNature.IT_DEPENDENT_MANUAL:
                if not ctrl.ipe:
                    report.violations.append(
                        LintViolation(
                            "D10",
                            cid,
                            "IT-dependent manual control must specify the IPE (reports, "
                            "queries, or extracts) it relies on in 'ipe'.",
                        )
                    )
            if ctrl.ipe or ctrl.nature in (
                ControlNature.MANUAL,
                ControlNature.IT_DEPENDENT_MANUAL,
            ):
                if strict_attributes and (
                    tp.population is None
                    or not (tp.population.completeness_procedure or "").strip()
                    or tp.population.completeness_procedure.strip().lower()
                    == "not specified"
                ):
                    report.violations.append(
                        LintViolation(
                            "D10",
                            cid,
                            "Manual / IT-dependent manual control (or control with IPE) "
                            "must specify a concrete population.completeness_procedure.",
                        )
                    )

            # D7: AICPA / Big Four Sample Size vs. Frequency & Nature
            if (
                tp.sampling_method == SamplingMethod.TEST_OF_ONE
                and ctrl.nature
                in (ControlNature.MANUAL, ControlNature.IT_DEPENDENT_MANUAL)
                and ctrl.frequency not in (ControlFrequency.ANNUAL, None)
            ):
                report.violations.append(
                    LintViolation(
                        "D7",
                        cid,
                        f"SamplingMethod 'Test of one' is invalid for a {ctrl.nature} "
                        f"control with frequency '{ctrl.frequency}' (only valid for "
                        "Automated controls or Annual manual controls).",
                    )
                )

            if (
                ctrl.nature in (ControlNature.MANUAL, ControlNature.IT_DEPENDENT_MANUAL)
                and ctrl.frequency in AICPA_MIN_SAMPLE_SIZE_BY_FREQUENCY
                and tp.sampling_method != SamplingMethod.FULL_POPULATION
                and tp.sample_size is not None
            ):
                min_n = AICPA_MIN_SAMPLE_SIZE_BY_FREQUENCY[ctrl.frequency]
                if tp.sample_size < min_n:
                    report.violations.append(
                        LintViolation(
                            "D7",
                            cid,
                            f"Sample size {tp.sample_size} for {ctrl.frequency} manual/ITDM "
                            f"control is below the AICPA minimum sample size ({min_n}).",
                        )
                    )

            # D14: Anti-slop on control description
            _check_slop_text(ctrl.description, cid, report)

    return report


# ── Phase 2: Working Papers & 5 Distortion Traps Linter ─────────────────────


def _extract_year_bounds(
    period_text: Optional[str],
) -> Optional[tuple[datetime.date, datetime.date]]:
    """Extract ISO date range (YYYY-MM-DD .. YYYY-MM-DD) or FY year from period_of_reliance."""
    if not period_text:
        return None
    iso_dates = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", period_text)
    if len(iso_dates) >= 2:
        try:
            start = datetime.date.fromisoformat(iso_dates[0])
            end = datetime.date.fromisoformat(iso_dates[1])
            return (min(start, end), max(start, end))
        except ValueError:
            pass
    fy_match = re.search(r"\bFY\s*(\d{4})\b", period_text, re.IGNORECASE)
    if fy_match:
        year = int(fy_match.group(1))
        return (datetime.date(year, 1, 1), datetime.date(year, 12, 31))
    return None


def classify_evidence_tier(source_operation: str) -> EvidenceHierarchyTier:
    """Classify an evidence collection source into PCAOB AS 1105 / IIA 14.3 tiers (D11)."""
    src = (source_operation or "").strip().lower()
    if any(
        prefix in src
        for prefix in (
            "aws.",
            "iam:",
            "s3:",
            "s3control:",
            "sts:",
            "cloudtrail:",
            "config:",
        )
    ):
        return EvidenceHierarchyTier.TIER_1_SYSTEM_GENERATED
    if "external" in src or "soc1" in src or "soc2" in src:
        return EvidenceHierarchyTier.TIER_2_EXTERNAL_DOCUMENTARY
    if "inquiry" in src or "interview" in src or "walkthrough_notes" in src:
        return EvidenceHierarchyTier.TIER_4_INQUIRY_OBSERVATION
    return EvidenceHierarchyTier.TIER_3_INTERNAL_DOCUMENTARY


def lint_distortion_traps(
    finding: AuditFindingSchema,
    control: Optional[Control] = None,
    all_findings: Optional[Sequence[AuditFindingSchema]] = None,
    all_controls: Optional[dict[str, Control]] = None,
) -> list[LintViolation]:
    """Check the 5 Audit Distortion Traps (Gao et al. ALCE / Min et al. FActScore / Tang et al. MiniCheck).

    Trap 1: Polarity / Exception Inversion (quote shows failure/disabled/public, finding claims Effective).
    Trap 2: Scope Over-Generalization (quote covers 1 user/bucket or has errors, finding claims all passed).
    Trap 3: Design-vs-Operating Confusion (point-in-time snapshot concludes ToE Effective without ITGC/history basis).
    Trap 4: Quantitative Threshold Mismatch (quote numeric metric violates expected threshold in RACM step).
    Trap 5: Citation Dumping / Unrelated Span (quote is boilerplate metadata unrelated to the control).
    """
    violations: list[LintViolation] = []
    cid = finding.control_id
    quote = (finding.exact_quote_from_evidence or "").strip()
    if finding.result == FindingResult.NOT_TESTED or not quote:
        return violations

    claims_effective = (
        finding.tod_conclusion == DesignConclusion.EFFECTIVE
        or finding.toe_conclusion == OperatingConclusion.EFFECTIVE
    ) and finding.result == FindingResult.NO_EXCEPTION

    # Trap 1: Polarity / Exception Inversion
    if claims_effective:
        inversion_patterns = (
            (r'"MFA_Enabled"\s*:\s*"No"', "quote shows MFA_Enabled is 'No'"),
            (r'"Verdict"\s*:\s*"PUBLIC"', "quote shows bucket Verdict is 'PUBLIC'"),
            (
                r"No IAM account password policy is set",
                "quote states no password policy is set",
            ),
            (r'"BlockPublicAcls"\s*:\s*false', "quote shows BlockPublicAcls is false"),
            (
                r'"AccountMFAEnabled"\s*:\s*0\b',
                "quote shows root AccountMFAEnabled is 0 (disabled)",
            ),
            (
                r'"AccountAccessKeysPresent"\s*:\s*[1-9]\d*\b',
                "quote shows root access keys are present",
            ),
        )
        for pat, desc in inversion_patterns:
            if re.search(pat, quote, re.IGNORECASE):
                violations.append(
                    LintViolation(
                        "TRAP-1",
                        cid,
                        f"Polarity / Exception Inversion: {desc}, but finding concludes "
                        f"'{finding.result}' (ToD={finding.tod_conclusion}, ToE={finding.toe_conclusion}).",
                    )
                )

    # Trap 2: Scope Over-Generalization (partial/denied read claimed as full pass)
    if claims_effective:
        if re.search(r'"Verdict"\s*:\s*"UNKNOWN"', quote, re.IGNORECASE) or re.search(
            r'"AccessDenied"', quote, re.IGNORECASE
        ):
            violations.append(
                LintViolation(
                    "TRAP-2",
                    cid,
                    "Scope Over-Generalization: evidence quote contains 'UNKNOWN' or "
                    "'AccessDenied' (incomplete visibility), which cannot support an "
                    "unqualified 'No exception' conclusion.",
                )
            )

    # Trap 3: Design-vs-Operating Confusion (PCAOB AS 2201 / IIA 14.3)
    if finding.toe_conclusion == OperatingConclusion.EFFECTIVE:
        basis = (finding.toe_basis or "").strip()
        is_snapshot = (finding.items_tested in (None, 1)) and (
            control is None
            or control.nature == ControlNature.AUTOMATED
            or (
                control.testing_procedures
                and control.testing_procedures.sampling_method
                == SamplingMethod.TEST_OF_ONE
            )
        )
        if is_snapshot and not basis:
            violations.append(
                LintViolation(
                    "TRAP-3",
                    cid,
                    "Design-vs-Operating Confusion: finding concludes ToE='Effective' "
                    "from a single point-in-time read without stating a 'toe_basis' "
                    "(e.g. reliance on tested change-management ITGCs or configuration "
                    "history covering the period).",
                )
            )

    # Trap 4: Quantitative Threshold Mismatch (e.g., MinimumPasswordLength < RACM threshold)
    if claims_effective and control is not None:
        len_match = re.search(
            r'MinimumPasswordLength["\s:=]+(\d+)', quote, re.IGNORECASE
        )
        if len_match:
            actual_len = int(len_match.group(1))
            steps_text = " ".join(
                f"{s.step_description} {s.expected_result}"
                for s in (
                    control.testing_procedures.test_of_design
                    + control.testing_procedures.test_of_effectiveness
                )
            )
            req_match = re.search(
                r"(?:minimum\s+length|at\s+least|>=?)\s*(?:of\s*(?:at\s*least\s*)?)?(\d+)",
                steps_text,
                re.IGNORECASE,
            )
            req_len = int(req_match.group(1)) if req_match else 14
            if actual_len < req_len:
                violations.append(
                    LintViolation(
                        "TRAP-4",
                        cid,
                        f"Quantitative Threshold Mismatch: evidence shows "
                        f"MinimumPasswordLength={actual_len}, which is below the "
                        f"required threshold ({req_len}), yet finding concludes 'No exception'.",
                    )
                )

    # Trap 5: Citation Dumping / Unrelated Span
    if re.fullmatch(
        r'[\s\{\}\[\]",:]*("AccountId"|"ResponseMetadata"|"RequestId")[\s\S]{0,40}',
        quote,
    ):
        violations.append(
            LintViolation(
                "TRAP-5",
                cid,
                "Citation Dumping: quote cites only generic envelope metadata "
                "rather than control-relevant configuration fields.",
            )
        )

    return violations


def lint_working_papers(
    papers: WorkingPaperSchema,
    racm: Optional[RiskControlMatrixSchema] = None,
    *,
    session_id: Optional[str] = None,
    verify_vault_quotes: bool = True,
    tolerable_deviation_rate: float = 0.0,
) -> LintReport:
    """Deterministically validate Phase 2 Working Papers (Rules D1, D2, D3, D7, D8, D11, D12 + 5 Traps)."""
    report = LintReport()

    controls_by_id: dict[str, Control] = {}
    if racm is not None:
        for risk in racm.risks:
            for ctrl in risk.controls:
                controls_by_id[ctrl.control_id] = ctrl

        # D1: 1-to-1 RACM <-> WorkingPaper control coverage
        racm_ids = list(controls_by_id.keys())
        paper_ids = [f.control_id for f in papers.findings]
        missing = [cid for cid in racm_ids if cid not in paper_ids]
        extra = [cid for cid in paper_ids if cid not in controls_by_id]
        if missing:
            report.violations.append(
                LintViolation(
                    "D1",
                    ",".join(missing),
                    f"Working papers are missing findings for RACM control(s): {', '.join(missing)}.",
                )
            )
        if extra:
            report.violations.append(
                LintViolation(
                    "D1",
                    ",".join(extra),
                    f"Working papers contain findings for control ID(s) not in the RACM: {', '.join(extra)}.",
                )
            )

    # Identify Change Management ITGC controls in the engagement for D8
    change_mgmt_Ids = [
        cid
        for cid, ctrl in controls_by_id.items()
        if _CHANGE_MGMT_CONTROL_RE.search(ctrl.description or "")
        or _CHANGE_MGMT_CONTROL_RE.search(cid)
        or any(_CHANGE_MGMT_CONTROL_RE.search(a) for a in (ctrl.assertions or []))
    ]
    findings_by_id = {f.control_id: f for f in papers.findings}

    for f in papers.findings:
        cid = f.control_id
        ctrl = controls_by_id.get(cid)

        # D2: Arithmetic consistency of items_tested and exceptions_noted
        if (
            f.items_tested is not None
            and f.exceptions_noted is not None
            and f.exceptions_noted > f.items_tested
        ):
            report.violations.append(
                LintViolation(
                    "D2",
                    cid,
                    f"exceptions_noted ({f.exceptions_noted}) cannot exceed items_tested ({f.items_tested}).",
                )
            )
        if f.result == FindingResult.NOT_TESTED:
            if (f.items_tested or 0) > 0 or (f.exceptions_noted or 0) > 0:
                report.violations.append(
                    LintViolation(
                        "D2",
                        cid,
                        "A finding with result='Not tested' cannot report positive items_tested or exceptions_noted.",
                    )
                )

        # D3: preliminary_deficiency == (result == FindingResult.EXCEPTION)
        expected_prelim = f.result == FindingResult.EXCEPTION
        if f.preliminary_deficiency != expected_prelim:
            report.violations.append(
                LintViolation(
                    "D3",
                    cid,
                    f"preliminary_deficiency is {f.preliminary_deficiency} but result is '{f.result}'.",
                )
            )

        # Quote verification against the evidence vault
        if verify_vault_quotes:
            quote = (f.exact_quote_from_evidence or "").strip()
            vid = (f.vault_id_reference or "").strip()
            if f.result != FindingResult.NOT_TESTED:
                if not quote or not vid:
                    report.violations.append(
                        LintViolation(
                            "D11",
                            cid,
                            "Tested control finding must cite both vault_id_reference and exact_quote_from_evidence.",
                        )
                    )
                elif not EvidenceAssuranceProtocol.verify_exact_quote(
                    vid, quote, session_id=session_id
                ):
                    report.violations.append(
                        LintViolation(
                            "D11",
                            cid,
                            f"Exact quote for {cid} failed verification against vault record '{vid}'.",
                        )
                    )

        # D8: Cross-control ITGC reliance chain
        if (
            f.toe_conclusion == OperatingConclusion.EFFECTIVE
            and f.toe_basis
            and _ITGC_RELIANCE_RE.search(f.toe_basis)
            and not _CONFIG_HISTORY_RE.search(f.toe_basis)
            and change_mgmt_Ids
        ):
            failed_itgcs = [
                itgc_id
                for itgc_id in change_mgmt_Ids
                if itgc_id != cid
                and (
                    itgc_id not in findings_by_id
                    or findings_by_id[itgc_id].toe_conclusion
                    != OperatingConclusion.EFFECTIVE
                )
            ]
            if failed_itgcs:
                report.violations.append(
                    LintViolation(
                        "D8",
                        cid,
                        f"Control {cid} concludes ToE='Effective' by relying on Change Management "
                        f"ITGCs ({', '.join(failed_itgcs)}), but those ITGC controls are not "
                        "operating effectively in this engagement.",
                    )
                )

        # D11: Evidence collection timestamp vs. period_of_reliance window
        if (
            ctrl is not None
            and ctrl.testing_procedures.period_of_reliance
            and f.vault_id_reference
        ):
            meta = vault_metadata(f.vault_id_reference)
            bounds = _extract_year_bounds(ctrl.testing_procedures.period_of_reliance)
            if meta and meta.collected and bounds:
                try:
                    collected_date = datetime.datetime.fromisoformat(
                        meta.collected
                    ).date()
                    start_date, end_date = bounds
                    # Allow a 90-day roll-forward / cutoff buffer after period end
                    if collected_date < start_date or collected_date > (
                        end_date + datetime.timedelta(days=90)
                    ):
                        report.violations.append(
                            LintViolation(
                                "D11",
                                cid,
                                f"Evidence collection date ({collected_date}) falls outside "
                                f"the control's period_of_reliance ({start_date} to {end_date}).",
                                severity="warning",
                            )
                        )
                except ValueError:
                    pass

        # D12: Deviation rate vs. tolerable error rate
        if (
            f.items_tested is not None
            and f.items_tested > 0
            and f.exceptions_noted is not None
            and f.exceptions_noted > 0
        ):
            dev_rate = f.exceptions_noted / f.items_tested
            if (
                dev_rate > tolerable_deviation_rate
                and f.toe_conclusion == OperatingConclusion.EFFECTIVE
            ):
                report.violations.append(
                    LintViolation(
                        "D12",
                        cid,
                        f"Deviation rate {f.exceptions_noted}/{f.items_tested} ({dev_rate:.1%}) "
                        f"exceeds tolerable rate ({tolerable_deviation_rate:.1%}), so "
                        "toe_conclusion cannot be 'Effective'.",
                    )
                )

        # 5 Audit Distortion Traps
        report.violations.extend(
            lint_distortion_traps(
                f,
                control=ctrl,
                all_findings=papers.findings,
                all_controls=controls_by_id,
            )
        )

        # D14: Anti-slop on test_conclusion
        _check_slop_text(f.test_conclusion, cid, report)

    return report


# ── Phase 3: Deficiency Evaluations & Final Report Linter (D4, D9, D14) ─────


def lint_deficiency_evaluations(
    evaluations: Sequence[DeficiencyEvaluationSchema],
    papers: Optional[WorkingPaperSchema] = None,
    racm: Optional[RiskControlMatrixSchema] = None,
    *,
    expected_scale: Optional[DeficiencyScale] = None,
) -> LintReport:
    """Deterministically validate Phase 3 Deficiency Evaluations (Rules D4, D9, D14)."""
    report = LintReport()

    findings_by_id: dict[str, AuditFindingSchema] = {}
    if papers is not None:
        for f in papers.findings:
            findings_by_id[f.control_id] = f

    risk_ids: set[str] = set()
    if racm is not None:
        for r in racm.risks:
            risk_ids.add(r.risk_id)

    # D4: Every preliminary_deficiency=True finding must appear in at least one evaluation
    covered_findings: set[str] = set()
    for ev in evaluations:
        did = ev.deficiency_id
        for rf in ev.related_findings:
            covered_findings.add(rf)
            if papers is not None and rf not in findings_by_id:
                report.violations.append(
                    LintViolation(
                        "D4",
                        did,
                        f"Deficiency {did} references unknown finding control_id '{rf}'.",
                    )
                )
        for rr in ev.related_risks:
            if racm is not None and rr not in risk_ids:
                report.violations.append(
                    LintViolation(
                        "D4",
                        did,
                        f"Deficiency {did} references unknown RACM risk_id '{rr}'.",
                    )
                )

        # D14: Scale discipline & PCAOB AS 2201 Material Weakness floor
        if expected_scale is not None:
            allowed = SCALE_CLASSIFICATIONS.get(expected_scale, frozenset())
            if allowed and ev.classification not in allowed:
                report.violations.append(
                    LintViolation(
                        "D14",
                        did,
                        f"Classification '{ev.classification}' is invalid for scale '{expected_scale}'.",
                    )
                )
        if ev.classification == DeficiencyClassification.MATERIAL_WEAKNESS:
            if ev.likelihood == RiskRating.LOW or ev.magnitude != RiskRating.HIGH:
                report.violations.append(
                    LintViolation(
                        "D14",
                        did,
                        "PCAOB AS 2201 floor: Material Weakness requires likelihood "
                        "Medium/High and magnitude High.",
                    )
                )

        # D9: Compensating Control Verification
        comp_text = (ev.compensating_controls or "").strip()
        if comp_text and comp_text.lower() not in (
            "none",
            "none identified",
            "none noted",
            "n/a",
        ):
            cited_ctrls = re.findall(r"\b(CTRL-\d+[A-Za-z0-9_-]*)\b", comp_text)
            for cited_cid in cited_ctrls:
                cited_f = findings_by_id.get(cited_cid)
                if cited_f is None:
                    report.violations.append(
                        LintViolation(
                            "D9",
                            did,
                            f"Compensating control '{cited_cid}' cited in {did} does not "
                            "exist in the working papers.",
                        )
                    )
                elif (
                    cited_f.result != FindingResult.NO_EXCEPTION
                    or cited_f.toe_conclusion != OperatingConclusion.EFFECTIVE
                ):
                    report.violations.append(
                        LintViolation(
                            "D9",
                            did,
                            f"Compensating control '{cited_cid}' cited in {did} cannot "
                            "mitigate deficiency severity because its tested conclusion is "
                            f"result='{cited_f.result}', ToE='{cited_f.toe_conclusion}' "
                            "(PCAOB AS 2201.68 requires compensating controls to be tested "
                            "and operating effectively).",
                        )
                    )

        _check_slop_text(ev.rationale, did, report)

    if papers is not None:
        prelim_ids = [f.control_id for f in papers.findings if f.preliminary_deficiency]
        uncovered = [cid for cid in prelim_ids if cid not in covered_findings]
        if uncovered:
            report.violations.append(
                LintViolation(
                    "D4",
                    ",".join(uncovered),
                    "Preliminary deficiency finding(s) missing from deficiency_evaluations: "
                    + ", ".join(uncovered),
                )
            )

    return report


def lint_report(
    final_report: FinalReportSchema,
    papers: Optional[WorkingPaperSchema] = None,
    racm: Optional[RiskControlMatrixSchema] = None,
    *,
    expected_scale: Optional[DeficiencyScale] = None,
) -> LintReport:
    """Deterministically validate a Phase 3 FinalReportSchema (Rules D4, D9, D14)."""
    report = lint_deficiency_evaluations(
        final_report.deficiency_evaluations or [],
        papers=papers,
        racm=racm,
        expected_scale=expected_scale,
    )

    for section_name, text in (
        ("executive_summary", final_report.executive_summary),
        ("detailed_report", final_report.detailed_report),
    ):
        _check_slop_text(text, section_name, report)
        for pat in FORBIDDEN_CERTIFICATION_PATTERNS:
            m = pat.search(text or "")
            if m:
                report.violations.append(
                    LintViolation(
                        "D14",
                        section_name,
                        f"Forbidden compliance certification claim '{m.group(0)}' in {section_name}. "
                        "State only what was tested and the exceptions noted.",
                    )
                )
        if expected_scale == DeficiencyScale.RISK_RATING:
            if re.search(
                r"\b(?:material\s+weakness|significant\s+deficiency)\b",
                text or "",
                re.IGNORECASE,
            ):
                report.violations.append(
                    LintViolation(
                        "D14",
                        section_name,
                        f"{section_name} uses SOX/ICFR terminology ('Material Weakness' or "
                        "'Significant Deficiency') on a 'Risk rating' scale engagement.",
                    )
                )

    return report


def _check_slop_text(text: Optional[str], subject_id: str, report: LintReport) -> None:
    if not text:
        return
    lower = text.lower()
    for phrase in FORBIDDEN_SLOP_PHRASES:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", lower):
            report.violations.append(
                LintViolation(
                    "D14",
                    subject_id,
                    f"Forbidden AI-slop phrase '{phrase}' detected in {subject_id}. "
                    "Use concise, factual audit prose.",
                )
            )
