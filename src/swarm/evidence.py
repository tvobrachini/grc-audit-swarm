from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field


EvidencePayload = str | Dict[str, Any] | List[Any]


class ToolEvidence(BaseModel):
    tool_name: str
    payload: EvidencePayload


class ControlEvidence(BaseModel):
    control_id: str
    source: Literal["mcp", "mock"] = "mcp"
    tool_results: Dict[str, ToolEvidence] = Field(default_factory=dict)


def serialize_control_evidence(evidence: ControlEvidence) -> str:
    lines = [
        f"Evidence Source: {evidence.source}",
        f"Control ID: {evidence.control_id}",
    ]
    for tool_name, tool_evidence in evidence.tool_results.items():
        lines.append(f"{tool_name}: {tool_evidence.payload}")
    return "\n".join(lines)
