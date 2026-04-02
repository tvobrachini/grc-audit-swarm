from pydantic import BaseModel, Field
from typing import List, Optional

class ControlTestStep(BaseModel):
    step_description: str = Field(..., description="Actionable step for the Field Auditor.")
    expected_result: str = Field(..., description="What the evidence must show to pass.")

class ControlTesting(BaseModel):
    test_of_design: List[ControlTestStep] = Field(..., description="Steps to test control design.")
    test_of_effectiveness: List[ControlTestStep] = Field(..., description="Steps to test operating effectiveness, including sampling instructions if applicable.")
    substantive_testing: Optional[List[ControlTestStep]] = Field(None, description="Substantive testing procedures if required.")

class Control(BaseModel):
    control_id: str = Field(..., description="Unique immutable ID for the control (e.g. CTRL-001a).")
    description: str = Field(..., description="Description of the internal control.")
    testing_procedures: ControlTesting = Field(..., description="The exact testing procedures required.")

class Risk(BaseModel):
    risk_id: str = Field(..., description="Unique immutable ID for the risk (e.g. RISK-001).")
    description: str = Field(..., description="The risk description.")
    regulatory_mapping: List[str] = Field(..., description="Mapped frameworks (e.g. COSO, PCAOB AS 2201).")
    controls: List[Control] = Field(..., description="List of controls mitigating this risk.")

class RiskControlMatrixSchema(BaseModel):
    theme: str = Field(..., description="The audit theme.")
    risks: List[Risk] = Field(..., description="The evaluated risks and their mitigating controls.")

class QA_PushbackSchema(BaseModel):
    approved: bool = Field(..., description="True if no errors found, False if the drafted artifact needs rework.")
    rejection_reason: Optional[str] = Field(None, description="Detailed critique mapping exactly to what the agents must fix based on PCAOB/IIA standards.")
