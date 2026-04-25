"""
In-memory store for active AuditFlow instances and per-session SSE event queues.
Intentionally simple: no persistence, restarts lose in-flight jobs (acceptable limitation).
"""

import queue
import threading
from typing import Any, Optional

# session_id → AuditFlow instance
_flows: dict[str, Any] = {}
_flows_lock = threading.Lock()

# session_id → Queue of SSE event dicts
_event_queues: dict[str, queue.Queue] = {}
_queues_lock = threading.Lock()

# job_id → {status: running|completed|failed, error: str|None}
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def get_flow(session_id: str) -> Optional[Any]:
    with _flows_lock:
        return _flows.get(session_id)


def set_flow(session_id: str, flow: Any) -> None:
    with _flows_lock:
        _flows[session_id] = flow


def remove_flow(session_id: str) -> None:
    with _flows_lock:
        _flows.pop(session_id, None)
    with _queues_lock:
        _event_queues.pop(session_id, None)


def get_queue(session_id: str) -> queue.Queue:
    with _queues_lock:
        if session_id not in _event_queues:
            _event_queues[session_id] = queue.Queue()
        return _event_queues[session_id]


def push_event(session_id: str, event: dict) -> None:
    get_queue(session_id).put(event)


def set_job(job_id: str, status: str, error: Optional[str] = None) -> None:
    with _jobs_lock:
        _jobs[job_id] = {"status": status, "error": error}


def get_job(job_id: str) -> Optional[dict]:
    with _jobs_lock:
        return _jobs.get(job_id)
