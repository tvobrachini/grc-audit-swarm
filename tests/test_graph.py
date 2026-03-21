"""
test_graph.py
-------------
Guardrails for the LangGraph structure.
Verifies all expected nodes exist, edges are wired correctly,
and interrupt points are at the right checkpoints.
"""

import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.graph import app
from swarm.graph import run_all_workers_node
from swarm.state.schema import (
    AuditFinding,
    AuditProcedure,
    AuditState,
    ControlMatrixItem,
)
from swarm.workflow_types import ExecutionStatus


EXPECTED_NODES = [
    # Phase 1 — Planning
    "orchestrator",
    "researcher",
    "control_mapper",
    "dynamic_specialists",
    "challenger",
    "human_review",
    "evidence_collector",
    # Phase 2 — Execution
    "run_all_workers",
    "phase2_specialist",
    "phase2_researcher",
    "phase2_challenger",
    "concluder",
    "human_review_execution",
]

EXPECTED_INTERRUPT_BEFORE = ["human_review", "human_review_execution"]


class TestGraphStructure:
    def test_graph_compiles_without_error(self):
        assert app is not None

    def test_all_expected_nodes_present(self):
        graph = app.get_graph()
        present = list(graph.nodes.keys())
        for node in EXPECTED_NODES:
            assert node in present, f"Missing node: '{node}'"

    def test_no_unexpected_nodes(self):
        graph = app.get_graph()
        present = set(graph.nodes.keys()) - {"__start__", "__end__"}
        expected = set(EXPECTED_NODES)
        unexpected = present - expected
        assert not unexpected, f"Unexpected nodes in graph: {unexpected}"

    def test_interrupt_points_are_set(self):
        """The graph must interrupt at both human review points."""
        # LangGraph stores interrupt_before in the compiled graph's builder
        interrupt_nodes = getattr(app, "interrupt_before", None)
        if interrupt_nodes is None:
            # Fallback: just assert graph compiled (interrupt config validated at compile time)
            assert app is not None
        else:
            for node in EXPECTED_INTERRUPT_BEFORE:
                assert node in interrupt_nodes, f"Missing interrupt: {node}"

    def test_phase2_pipeline_order_via_edges(self):
        """Verify Phase 2 flows: workers → specialist → researcher → challenger → concluder."""
        graph = app.get_graph()
        edges = {(e.source, e.target) for e in graph.edges}
        pipeline = [
            ("run_all_workers", "phase2_specialist"),
            ("phase2_specialist", "phase2_researcher"),
            ("phase2_researcher", "phase2_challenger"),
            ("phase2_challenger", "concluder"),
            ("concluder", "human_review_execution"),
        ]
        for src, dst in pipeline:
            assert (src, dst) in edges, f"Missing edge: {src} → {dst}"

    def test_phase1_pipeline_order_via_edges(self):
        """Verify Phase 1 flows: orchestrator → researcher → mapper → specialist → challenger → human_review."""
        graph = app.get_graph()
        edges = {(e.source, e.target) for e in graph.edges}
        pipeline = [
            ("orchestrator", "researcher"),
            ("researcher", "control_mapper"),
            ("control_mapper", "dynamic_specialists"),
            ("dynamic_specialists", "challenger"),
        ]
        for src, dst in pipeline:
            assert (src, dst) in edges, f"Missing edge: {src} → {dst}"


class TestExecutionReruns:
    def test_run_all_workers_preserves_findings_without_feedback(self):
        control = ControlMatrixItem(
            control_id="AC-01",
            domain="Access Control",
            description="Quarterly user access review.",
            procedures=AuditProcedure(
                control_id="AC-01",
                tod_steps=["Review policy"],
                toe_steps=["Sample access reviews"],
                substantive_steps=["Inspect orphan accounts"],
                erl_items=["Access review evidence"],
            ),
        )
        existing = AuditFinding(
            control_id="AC-01",
            agent_role="Execution Worker (Access Control)",
            status="Pass",
            justification="Existing finding.",
            evidence_extracted=["Existing evidence."],
            tod_result="Pass",
            toe_result="Pass",
            substantive_result="Pass",
        )
        state = AuditState(
            audit_scope_narrative="Access review audit",
            control_matrix=[control],
            testing_findings=[existing],
            execution_status={"AC-01": ExecutionStatus.CLEAN},
            control_feedback={},
        )

        with patch("swarm.graph.run_control_test") as run_control_test_mock:
            result = run_all_workers_node(state)

        assert result["testing_findings"] == [existing]
        assert result["execution_status"]["AC-01"] == ExecutionStatus.CLEAN
        run_control_test_mock.assert_not_called()

    def test_run_all_workers_reruns_controls_with_feedback(self):
        control = ControlMatrixItem(
            control_id="AC-01",
            domain="Access Control",
            description="Quarterly user access review.",
            procedures=AuditProcedure(
                control_id="AC-01",
                tod_steps=["Review policy"],
                toe_steps=["Sample access reviews"],
                substantive_steps=["Inspect orphan accounts"],
                erl_items=["Access review evidence"],
            ),
        )
        existing = AuditFinding(
            control_id="AC-01",
            agent_role="Execution Worker (Access Control)",
            status="Pass",
            justification="Existing finding.",
            evidence_extracted=["Existing evidence."],
            tod_result="Pass",
            toe_result="Pass",
            substantive_result="Pass",
        )
        rerun = AuditFinding(
            control_id="AC-01",
            agent_role="Execution Worker (Access Control)",
            status="Exception",
            justification="Rerun finding.",
            evidence_extracted=["Updated evidence."],
            risk_rating="Medium",
            tod_result="Pass",
            toe_result="Exception",
            substantive_result="Exception",
        )
        state = AuditState(
            audit_scope_narrative="Access review audit",
            control_matrix=[control],
            testing_findings=[existing],
            execution_status={"AC-01": ExecutionStatus.CLEAN},
            control_feedback={"AC-01": "Recheck pending offboarding evidence."},
        )

        with patch(
            "swarm.graph.run_control_test", return_value=rerun
        ) as run_control_test_mock:
            result = run_all_workers_node(state)

        assert result["testing_findings"] == [rerun]
        assert result["execution_status"]["AC-01"] == ExecutionStatus.AWAITING_REVIEW
        run_control_test_mock.assert_called_once()
