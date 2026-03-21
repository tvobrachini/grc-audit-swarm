from typing import Any

from swarm.graph import app as swarm_app


class GraphService:
    def __init__(self, graph_app=swarm_app):
        self._graph_app = graph_app

    def get_state(self, config: dict[str, Any]):
        return self._graph_app.get_state(config)

    def stream_updates(
        self,
        stream_input: dict[str, Any] | None,
        config: dict[str, Any],
    ):
        return self._graph_app.stream(
            stream_input, config=config, stream_mode="updates"
        )

    def update_state(self, config: dict[str, Any], values: dict[str, Any]) -> None:
        self._graph_app.update_state(config, values)
