from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditStateSnapshot(BaseModel):
    captured_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    view_phase: str
    next_nodes: list[str] = Field(default_factory=list)
    risk_themes: list[str] = Field(default_factory=list)
    control_count: int = 0
    finding_count: int = 0
    finding_status_counts: dict[str, int] = Field(default_factory=dict)
    execution_status: dict[str, str] = Field(default_factory=dict)
    executive_summary: str = ""


def build_audit_state_snapshot(
    view_phase: str,
    next_nodes: list[str] | tuple[str, ...] | None,
    state_vals: dict[str, Any],
) -> AuditStateSnapshot:
    findings = state_vals.get("testing_findings", [])
    status_counts: dict[str, int] = {}
    for finding in findings:
        if isinstance(finding, dict):
            status = finding.get("status", "unknown")
        else:
            status = getattr(finding, "status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    execution_status = {
        str(control_id): str(status)
        for control_id, status in (state_vals.get("execution_status") or {}).items()
    }

    return AuditStateSnapshot(
        view_phase=str(view_phase),
        next_nodes=[str(node) for node in (next_nodes or [])],
        risk_themes=list(state_vals.get("risk_themes") or []),
        control_count=len(state_vals.get("control_matrix") or []),
        finding_count=len(findings),
        finding_status_counts=status_counts,
        execution_status=execution_status,
        executive_summary=state_vals.get("executive_summary", ""),
    )
