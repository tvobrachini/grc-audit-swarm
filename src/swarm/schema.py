from pydantic import BaseModel, Field
from typing import List, Optional


class ControlTestStep(BaseModel):
    step_description: str = Field(
        ..., description="Actionable step for the Field Auditor."
    )
    expected_result: str = Field(
        ..., description="What the evidence must show to pass."
    )


class ControlTesting(BaseModel):
    test_of_design: List[ControlTestStep] = Field(
        ..., description="Steps to test control design."
    )
    test_of_effectiveness: List[ControlTestStep] = Field(
        ...,
        description="Steps to test operating effectiveness, including sampling instructions if applicable.",
    )
    substantive_testing: Optional[List[ControlTestStep]] = Field(
        None, description="Substantive testing procedures if required."
    )


class Control(BaseModel):
    control_id: str = Field(
        ..., description="Unique immutable ID for the control (e.g. CTRL-001a)."
    )
    description: str = Field(..., description="Description of the internal control.")
    testing_procedures: ControlTesting = Field(
        ..., description="The exact testing procedures required."
    )


class Risk(BaseModel):
    risk_id: str = Field(
        ..., description="Unique immutable ID for the risk (e.g. RISK-001)."
    )
    description: str = Field(..., description="The risk description.")
    regulatory_mapping: List[str] = Field(
        ..., description="Mapped frameworks (e.g. COSO, PCAOB AS 2201)."
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
        None,
        description="Detailed critique mapping exactly to what the agents must fix based on PCAOB/IIA standards.",
    )


class AuditFindingSchema(BaseModel):
    control_id: str = Field(..., description="The ID of the control being evaluated.")
    vault_id_reference: str = Field(
        ...,
        description="The immutable UUID hash belonging to the raw testing evidence.",
    )
    exact_quote_from_evidence: str = Field(
        ..., description="Exact substring from the raw evidence proving the condition."
    )
    test_conclusion: str = Field(
        ..., description="Detailed description of execution results."
    )
    severity: str = Field(
        ...,
        description="Must be 'Pass', 'Control Deficiency', 'Significant Deficiency', or 'Material Weakness'.",
    )


class WorkingPaperSchema(BaseModel):
    theme: str = Field(..., description="The overarching audit theme.")
    findings: List[AuditFindingSchema] = Field(
        ..., description="The evaluated findings mapping back to the RACM controls."
    )


class FinalReportSchema(BaseModel):
    executive_summary: str = Field(
        ...,
        description="Board-level executive summary summarizing major compliance gaps.",
    )
    detailed_report: str = Field(
        ...,
        description="The comprehensive technical narrative mapping findings to original frameworks.",
    )
    compliance_tone_approved: bool = Field(
        ..., description="Must be approved by QA Tone Gate before saving."
    )
    oscal_sar: Optional["OSCAL_SAR_Schema"] = Field(
        None, description="The machine-readable OSCAL Security Assessment Report."
    )


class OSCAL_SAR_Metadata(BaseModel):
    title: str = Field(..., description="Report title.")
    last_modified: str = Field(
        ..., description="ISO 8601 timestamp of last modification."
    )
    version: str = Field(..., description="Report version string.")
    oscal_version: str = Field(default="1.1.2", description="OSCAL schema version.")


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
    results: List[OSCAL_SAR_Result]
