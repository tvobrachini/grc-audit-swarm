import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

logger = logging.getLogger(__name__)

_MAX_WORKERS = int(os.environ.get("PHASE_EXECUTOR_MAX_WORKERS", "10"))


class PhaseExecutor:
    """Bounded thread pool for audit phase execution."""

    def __init__(self, max_workers: int = _MAX_WORKERS) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="phase")

    def submit(self, session_id: str, fn: Callable, *args) -> Future:
        logger.debug("Submitting phase task for session %s", session_id)
        return self._pool.submit(fn, *args)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)


_executor: PhaseExecutor | None = None


def get_executor() -> PhaseExecutor:
    global _executor
    if _executor is None:
        _executor = PhaseExecutor()
    return _executor


def init_executor() -> PhaseExecutor:
    global _executor
    _executor = PhaseExecutor()
    return _executor


def shutdown_executor() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False)
        _executor = None
