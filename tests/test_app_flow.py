import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.app_flow import derive_app_view_state


class DummyState:
    def __init__(self, next_nodes, values):
        self.next = next_nodes
        self.values = values


def test_derive_app_view_state_for_phase1_review():
    state = DummyState(("human_review",), {"control_matrix": [{"id": 1}]})
    derived = derive_app_view_state(state)
    assert derived["at_phase1_review"] is True
    assert derived["at_phase2_review"] is False
    assert derived["should_stream"] is False


def test_derive_app_view_state_for_phase2_review():
    state = DummyState(
        ("human_review_execution",),
        {"control_matrix": [{"id": 1}], "testing_findings": [{"id": 2}]},
    )
    derived = derive_app_view_state(state)
    assert derived["at_phase2_review"] is True
    assert derived["is_phase1"] is False


def test_derive_app_view_state_for_completed_run():
    state = DummyState((), {"testing_findings": [{"id": 1}]})
    derived = derive_app_view_state(state)
    assert derived["is_fully_done"] is True
    assert derived["should_stream"] is False
