"""Shared test configuration."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_evidence_vault(tmp_path, monkeypatch):
    """Keep every test's evidence vault in a temp dir, never the repo's.

    DEMO_MODE registers its synthetic evidence in the vault, so API and demo
    tests would otherwise write into evidence_vault/ at the repo root. Tests
    that need a specific path still override this with their own setenv.
    """
    monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path / "evidence_vault"))
