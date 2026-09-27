"""
Tests for GET /api/stream/{session_id} (src/api/routers/phases.py) and the
event-queue lifecycle it relies on in src/api/job_store.py.

The route's generator never terminates on its own (it emits a heartbeat every
second forever), and closing a live streamed httpx/TestClient connection onto
it turned out to hang this test suite (the portal-based transport blocks on
teardown of an endlessly-yielding StreamingResponse). So: the HTTP-auth test
goes through TestClient as normal (a 401 is an ordinary bounded response), but
delivery/order/cleanup tests drive the router's async generator directly and
close it themselves with `aclose()`, which is deterministic and fast.
"""

import asyncio
import json
import os
import sys
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from api import job_store
from api.job_store import get_queue, push_event, remove_flow, set_flow
from api.routers.phases import stream_events

AUTH = {"Authorization": "Bearer test-token"}
_DATA_PREFIX = "data: "


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "test-token")
    from api.main import app

    return TestClient(app)


async def _collect_events(session_id: str, count: int) -> list[dict]:
    """Drive stream_events()'s generator directly and grab `count` events.

    Skips heartbeat chunks (": heartbeat\\n\\n") and always closes the
    generator afterwards, mirroring what a disconnecting client causes the
    ASGI server to do.
    """
    response = await stream_events(session_id)
    events: list[dict] = []
    if count == 0:
        # Just trigger the route's queue registration; nothing to await.
        await response.body_iterator.aclose()
        return events
    try:
        async for chunk in response.body_iterator:
            if chunk.startswith(_DATA_PREFIX):
                events.append(json.loads(chunk[len(_DATA_PREFIX) :].rstrip("\n")))
                if len(events) == count:
                    break
    finally:
        await response.body_iterator.aclose()
    return events


class TestStreamAuth:
    def test_requires_auth(self, client):
        resp = client.get(f"/api/stream/{uuid.uuid4()}")
        assert resp.status_code == 401


class TestStreamDeliversEvents:
    def test_events_are_delivered_in_order(self):
        sid = f"sess-{uuid.uuid4()}"
        push_event(sid, {"type": "status", "status": "RUNNING_PHASE_1"})
        push_event(sid, {"type": "status", "status": "WAITING_HUMAN_GATE_1"})
        push_event(sid, {"type": "error", "reason": "boom"})

        received = asyncio.run(_collect_events(sid, 3))

        assert received == [
            {"type": "status", "status": "RUNNING_PHASE_1"},
            {"type": "status", "status": "WAITING_HUMAN_GATE_1"},
            {"type": "error", "reason": "boom"},
        ]

    def test_unknown_session_id_still_streams_pushed_events(self):
        """An id with events already queued internally can be streamed."""
        sid = f"never-created-{uuid.uuid4()}"
        push_event(sid, {"type": "status", "status": "RUNNING_PHASE_1"})

        received = asyncio.run(_collect_events(sid, 1))

        assert received == [{"type": "status", "status": "RUNNING_PHASE_1"}]


class TestStreamQueueCleanup:
    def test_remove_flow_clears_the_session_queue(self):
        sid = f"sess-{uuid.uuid4()}"
        push_event(sid, {"type": "status", "status": "x"})
        original_queue = get_queue(sid)

        remove_flow(sid)

        assert get_queue(sid) is not original_queue

    def test_opening_the_stream_registers_a_queue_for_a_known_session(self):
        sid = f"sess-{uuid.uuid4()}"
        set_flow(sid, object())  # a live session held in memory
        try:
            assert sid not in job_store._event_queues

            asyncio.run(_collect_events(sid, 0))

            assert sid in job_store._event_queues
        finally:
            remove_flow(sid)


class TestStreamUnboundedQueueGrowth:
    def test_streaming_random_session_ids_does_not_grow_the_queue_table(self):
        """Unknown ids get a 404 and never create a queue (unbounded growth)."""
        before = len(job_store._event_queues)
        for _ in range(10):
            sid = f"scratch-{uuid.uuid4()}"
            with pytest.raises(HTTPException) as exc:
                asyncio.run(_collect_events(sid, 0))
            assert exc.value.status_code == 404
        after = len(job_store._event_queues)
        assert after == before
