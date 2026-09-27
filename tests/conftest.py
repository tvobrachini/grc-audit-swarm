"""Shared test configuration."""

import os
import sys

import pytest


@pytest.fixture(autouse=True)
def _isolated_evidence_vault(tmp_path, monkeypatch):
    """Keep every test's evidence vault in a temp dir, never the repo's.

    DEMO_MODE registers its synthetic evidence in the vault, so API and demo
    tests would otherwise write into evidence_vault/ at the repo root. Tests
    that need a specific path still override this with their own setenv.
    """
    monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path / "evidence_vault"))


@pytest.fixture(autouse=True)
def _reviewer_tokens_off(monkeypatch):
    """Per-reviewer tokens (ADR-012) are off unless a test turns them on, and
    no test inherits another's failed-attempt count or cached tokens file."""
    monkeypatch.delenv("REVIEWER_TOKENS_FILE", raising=False)
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from api import reviewer_tokens

    reviewer_tokens.limiter.reset()
    reviewer_tokens.clear_cache()
    yield
    reviewer_tokens.limiter.reset()
    reviewer_tokens.clear_cache()
