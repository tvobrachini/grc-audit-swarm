"""Test helpers: record reviewer decisions through the API, as a reviewer would.

The decisions are the demo walk-through's examples
(``swarm.demo.demo_review_decisions``); the application never records them
itself.
"""

from typing import Any

from swarm.demo import demo_review_decisions


def record_demo_decisions(
    client: Any, sid: str, stage: str, reviewer: str, headers: dict[str, str]
) -> list[dict[str, Any]]:
    """POST the walk-through decisions for ``stage`` as ``reviewer``."""
    detail = client.get(f"/api/sessions/{sid}", headers=headers).json()
    recorded = []
    for body in demo_review_decisions(stage, detail["effective"]):
        r = client.post(
            f"/api/sessions/{sid}/decisions",
            headers=headers,
            json={**body, "decided_by": reviewer},
        )
        assert r.status_code == 201, r.text
        recorded.append(r.json())
    return recorded


def record_flow_decisions(flow: Any, stage: str, reviewer: str) -> list[Any]:
    """Same, directly on an AuditFlow."""
    view = flow.effective_view().model_dump(mode="json")
    return [
        flow.record_decision(decided_by=reviewer, **body)
        for body in demo_review_decisions(stage, view)
    ]
