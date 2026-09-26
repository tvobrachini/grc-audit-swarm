"""
Pydantic contracts for the three audit artifacts.

The model follows common IT-audit practice, drawing on (not claiming
conformance with) the IIA Global Internal Audit Standards, COSO 2013 and Big
Four ITGC methodology:

* **Planning** — a Risk and Control Matrix (RACM). Each control carries the
  attributes a reviewer expects (owner, frequency, nature, type, key control,
  assertions / IT objectives, IPE) and each test design its population,
  sample size, sampling method and period of reliance.
* **Fieldwork** — one finding per control with separate test-of-design (ToD)
  and test-of-operating-effectiveness (ToE) conclusions, an exception count,
  an overall result and a *preliminary* deficiency flag. Fieldwork never
  classifies severity.
* **Reporting** — an engagement-level deficiency evaluation (aggregation,
  compensating controls, likelihood × magnitude, classification). It is a
  draft for the auditor's judgement at Gate 3, not a conclusion.

Enum fields accept case/spacing variants ("exceptions_noted", "effective")
so small LLM formatting drift still validates; anything else is rejected.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, List, Optional

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema


# ── Enum helpers ─────────────────────────────────────────────────────────────


def _norm(value: str) -> str:
    return re.sub(r"[\s_\-/]+", " ", value).strip().lower()


def _lenient(enum_cls: type[StrEnum]) -> BeforeValidator:
    """Match an enum value ignoring case, underscores, hyphens and spacing."""
    lookup = {_norm(m.value): m for m in enum_cls}

    def coerce(value: Any) -> Any:
        if isinstance(value, str):
            return lookup.get(_norm(value), value)
        return value

    return BeforeValidator(coerce)


def _lenient_int(value: Any) -> Any:
    """Accept "25", "25 items" or 25; anything without a number becomes None."""
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        return int(match.group()) if match else None
    return value


# ── Planning: RACM ───────────────────────────────────────────────────────────


class RiskRating(StrEnum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class ControlFrequency(StrEnum):
    ANNUAL = "Annual"
    QUARTERLY = "Quarterly"
    MONTHLY = "Monthly"
    WEEKLY = "Weekly"
    DAILY = "Daily"
    MULTIPLE_TIMES_PER_DAY = "Multiple times per day"
    EVENT_DRIVEN = "Event-driven"
    CONTINUOUS = "Continuous"


class ControlNature(StrEnum):
    MANUAL = "Manual"
    AUTOMATED = "Automated"
    IT_DEPENDENT_MANUAL = "IT-dependent manual"


class ControlType(StrEnum):
    PREVENTIVE = "Preventive"
    DETECTIVE = "Detective"


class SamplingMethod(StrEnum):
    TEST_OF_ONE = "Test of one"
    RANDOM = "Random"
    SYSTEMATIC = "Systematic"
    HAPHAZARD = "Haphazard"
    JUDGMENTAL = "Judgmental"
    FULL_POPULATION = "Full population"
    NOT_APPLICABLE = "Not applicable"


RiskRatingField = Annotated[RiskRating, _lenient(RiskRating)]


class ControlTestStep(BaseModel):
    step_description: str = Field(
        ..., description="Actionable step for the Field Auditor."
    )
    expected_result: str = Field(
        ..., description="What the evidence must show for no exception."
    )


class Population(BaseModel):
    source: str = Field(
        ...,
        description=(
            "System or report the population is extracted from (e.g. 'IAM "
            "credential report', 'change tickets closed in the period from the "
            "ticketing tool')."
        ),
    )
    completeness_procedure: str = Field(
        default="Not specified",
        description=(
            "How the auditor confirms the population is complete and accurate "
            "(e.g. reconcile the ticket export to the deployment log; inspect "
            "the query parameters)."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _from_string(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"source": data}
        return data


class ControlTesting(BaseModel):
    test_of_design: List[ControlTestStep] = Field(
        ...,
        description=(
            "Steps to test design and implementation: is the control designed "
            "to address the risk, and is it in place (walkthrough / "
            "configuration inspection)."
        ),
    )
    test_of_effectiveness: List[ControlTestStep] = Field(
        ...,
        description=(
            "Steps to test operating effectiveness over the period of reliance. "
            "Manual controls: sample occurrences across the period. Automated "
            "controls: a test of one plus reliance on effective ITGCs (change "
            "management) or configuration history covering the period — a "
            "single point-in-time read is design evidence only."
        ),
    )
    substantive_testing: Optional[List[ControlTestStep]] = Field(
        default=None,
        description=(
            "Substantive / follow-up procedures: what to inspect directly if "
            "exceptions are found or to corroborate the control (e.g. inspect "
            "what an exposed bucket contained)."
        ),
    )
    population: Optional[Population] = Field(
        default=None,
        description="Population for the ToE sample: source and completeness procedure.",
    )
    sample_size: Annotated[Optional[int], BeforeValidator(_lenient_int)] = Field(
        default=None,
        ge=0,
        description=(
            "Items to test for ToE (1 for a test of one of an automated "
            "control). Size by control frequency and risk."
        ),
    )
    sampling_method: Annotated[Optional[SamplingMethod], _lenient(SamplingMethod)] = (
        Field(default=None, description="How the sample is selected.")
    )
    period_of_reliance: Optional[str] = Field(
        default=None,
        description=(
            "Period the conclusion must cover (e.g. '2026-01-01 to "
            "2026-12-31'); 'point in time as of <date>' if only a snapshot is "
            "planned."
        ),
    )


class Control(BaseModel):
    control_id: str = Field(
        ..., description="Unique immutable ID for the control (e.g. CTRL-001a)."
    )
    description: str = Field(..., description="Description of the internal control.")
    control_owner: Optional[str] = Field(
        default=None,
        description="Role accountable for performing the control (a role, not a person's name).",
    )
    frequency: Annotated[Optional[ControlFrequency], _lenient(ControlFrequency)] = (
        Field(default=None, description="How often the control operates.")
    )
    nature: Annotated[Optional[ControlNature], _lenient(ControlNature)] = Field(
        None, description="Manual, Automated or IT-dependent manual."
    )
    control_type: Annotated[Optional[ControlType], _lenient(ControlType)] = Field(
        None, description="Preventive or Detective."
    )
    key_control: Optional[bool] = Field(
        default=None,
        description="True if the control is relied on to address the risk (a key control).",
    )
    assertions: List[str] = Field(
        default_factory=list,
        description=(
            "Objectives the control supports: financial statement assertions "
            "for ICFR (Existence/Occurrence, Completeness, Accuracy/Valuation, "
            "Rights & Obligations, Presentation & Disclosure), ITGC domains "
            "(Access to programs and data, Program changes, Program "
            "development, Computer operations) or security objectives "
            "(Confidentiality, Integrity, Availability)."
        ),
    )
    ipe: List[str] = Field(
        default_factory=list,
        description=(
            "Information produced by the entity the control relies on "
            "(reports, queries, system extracts). Each needs its own "
            "completeness and accuracy procedure."
        ),
    )
    testing_procedures: ControlTesting = Field(
        ..., description="The exact testing procedures required."
    )


class Risk(BaseModel):
    risk_id: str = Field(
        ..., description="Unique immutable ID for the risk (e.g. RISK-001)."
    )
    description: str = Field(..., description="The risk description.")
    likelihood: Optional[RiskRatingField] = Field(
        None, description="Inherent likelihood before controls (Low/Medium/High)."
    )
    impact: Optional[RiskRatingField] = Field(
        None, description="Inherent impact before controls (Low/Medium/High)."
    )
    rating_rationale: Optional[str] = Field(
        None, description="One sentence on why this likelihood and impact."
    )
    regulatory_mapping: List[str] = Field(
        ...,
        description=(
            "Control-framework references for this risk, e.g. 'COSO 2013 "
            "Principle 11' (ICFR), 'NIST SP 800-53 AC-2', 'CIS AWS Foundations', "
            "'ISO/IEC 27001:2022 A.5.15', 'SCF IAC-01'. Only reference IDs you "
            "are sure exist. Auditing standards (e.g. PCAOB AS 2201) govern the "
            "auditor, not the entity's controls, and are not mappings."
        ),
    )
    controls: List[Control] = Field(
        ..., description="List of controls mitigating this risk."
    )


class RiskControlMatrixSchema(BaseModel):
    theme: str = Field(..., description="The audit theme.")
    risks: List[Risk] = Field(
        ..., description="The evaluated risks and their mitigating controls."
    )


class QA_PushbackSchema(BaseModel):
    approved: bool = Field(
        ...,
        description="True if no errors found, False if the drafted artifact needs rework.",
    )
    rejection_reason: Optional[str] = Field(
        default=None,
        description=(
            "Specific, actionable critique naming each control/finding ID and "
            "exactly what the drafting agent must fix."
        ),
    )


# ── Fieldwork: working papers ────────────────────────────────────────────────


class DesignConclusion(StrEnum):
    EFFECTIVE = "Effective"
    INEFFECTIVE = "Ineffective"
    NOT_TESTED = "Not tested"


class OperatingConclusion(StrEnum):
    EFFECTIVE = "Effective"
    EXCEPTIONS_NOTED = "Exceptions noted"
    INEFFECTIVE = "Ineffective"
    NOT_TESTED = "Not tested"


class FindingResult(StrEnum):
    NO_EXCEPTION = "No exception"
    EXCEPTION = "Exception"
    NOT_TESTED = "Not tested"


# Pre-split sessions stored one ``severity`` string per finding. They are
# migrated on load; the original label is kept in ``legacy_severity``.
_LEGACY_PASS = {"pass", "passed", "effective", "no exception"}
_LEGACY_NOT_TESTED = {"not tested", "n/a", "not applicable", "none", ""}


def _migrate_legacy_severity(data: dict[str, Any]) -> dict[str, Any]:
    severity = data.pop("severity")
    if any(
        k in data for k in ("tod_conclusion", "toe_conclusion", "result")
    ):  # already new-shape: a stray key, ignore it
        return data
    label = _norm(str(severity or ""))
    data["legacy_severity"] = str(severity)
    if label in _LEGACY_PASS:
        data.update(
            tod_conclusion=DesignConclusion.EFFECTIVE,
            toe_conclusion=OperatingConclusion.EFFECTIVE,
            result=FindingResult.NO_EXCEPTION,
            preliminary_deficiency=False,
        )
    elif label in _LEGACY_NOT_TESTED:
        data.update(
            tod_conclusion=DesignConclusion.NOT_TESTED,
            toe_conclusion=OperatingConclusion.NOT_TESTED,
            result=FindingResult.NOT_TESTED,
            preliminary_deficiency=False,
        )
    else:
        # Control / Significant Deficiency, Material Weakness, or any other
        # non-pass label: the old model only recorded that the test failed.
        data.update(
            tod_conclusion=DesignConclusion.NOT_TESTED,
            toe_conclusion=OperatingConclusion.INEFFECTIVE,
            result=FindingResult.EXCEPTION,
            preliminary_deficiency=True,
        )
    return data


def _derive_result(
    tod: DesignConclusion, toe: OperatingConclusion, exceptions: Optional[int]
) -> FindingResult:
    if (
        tod == DesignConclusion.INEFFECTIVE
        or toe
        in (OperatingConclusion.EXCEPTIONS_NOTED, OperatingConclusion.INEFFECTIVE)
        or (exceptions or 0) > 0
    ):
        return FindingResult.EXCEPTION
    if tod == DesignConclusion.NOT_TESTED and toe == OperatingConclusion.NOT_TESTED:
        return FindingResult.NOT_TESTED
    return FindingResult.NO_EXCEPTION


class AuditFindingSchema(BaseModel):
    control_id: str = Field(..., description="The ID of the control being evaluated.")
    vault_id_reference: str = Field(
        default="",
        description=(
            "Vault ID of the raw evidence (as printed by the collection tool). "
            "Empty only when the control was not tested."
        ),
    )
    exact_quote_from_evidence: str = Field(
        default="",
        description=(
            "Verbatim substring of the raw evidence supporting the conclusion. "
            "Empty only when the control was not tested."
        ),
    )
    tod_conclusion: Annotated[DesignConclusion, _lenient(DesignConclusion)] = Field(
        ...,
        description=(
            "Test of design and implementation: Effective / Ineffective / Not "
            "tested. 'Not tested' when no evidence covers the design."
        ),
    )
    toe_conclusion: Annotated[OperatingConclusion, _lenient(OperatingConclusion)] = (
        Field(
            ...,
            description=(
                "Test of operating effectiveness over the period: Effective / "
                "Exceptions noted / Ineffective / Not tested. A single "
                "point-in-time configuration read is NOT operating-effectiveness "
                "evidence: use 'Not tested' or state the reliance in toe_basis."
            ),
        )
    )
    toe_basis: Optional[str] = Field(
        default=None,
        description=(
            "Basis for the ToE conclusion: sample tested over which period, or "
            "the reliance assumption (e.g. 'test of one; relies on effective "
            "change-management ITGCs for the rest of the period')."
        ),
    )
    items_tested: Annotated[Optional[int], BeforeValidator(_lenient_int)] = Field(
        default=None, ge=0, description="Number of items or occurrences inspected."
    )
    exceptions_noted: Annotated[Optional[int], BeforeValidator(_lenient_int)] = Field(
        default=None,
        ge=0,
        description="Number of exceptions found in the items tested.",
    )
    result: Annotated[Optional[FindingResult], _lenient(FindingResult)] = Field(
        default=None,
        description=(
            "Overall result: No exception / Exception / Not tested. Derived "
            "from the ToD/ToE conclusions when omitted."
        ),
    )
    preliminary_deficiency: Optional[bool] = Field(
        default=None,
        description=(
            "True if the exception indicates a possible control deficiency. "
            "Preliminary only — classification (e.g. significant deficiency) "
            "happens at engagement level in Reporting. Defaults to True for an "
            "Exception."
        ),
    )
    test_conclusion: str = Field(
        ...,
        description="What was tested, what the evidence shows, and why the conclusions follow.",
    )
    legacy_severity: SkipJsonSchema[Optional[str]] = Field(
        default=None,
        description=(
            "Severity label from a session created before the fieldwork / "
            "deficiency-evaluation split. Kept for traceability only."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate(cls, data: Any) -> Any:
        if isinstance(data, dict) and "severity" in data:
            return _migrate_legacy_severity(dict(data))
        return data

    @model_validator(mode="after")
    def _consistent(self) -> "AuditFindingSchema":
        if (
            self.toe_conclusion == OperatingConclusion.EFFECTIVE
            and (self.exceptions_noted or 0) > 0
        ):
            raise ValueError(
                f"{self.control_id}: ToE 'Effective' with exceptions_noted="
                f"{self.exceptions_noted}; conclude 'Exceptions noted' or "
                "'Ineffective'"
            )
        derived = _derive_result(
            self.tod_conclusion, self.toe_conclusion, self.exceptions_noted
        )
        if self.result is None:
            self.result = derived
        elif self.result != derived:
            raise ValueError(
                f"{self.control_id}: result '{self.result}' contradicts ToD "
                f"'{self.tod_conclusion}', ToE '{self.toe_conclusion}' and "
                f"exceptions_noted={self.exceptions_noted} (expected '{derived}')"
            )
        if self.preliminary_deficiency is None:
            self.preliminary_deficiency = self.result == FindingResult.EXCEPTION
        elif self.preliminary_deficiency and self.result != FindingResult.EXCEPTION:
            raise ValueError(
                f"{self.control_id}: preliminary_deficiency requires result 'Exception'"
            )
        tested = self.result != FindingResult.NOT_TESTED
        if tested and not (
            self.exact_quote_from_evidence.strip() and self.vault_id_reference.strip()
        ):
            raise ValueError(
                f"{self.control_id}: a tested control needs a verbatim evidence "
                "quote and its vault ID; otherwise conclude 'Not tested'"
            )
        return self


class WorkingPaperSchema(BaseModel):
    theme: str = Field(..., description="The overarching audit theme.")
    findings: List[AuditFindingSchema] = Field(
        ..., description="One finding per RACM control, in RACM order."
    )


# ── Reporting: engagement-level deficiency evaluation ────────────────────────


class DeficiencyScale(StrEnum):
    ICFR = "ICFR deficiency scale"
    RISK_RATING = "Risk rating"


class DeficiencyClassification(StrEnum):
    # ICFR / SOX scopes
    CONTROL_DEFICIENCY = "Control Deficiency"
    SIGNIFICANT_DEFICIENCY = "Significant Deficiency"
    MATERIAL_WEAKNESS = "Material Weakness"
    # Other scopes
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    # Either scale: the exception does not amount to a deficiency.
    NOT_A_DEFICIENCY = "Not a deficiency"


SCALE_CLASSIFICATIONS: dict[DeficiencyScale, frozenset[DeficiencyClassification]] = {
    DeficiencyScale.ICFR: frozenset(
        {
            DeficiencyClassification.CONTROL_DEFICIENCY,
            DeficiencyClassification.SIGNIFICANT_DEFICIENCY,
            DeficiencyClassification.MATERIAL_WEAKNESS,
            DeficiencyClassification.NOT_A_DEFICIENCY,
        }
    ),
    DeficiencyScale.RISK_RATING: frozenset(
        {
            DeficiencyClassification.LOW,
            DeficiencyClassification.MEDIUM,
            DeficiencyClassification.HIGH,
            DeficiencyClassification.NOT_A_DEFICIENCY,
        }
    ),
}


class DeficiencyEvaluationSchema(BaseModel):
    """One deficiency, or a group of related findings aggregated into one."""

    deficiency_id: str = Field(..., description="Unique ID, e.g. DEF-01.")
    title: str = Field(..., description="Short neutral title of the deficiency.")
    related_findings: List[str] = Field(
        ...,
        min_length=1,
        description=(
            "control_ids of the working-paper findings evaluated together. "
            "Group findings that affect the same risk, assertion or system."
        ),
    )
    related_risks: List[str] = Field(
        default_factory=list, description="RACM risk_ids affected."
    )
    compensating_controls: str = Field(
        ...,
        description=(
            "Compensating or mitigating controls considered and whether they "
            "were tested; 'None identified' if none. An untested compensating "
            "control cannot reduce the classification."
        ),
    )
    likelihood: RiskRatingField = Field(
        ...,
        description=(
            "Likelihood that the deficiency results in the risk occurring. For "
            "ICFR: Low = remote; Medium/High = a reasonable possibility that a "
            "misstatement is not prevented or detected on a timely basis."
        ),
    )
    magnitude: RiskRatingField = Field(
        ...,
        description=(
            "Potential magnitude of the impact. For ICFR: High = the potential "
            "misstatement could be material to the financial statements."
        ),
    )
    classification: Annotated[
        DeficiencyClassification, _lenient(DeficiencyClassification)
    ] = Field(
        ...,
        description=(
            "Proposed classification on the engagement's scale. ICFR scopes: "
            "Control Deficiency / Significant Deficiency / Material Weakness. "
            "Other scopes: Low / Medium / High. 'Not a deficiency' if the "
            "evaluation concludes the exception is not a deficiency."
        ),
    )
    rationale: str = Field(
        ...,
        description=(
            "Why this classification: aggregation, compensating controls, "
            "likelihood and magnitude. DRAFT for the auditor's judgement at "
            "Gate 3 — not a final conclusion."
        ),
    )

    @model_validator(mode="after")
    def _material_weakness_needs_magnitude(self) -> "DeficiencyEvaluationSchema":
        if self.classification == DeficiencyClassification.MATERIAL_WEAKNESS and (
            self.likelihood == RiskRating.LOW or self.magnitude != RiskRating.HIGH
        ):
            raise ValueError(
                f"{self.deficiency_id}: a Material Weakness requires at least a "
                "reasonable possibility (likelihood Medium/High) of a material "
                "misstatement (magnitude High)"
            )
        return self


def _check_scale(
    scale: Optional[DeficiencyScale], evaluations: List[DeficiencyEvaluationSchema]
) -> Optional[DeficiencyScale]:
    """Return the scale, inferred when unset and unambiguous; raise if the
    classifications are not all on one scale."""
    used = {e.classification for e in evaluations}
    if scale is None:
        matches = [s for s, allowed in SCALE_CLASSIFICATIONS.items() if used <= allowed]
        if not matches:
            raise ValueError("deficiency evaluations mix ICFR and risk-rating scales")
        # Nothing (or only "Not a deficiency") classified: scale unknown.
        return matches[0] if len(matches) == 1 else None
    stray = used - SCALE_CLASSIFICATIONS[scale]
    if stray:
        raise ValueError(
            f"classifications {sorted(stray)} are not on the '{scale}' scale"
        )
    return scale


class DeficiencyEvaluationSetSchema(BaseModel):
    """Output of the reporting crew's deficiency-evaluation task."""

    deficiency_scale: Annotated[DeficiencyScale, _lenient(DeficiencyScale)] = Field(
        ..., description="The classification scale given in the task."
    )
    evaluations: List[DeficiencyEvaluationSchema] = Field(
        default_factory=list,
        description=(
            "One entry per deficiency or aggregated group. Every finding with "
            "preliminary_deficiency=true must appear in exactly one entry."
        ),
    )

    @model_validator(mode="after")
    def _scale(self) -> "DeficiencyEvaluationSetSchema":
        _check_scale(self.deficiency_scale, self.evaluations)
        return self


class FinalReportSchema(BaseModel):
    executive_summary: str = Field(
        ...,
        description="Board-level executive summary of the key findings and their business impact.",
    )
    detailed_report: str = Field(
        ...,
        description="The comprehensive technical narrative mapping findings to risks and frameworks.",
    )
    compliance_tone_approved: bool = Field(
        ..., description="Must be approved by QA Tone Gate before saving."
    )
    deficiency_evaluations: List[DeficiencyEvaluationSchema] = Field(
        default_factory=list,
        description=(
            "Engagement-level deficiency evaluation, copied from the "
            "deficiency-evaluation task. DRAFT for the auditor's judgement at "
            "Gate 3."
        ),
    )
    # Declared after deficiency_evaluations so its validator can see them. A
    # field validator (not a model validator) so it runs only on real input.
    deficiency_scale: Annotated[
        Optional[DeficiencyScale], _lenient(DeficiencyScale)
    ] = Field(
        default=None,
        validate_default=True,
        description="Scale used to classify deficiencies (ICFR or risk rating).",
    )
    oscal_sar: Optional["OSCAL_SAR_Schema"] = Field(
        None, description="The machine-readable OSCAL Security Assessment Report."
    )

    @field_validator("deficiency_scale")
    @classmethod
    def _scale(
        cls, value: Optional[DeficiencyScale], info: ValidationInfo
    ) -> Optional[DeficiencyScale]:
        return _check_scale(value, info.data.get("deficiency_evaluations") or [])


class OSCAL_SAR_Metadata(BaseModel):
    title: str = Field(..., description="Report title.")
    last_modified: str = Field(
        ..., description="ISO 8601 timestamp of last modification."
    )
    version: str = Field(..., description="Report version string.")
    oscal_version: str = Field(default="1.1.2", description="OSCAL schema version.")


class OSCAL_SAR_ImportAP(BaseModel):
    """NIST OSCAL's required `import-ap` — links these results back to the
    assessment plan (and, transitively, the system security plan) they assess."""

    href: str = Field(
        ...,
        description=(
            "Reference (URI or identifier) to the OSCAL assessment plan this "
            "result set was generated from."
        ),
    )
    remarks: Optional[str] = Field(
        None, description="Optional remarks about the imported assessment plan."
    )


class OSCAL_SAR_Observation(BaseModel):
    observation_id: str = Field(..., description="Unique identifier for the finding.")
    description: str = Field(..., description="The narrative finding or deficiency.")
    methods: List[str] = Field(
        ..., description="Assessment methods used: examine, test, or interview."
    )
    subjects: List[str] = Field(
        ..., description="The control IDs or system components assessed."
    )
    relevant_evidence: List[str] = Field(
        ..., description="Vault ID hashes mapping to evidence stored in the vault."
    )


class OSCAL_SAR_Result(BaseModel):
    assessment_result_id: str = Field(..., description="Unique ID for this result set.")
    start_date: str = Field(..., description="ISO 8601 assessment start.")
    end_date: str = Field(..., description="ISO 8601 assessment end.")
    observations: List[OSCAL_SAR_Observation] = Field(
        ..., description="Technical findings and gaps identified."
    )


class OSCAL_SAR_Schema(BaseModel):
    metadata: OSCAL_SAR_Metadata
    import_ap: OSCAL_SAR_ImportAP
    results: List[OSCAL_SAR_Result]


FinalReportSchema.model_rebuild()
