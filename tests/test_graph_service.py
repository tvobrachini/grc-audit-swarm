import os
import sys
from unittest.mock import Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.graph_service import GraphService


def test_graph_service_delegates_get_state():
    graph_app = Mock()
    graph_app.get_state.return_value = {"state": "ok"}

    service = GraphService(graph_app)

    result = service.get_state({"configurable": {"thread_id": "t1"}})

    assert result == {"state": "ok"}
    graph_app.get_state.assert_called_once_with({"configurable": {"thread_id": "t1"}})


def test_graph_service_delegates_stream_updates():
    graph_app = Mock()
    graph_app.stream.return_value = iter([{"node": {"audit_trail": []}}])

    service = GraphService(graph_app)

    result = service.stream_updates({"audit_scope_narrative": "scope"}, {"cfg": 1})

    assert list(result) == [{"node": {"audit_trail": []}}]
    graph_app.stream.assert_called_once_with(
        {"audit_scope_narrative": "scope"},
        config={"cfg": 1},
        stream_mode="updates",
    )


def test_graph_service_delegates_update_state():
    graph_app = Mock()
    service = GraphService(graph_app)

    service.update_state({"cfg": 1}, {"revision_feedback": ""})

    graph_app.update_state.assert_called_once_with(
        {"cfg": 1}, {"revision_feedback": ""}
    )
