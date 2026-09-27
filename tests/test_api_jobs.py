"""
Tests for src/api/executor.py (PhaseExecutor lifecycle) and the
GET /api/jobs/{job_id}/status endpoint in src/api/routers/phases.py.

All AuditFlow / crew code is irrelevant here: the executor just runs plain
callables on a bounded thread pool, so tests submit trivial functions instead
of real phase runners.
"""

import os
import subprocess
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import api.executor as executor_mod
from api.executor import (
    PhaseExecutor,
    get_executor,
    init_executor,
    shutdown_executor,
)
from api.job_store import set_job

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture(autouse=True)
def _reset_global_executor():
    """Each test gets a clean module-level singleton, and never leaks a pool."""
    executor_mod._executor = None
    yield
    if executor_mod._executor is not None:
        executor_mod._executor.shutdown(wait=False)
    executor_mod._executor = None


# ---------------------------------------------------------------------------
# PhaseExecutor: submit / exceptions / max_workers / shutdown
# ---------------------------------------------------------------------------


class TestPhaseExecutorSubmit:
    def test_submit_runs_the_job(self):
        ex = PhaseExecutor(max_workers=2)
        try:
            future = ex.submit("sess-1", lambda a, b: a + b, 2, 3)
            assert future.result(timeout=2) == 5
        finally:
            ex.shutdown(wait=True)

    def test_submit_passes_session_id_positional_args_through(self):
        ex = PhaseExecutor(max_workers=2)
        seen = {}

        def _job(x, y):
            seen["x"] = x
            seen["y"] = y
            return x, y

        try:
            future = ex.submit("sess-2", _job, "a", "b")
            assert future.result(timeout=2) == ("a", "b")
            assert seen == {"x": "a", "y": "b"}
        finally:
            ex.shutdown(wait=True)


class TestPhaseExecutorExceptions:
    def test_exception_in_job_is_captured_not_raised_by_submit(self):
        """submit() itself must not raise even if the job will fail."""
        ex = PhaseExecutor(max_workers=2)
        try:
            future = ex.submit("sess-err", self._boom)
            with pytest.raises(RuntimeError, match="phase blew up"):
                future.result(timeout=2)
        finally:
            ex.shutdown(wait=True)

    @staticmethod
    def _boom():
        raise RuntimeError("phase blew up")

    def test_worker_pool_survives_a_failed_job(self):
        """A job raising must not crash the pool: later submissions still run."""
        ex = PhaseExecutor(max_workers=1)
        try:
            failing = ex.submit("sess-err", self._boom)
            with pytest.raises(RuntimeError):
                failing.result(timeout=2)

            ok = ex.submit("sess-ok", lambda: 42)
            assert ok.result(timeout=2) == 42
        finally:
            ex.shutdown(wait=True)

    def test_report_as_job_error_pattern(self):
        """The pattern routers use: catch in the job body, record via job_store."""

        def _job(job_id):
            try:
                raise ValueError("bad phase input")
            except Exception as exc:  # mirrors sessions.py's _run_phase_N wrapping
                set_job(job_id, "failed", str(exc))

        ex = PhaseExecutor(max_workers=1)
        try:
            future = ex.submit("sess-err2", _job, "job-err-1")
            future.result(timeout=2)
        finally:
            ex.shutdown(wait=True)

        from api.job_store import get_job

        job = get_job("job-err-1")
        assert job == {"status": "failed", "error": "bad phase input"}


class TestPhaseExecutorMaxWorkers:
    def test_max_workers_bounds_concurrency(self):
        """With max_workers=2, a 3rd blocking job must not start until one finishes."""
        ex = PhaseExecutor(max_workers=2)
        started = threading.Semaphore(0)
        release = threading.Event()
        running_count = {"n": 0}
        lock = threading.Lock()

        def _blocker():
            with lock:
                running_count["n"] += 1
            started.release()
            release.wait(timeout=2)
            with lock:
                running_count["n"] -= 1

        try:
            futures = [ex.submit(f"sess-{i}", _blocker) for i in range(3)]
            # Exactly 2 should be able to start (the pool is bounded to 2).
            assert started.acquire(timeout=2)
            assert started.acquire(timeout=2)
            time.sleep(0.1)
            with lock:
                assert running_count["n"] == 2
            release.set()
            for f in futures:
                f.result(timeout=2)
        finally:
            ex.shutdown(wait=True)

    def test_default_max_workers_from_env(self):
        """_MAX_WORKERS is computed once at import time from the env var, so
        this checks it in a fresh subprocess rather than reload()ing the
        already-imported module (which would leave a second PhaseExecutor
        class object floating around and break identity checks elsewhere)."""
        src_dir = os.path.join(os.path.dirname(__file__), "..", "src")
        env = {**os.environ, "PYTHONPATH": src_dir, "PHASE_EXECUTOR_MAX_WORKERS": "3"}
        proc = subprocess.run(
            [sys.executable, "-c", "import api.executor as e; print(e._MAX_WORKERS)"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "3"


class TestPhaseExecutorShutdown:
    def test_submit_after_shutdown_raises(self):
        ex = PhaseExecutor(max_workers=1)
        ex.shutdown(wait=True)
        with pytest.raises(RuntimeError):
            ex.submit("sess-late", lambda: 1)

    def test_shutdown_is_idempotent_via_module_helper(self):
        init_executor()
        shutdown_executor()
        # Calling shutdown again with no executor set must not raise.
        shutdown_executor()


class TestModuleLevelExecutorLifecycle:
    def test_get_executor_lazily_creates_singleton(self):
        first = get_executor()
        second = get_executor()
        assert first is second
        assert isinstance(first, PhaseExecutor)

    def test_init_executor_replaces_singleton(self):
        first = init_executor()
        second = init_executor()
        assert first is not second
        assert get_executor() is second

    def test_shutdown_executor_clears_singleton(self):
        init_executor()
        shutdown_executor()
        assert executor_mod._executor is None
        # get_executor() must still work afterwards (lazily recreates one).
        fresh = get_executor()
        assert isinstance(fresh, PhaseExecutor)


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/status
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("API_AUTH_TOKEN", "test-token")
    from api.main import app

    return TestClient(app)


class TestJobStatusEndpoint:
    def test_requires_auth(self, client):
        resp = client.get("/api/jobs/some-job/status")
        assert resp.status_code == 401

    def test_unknown_job_is_404(self, client):
        resp = client.get("/api/jobs/does-not-exist/status", headers=AUTH)
        assert resp.status_code == 404

    def test_running_job_state(self, client):
        set_job("job-running-1", "running")
        resp = client.get("/api/jobs/job-running-1/status", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {"status": "running", "error": None}

    def test_completed_job_state(self, client):
        set_job("job-done-1", "completed")
        resp = client.get("/api/jobs/job-done-1/status", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {"status": "completed", "error": None}

    def test_error_job_state_includes_error_message(self, client):
        set_job("job-fail-1", "failed", "phase crew raised ValueError")
        resp = client.get("/api/jobs/job-fail-1/status", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "failed",
            "error": "phase crew raised ValueError",
        }
