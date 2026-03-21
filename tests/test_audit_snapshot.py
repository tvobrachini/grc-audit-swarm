import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.audit_snapshot import build_audit_state_snapshot
from swarm.workflow_types import ExecutionStatus, ViewPhase


def test_build_audit_state_snapshot_summarizes_findings_and_execution_status():
    snapshot = build_audit_state_snapshot(
        ViewPhase.PHASE2_REVIEW,
        ("human_review_execution",),
        {
            "risk_themes": ["AWS", "Logging"],
            "control_matrix": [{"control_id": "AC-01"}, {"control_id": "LOG-01"}],
            "testing_findings": [
                {"status": "Pass"},
                {"status": "Fail"},
                {"status": "Fail"},
            ],
            "execution_status": {
                "AC-01": ExecutionStatus.CLEAN,
                "LOG-01": ExecutionStatus.FLAGGED,
            },
            "executive_summary": "summary",
        },
    )

    assert snapshot.view_phase == ViewPhase.PHASE2_REVIEW
    assert snapshot.next_nodes == ["human_review_execution"]
    assert snapshot.control_count == 2
    assert snapshot.finding_count == 3
    assert snapshot.finding_status_counts == {"Pass": 1, "Fail": 2}
    assert snapshot.execution_status == {
        "AC-01": "clean",
        "LOG-01": "flagged",
    }
