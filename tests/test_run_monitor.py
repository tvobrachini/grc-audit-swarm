"""
Regression test: run_monitor.py treated artifacts as dicts (``.get``) after
they became Pydantic models, crashing with AttributeError right after Phase 1.
The phase helpers are exercised with a real AuditFlow whose crew calls are
stubbed.
"""

import importlib
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flow_builders import make_papers, make_racm, make_report  # type: ignore[import-not-found]
from swarm.audit_flow import AuditFlow


@pytest.fixture(scope="module")
def run_monitor():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root not in sys.path:
        sys.path.insert(0, root)
    # The script mutates os.environ at import (load_dotenv, DEMO_MODE=0);
    # keep that from leaking into other tests.
    saved = dict(os.environ)
    try:
        module = importlib.import_module("run_monitor")
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return module


def _flow() -> AuditFlow:
    flow = AuditFlow()
    flow.state.theme = "AWS S3"
    flow.state.business_context = "Fintech"
    return flow


def _complete(flow: AuditFlow, phase: int, field: str, artifact) -> None:
    setattr(flow.state, field, artifact)
    flow.machine.complete_phase(phase)
    flow._commit_status()


def test_full_run_prints_model_artifacts(run_monitor, capsys):
    flow = _flow()

    def planning(*_a, **_k):
        flow.begin_phase_1()
        _complete(flow, 1, "racm_plan", make_racm())

    with patch.object(flow, "generate_planning", side_effect=planning):
        assert run_monitor.run_phase1(flow) is True

    with (
        patch.object(
            flow,
            "generate_fieldwork",
            side_effect=lambda *a, **k: _complete(
                flow, 2, "working_papers", make_papers()
            ),
        ),
        patch(
            "swarm.evidence.EvidenceAssuranceProtocol.verify_exact_quote",
            return_value=True,
        ),
    ):
        assert run_monitor.run_phase2(flow, skip_aws=False) is True

    with patch.object(
        flow,
        "generate_reporting",
        side_effect=lambda *a, **k: _complete(flow, 3, "final_report", make_report()),
    ):
        assert run_monitor.run_phase3(flow) is True

    out = capsys.readouterr().out
    assert "Risk RISK-01: 1 control(s)" in out
    assert "CTRL-01  [Pass]" in out
    assert "No exceptions." in out
    assert flow.state.status == "COMPLETED"


def test_skip_aws_injects_papers_via_state_machine(run_monitor):
    flow = AuditFlow(initial_status="WAITING_HUMAN_GATE_1")
    flow.state.theme = "AWS S3"
    assert run_monitor.run_phase2(flow, skip_aws=True) is True
    assert flow.state.status == "WAITING_HUMAN_GATE_2"
    assert flow.machine.status.value == "WAITING_HUMAN_GATE_2"
    assert flow.state.working_papers is not None
    assert flow.state.working_papers.findings[0].control_id == "CTRL-01"


def test_missing_racm_reports_failure(run_monitor):
    flow = _flow()

    def planning(*_a, **_k):
        flow.begin_phase_1()
        flow.machine.complete_phase(1)
        flow._commit_status()

    with patch.object(flow, "generate_planning", side_effect=planning):
        assert run_monitor.run_phase1(flow) is False
