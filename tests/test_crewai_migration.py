import os
import sys
import json

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.schema import RiskControlMatrixSchema, FinalReportSchema
from swarm.evidence import EvidenceAssuranceProtocol
from pydantic import ValidationError


def load_mock_data():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    mock_path = os.path.join(base_dir, "mock_data", "aws_s3_audit.json")
    with open(mock_path, "r") as f:
        return json.load(f)


def test_mock_data_integrity():
    """TDD Phase 0: Validate the mock data loads correctly before injecting it into the Agent Flow."""
    data = load_mock_data()
    assert data["theme"] == "AWS S3 Public Exposure Risk"
    assert "PCAOB AS 2201" in data["frameworks"]
    assert "internal-fin-data-prod" in data["mock_evidence"]["list_public_s3_buckets"]


def test_planning_phase_schema_guardrail():
    """TDD Phase 1: Ensures Pydantic schema structure perfectly catches hallucinated objects."""
    valid_data = {
        "theme": "S3 Risk",
        "risks": [
            {
                "risk_id": "RSK-001",
                "description": "Bucket exposed",
                "regulatory_mapping": ["COSO"],
                "controls": [
                    {
                        "control_id": "CTL-001",
                        "description": "Bucket privacy",
                        "testing_procedures": {
                            "test_of_design": [
                                {
                                    "step_description": "Check AWS",
                                    "expected_result": "Private",
                                }
                            ],
                            "test_of_effectiveness": [
                                {
                                    "step_description": "Use AWS CLI",
                                    "expected_result": "Deny",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    racm = RiskControlMatrixSchema(**valid_data)
    assert racm.risks[0].risk_id == "RSK-001"

    # Catching structural failure
    invalid_data = valid_data.copy()
    invalid_data["risks"][0]["controls"][0]["testing_procedures"].pop(
        "test_of_effectiveness"
    )
    try:
        RiskControlMatrixSchema(**invalid_data)
        assert False, "Should have failed due to missing mandatory ToE"
    except ValidationError:
        pass


def test_fieldwork_phase_evidence_hash_guardrail():
    """TDD Phase 2: Ensures PCAOB AS 1215 chain of custody hashing works seamlessly."""
    raw_payload_from_mcp = '{"buckets": ["internal-fin-data-prod"]}'

    # Register it with the vault
    result = EvidenceAssuranceProtocol.register_evidence(
        raw_payload_from_mcp, "aws-s3-mcp.list_buckets"
    )
    vault_id = result["vault_id"]

    assert vault_id is not None
    assert len(result["sha256"]) == 64

    # Verify deterministic checks
    assert (
        EvidenceAssuranceProtocol.verify_exact_quote(vault_id, "internal-fin-data-prod")
        is True
    )
    assert (
        EvidenceAssuranceProtocol.verify_exact_quote(vault_id, "hallucinated_bucket")
        is False
    )


def test_final_report_schema_guardrail():
    """TDD Phase 3: Ensures the reporting schema enforces the tone QA booleans."""
    valid_data = {
        "executive_summary": "Major S3 vulnerabilities found.",
        "detailed_report": "Details of the vulnerability mapped to PCAOB.",
        "compliance_tone_approved": True,
    }
    report = FinalReportSchema(**valid_data)
    assert report.compliance_tone_approved is True
