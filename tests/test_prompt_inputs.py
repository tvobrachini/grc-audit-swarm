"""
Prompt wiring regression tests.

- Every ``{placeholder}`` in each crew's YAML (tasks *and* agents) must be
  satisfied by the inputs AuditFlow passes to ``crew.kickoff`` — this is the
  generic check that would have caught Reporting silently ignoring the
  working papers.
- Reporting receives the Phase 1 RACM and the Phase 2 working papers; the
  OSCAL task gets the control_id → vault_id index.
- The RACM drafter receives the Risk Specialist's weighting output as context.
"""

import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from crewai.utilities.string_utils import interpolate_only

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.schema import QA_PushbackSchema
from flow_builders import (  # type: ignore[import-not-found]
    PHASES,
    crew_result,
    make_flow,
    make_papers,
    make_racm,
    make_report,
)

CONFIG_DIR = Path(__file__).parent.parent / "src" / "swarm" / "config"
# Same pattern CrewAI uses for template variables.
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_\-]*)}")

CREW_CONFIGS = {
    1: ("planning_tasks.yaml", "planning_agents.yaml"),
    2: ("fieldwork_tasks.yaml", "fieldwork_agents.yaml"),
    3: ("reporting_tasks.yaml", "reporting_agents.yaml"),
}


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def _yaml_templates(phase: int) -> list[str]:
    out: list[str] = []
    for fname in CREW_CONFIGS[phase]:
        with open(CONFIG_DIR / fname, encoding="utf-8") as f:
            out.extend(_strings(yaml.safe_load(f)))
    return out


def _placeholders(phase: int) -> set[str]:
    found: set[str] = set()
    for s in _yaml_templates(phase):
        found.update(_PLACEHOLDER.findall(s))
    return found


def _captured_inputs(phase: int) -> list[dict]:
    """Run the phase with a mocked crew (QA rejects once) and return every
    inputs dict passed to kickoff — covers the first attempt and the retry."""
    flow = make_flow(phase)
    artifact = PHASES[phase]["make"]()
    rejected = QA_PushbackSchema(approved=False, rejection_reason="fix {braces}")
    approved = QA_PushbackSchema(approved=True)
    mock_crew = MagicMock()
    captured: list[dict] = []

    def kickoff(inputs):
        captured.append(dict(inputs))
        qa = rejected if len(captured) == 1 else approved
        return crew_result(phase, qa, artifact)

    mock_crew.kickoff.side_effect = kickoff
    with patch(f"swarm.audit_flow.{PHASES[phase]['crew']}") as MockCrew:
        MockCrew.return_value.crew.return_value = mock_crew
        getattr(flow, PHASES[phase]["run"])()
    assert flow.state.status == f"WAITING_HUMAN_GATE_{phase}"
    assert len(captured) == 2
    return captured


@pytest.mark.parametrize("phase", [1, 2, 3])
def test_every_yaml_placeholder_is_supplied(phase):
    required = _placeholders(phase)
    assert required, "expected at least one placeholder per crew"
    for inputs in _captured_inputs(phase):
        missing = required - set(inputs)
        assert not missing, f"phase {phase} kickoff inputs lack {sorted(missing)}"
        # And CrewAI itself can interpolate every template with these inputs.
        for template in _yaml_templates(phase):
            interpolate_only(template, inputs)


@pytest.mark.parametrize("phase", [1, 2, 3])
def test_no_unused_inputs(phase):
    """An input no template references is dead data (the original bug:
    working_papers_string was passed but never interpolated)."""
    required = _placeholders(phase)
    for inputs in _captured_inputs(phase):
        unused = set(inputs) - required
        assert not unused, f"phase {phase} passes unused inputs {sorted(unused)}"


class TestReportingSeesFieldwork:
    def _inputs(self) -> dict:
        return _captured_inputs(3)[0]

    def test_drafting_gets_working_papers(self):
        inputs = self._inputs()
        papers = make_papers()
        assert "vault-abc123" in inputs["working_papers_string"]
        assert "BlockPublicAcls: true" in inputs["working_papers_string"]
        assert papers.findings[0].test_conclusion in inputs["working_papers_string"]

    def test_drafting_gets_racm_and_scope(self):
        inputs = self._inputs()
        assert "RISK-01" in inputs["racm_summary"]
        assert "CTRL-01" in inputs["racm_summary"]
        # Summary is compact: no test procedures.
        assert "Inspect policy" not in inputs["racm_summary"]
        assert "AWS S3" in inputs["scope_string"]
        assert "Fintech storing customer data" in inputs["scope_string"]
        assert "CIS AWS" in inputs["scope_string"]

    def test_oscal_gets_findings_index(self):
        inputs = self._inputs()
        assert inputs["findings_index"] == "CTRL-01 | Pass | vault-abc123"
        assert inputs["theme"] == "AWS S3"

    def test_rendered_drafting_prompt_contains_fieldwork(self):
        inputs = self._inputs()
        with open(CONFIG_DIR / "reporting_tasks.yaml", encoding="utf-8") as f:
            tasks = yaml.safe_load(f)
        drafting = interpolate_only(tasks["drafting_task"]["description"], inputs)
        assert "vault-abc123" in drafting
        assert "CTRL-01" in drafting
        oscal = interpolate_only(
            tasks["generate_oscal_sar_task"]["description"], inputs
        )
        assert "CTRL-01 | Pass | vault-abc123" in oscal

    def test_working_papers_serialised_once_and_compact(self):
        inputs = self._inputs()
        blob = "".join(str(v) for v in inputs.values())
        assert blob.count("BlockPublicAcls: true") == 1
        assert "null" not in inputs["working_papers_string"]


def test_fieldwork_gets_full_racm_without_nulls():
    racm = make_racm()
    racm.risks[0].controls[0].testing_procedures.substantive_testing = None
    flow = make_flow(2)
    flow.state.racm_plan = racm
    inputs = flow._fieldwork_inputs()
    assert "Inspect policy" in inputs["racm_string"]
    assert "null" not in inputs["racm_string"]


def test_reporting_without_working_papers_is_an_error():
    flow = make_flow(3)
    flow.state.working_papers = None
    with patch("swarm.audit_flow.ReportingCrew") as MockCrew:
        flow.generate_reporting()
    assert flow.state.status == "ERROR_PHASE_3"
    MockCrew.assert_not_called()


def test_fieldwork_without_racm_is_an_error():
    flow = make_flow(2)
    flow.state.racm_plan = None
    with patch("swarm.audit_flow.FieldworkCrew") as MockCrew:
        flow.generate_fieldwork()
    assert flow.state.status == "ERROR_PHASE_2"
    MockCrew.assert_not_called()


class TestPlanningContextWiring:
    def _build(self):
        import swarm.crews.planning_crew as planning_crew

        tasks: dict[str, MagicMock] = {}

        def fake_task(**kwargs):
            t = MagicMock(name=kwargs["name"])
            t.kwargs = kwargs
            tasks[kwargs["name"]] = t
            return t

        with (
            patch.object(planning_crew, "get_crew_llm"),
            patch.object(planning_crew, "Agent"),
            patch.object(planning_crew, "Crew"),
            patch.object(planning_crew, "Task", side_effect=fake_task),
        ):
            planning_crew.PlanningCrew().crew()
        return tasks

    def test_racm_task_receives_weighting_output(self):
        tasks = self._build()
        assert tasks["racm_drafting_task"].kwargs["context"] == [
            tasks["weighting_task"]
        ]

    def test_qa_task_reviews_only_racm(self):
        tasks = self._build()
        assert tasks["qa_gate_task"].kwargs["context"] == [tasks["racm_drafting_task"]]


def test_report_fixture_is_valid():
    # Guard: shared builders used above stay schema-valid.
    assert make_report().compliance_tone_approved is True
