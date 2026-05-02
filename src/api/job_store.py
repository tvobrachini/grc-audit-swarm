"""
In-memory store for active AuditFlow instances and per-session SSE event queues.
Intentionally simple: no persistence, restarts lose in-flight jobs (acceptable limitation).
"""

import asyncio
import threading
from typing import Any, Optional

_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


# session_id → AuditFlow instance
_flows: dict[str, Any] = {}
_flows_lock = threading.Lock()

# session_id → Queue of SSE event dicts
_event_queues: dict[str, asyncio.Queue] = {}
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


def get_queue(session_id: str) -> asyncio.Queue:
    # First, try to get the queue without holding the lock for long if we need to create it safely
    with _queues_lock:
        if session_id in _event_queues:
            return _event_queues[session_id]

    # Queue does not exist. We need to create it.
    # To prevent deadlocks, we must not hold the lock while waiting on the event loop.
    new_q = None
    if _main_loop and not _main_loop.is_closed():
        try:
            asyncio.get_running_loop()
            new_q = asyncio.Queue()
        except RuntimeError:
            future = asyncio.run_coroutine_threadsafe(_create_queue(), _main_loop)
            new_q = future.result()
    else:
        new_q = asyncio.Queue()

    # Now acquire the lock again to safely store it
    with _queues_lock:
        if session_id not in _event_queues:
            _event_queues[session_id] = new_q
        return _event_queues[session_id]


async def _create_queue() -> asyncio.Queue:
    return asyncio.Queue()


def push_event(session_id: str, event: dict) -> None:
    q = get_queue(session_id)
    if _main_loop and not _main_loop.is_closed():
        _main_loop.call_soon_threadsafe(q.put_nowait, event)
    else:
        q.put_nowait(event)


def set_job(job_id: str, status: str, error: Optional[str] = None) -> None:
    with _jobs_lock:
        _jobs[job_id] = {"status": status, "error": error}


def get_job(job_id: str) -> Optional[dict]:
    with _jobs_lock:
        return _jobs.get(job_id)
