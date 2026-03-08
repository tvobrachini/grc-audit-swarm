from pytest_bdd import scenario, given, when, then
from src.swarm.state.schema import ControlMatrixItem, AuditState
from src.swarm.agents.worker import run_control_test


@scenario(
    "features/evaluation.feature",
    "Missing Evidence always results in Failure or Exception",
)
def test_missing_evidence_fails():
    pass


@given(
    'the worker agent is evaluating a control "IAM-01"', target_fixture="setup_control"
)
def setup_control():
    control = ControlMatrixItem(
        control_id="IAM-01",
        domain="Identity",
        description="All accounts must have MFA.",
        procedures={
            "control_id": "IAM-01",
            "tod_steps": ["Check MFA policy"],
            "toe_steps": [],
            "substantive_steps": [],
            "erl_items": ["Screenshot of MFA setting"],
        },
    )
    state = AuditState(
        audit_scope_narrative="Review IAM",
        control_matrix=[control],
        testing_findings=[],
        audit_trail=[],
    )
    return control, state


@when(
    "the provided evidence is empty or missing",
    target_fixture="evaluate_missing_evidence",
)
def evaluate_missing_evidence(setup_control):
    control, state = setup_control
    # We mock out Groq here just for this domain rule simulation.
    # In a real environment, we would invoke the node with an empty human context.
    # Here, we supply human context explicitly stating "No evidence provided."
    finding = run_control_test(
        control, state, human_context="No evidence provided for this control."
    )
    return finding


@then('the AI evaluation must not return a "Pass" status')
def verify_finding_status(evaluate_missing_evidence):
    finding = evaluate_missing_evidence
    assert finding.status in ["Fail", "Exception"], (
        f"Domain Rule Violated: Agent passed a control with missing evidence! Got status: {finding.status}"
    )
