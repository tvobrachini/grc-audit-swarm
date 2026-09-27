"""
Generation provenance (see DECISIONS.md, ADR-010): per-phase generation
metadata, trail entries tied to the run they reviewed, and evidence
collection metadata. No secret (API key, base URL with credentials, token)
may ever appear in any of this.
"""

import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm import session_manager
from swarm.audit_flow import AuditFlow, prompt_fingerprint
from swarm.evidence import EvidenceAssuranceProtocol
from swarm.llm_factory import describe_crew_llm, describe_qa_llm, LLMConfigurationError
from swarm.state.repository import FlowRepository

# Env vars that could hold a secret; none of them may ever appear as a value
# inside recorded provenance (only provider/model *names* are safe).
_SECRET_ENV_VALUES = {
    "sk-demo-super-secret-openai-key",
    "gsk_demo_super_secret_groq_key",
    "nvapi-demo-super-secret-key",
    "AIzaDemoSuperSecretGeminiKey",
}


@pytest.fixture
def no_real_crews():
    boom = AssertionError("a real crew was built in DEMO_MODE")
    with (
        patch("swarm.audit_flow.PlanningCrew", side_effect=boom),
        patch("swarm.audit_flow.FieldworkCrew", side_effect=boom),
        patch("swarm.audit_flow.ReportingCrew", side_effect=boom),
    ):
        yield


@pytest.fixture
def demo_env(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DEMO_STEP_DELAY", "0")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEMO_QA_REJECT_PHASE", raising=False)


def _no_secrets_anywhere(value) -> None:
    text = json.dumps(value, default=str)
    for secret in _SECRET_ENV_VALUES:
        assert secret not in text
    assert "api_key" not in text.lower()
    assert "base_url" not in text.lower()
    assert "Bearer " not in text


# ── prompt_fingerprint ───────────────────────────────────────────────────


class TestPromptFingerprint:
    def test_deterministic_for_same_phase_and_skills(self):
        assert prompt_fingerprint(1, []) == prompt_fingerprint(1, [])

    def test_differs_by_phase(self):
        assert prompt_fingerprint(1, []) != prompt_fingerprint(2, [])
        assert prompt_fingerprint(2, []) != prompt_fingerprint(3, [])

    def test_differs_when_a_skill_is_active(self):
        skill = [{"id": "s1", "name": "Skill One", "specialist_system_prompt": "Do X."}]
        assert prompt_fingerprint(1, []) != prompt_fingerprint(1, skill)

    def test_differs_between_different_skill_sets(self):
        skill_a = [{"id": "a", "name": "A", "specialist_system_prompt": "Do X."}]
        skill_b = [{"id": "b", "name": "B", "specialist_system_prompt": "Do Y."}]
        assert prompt_fingerprint(1, skill_a) != prompt_fingerprint(1, skill_b)

    def test_is_a_sha256_hex_digest(self):
        fp = prompt_fingerprint(1, [])
        assert len(fp) == 64
        int(fp, 16)  # raises if not hex


# ── llm_factory describe_* ───────────────────────────────────────────────


class TestDescribeCrewLLM:
    def test_no_provider_configured_raises(self, monkeypatch):
        for var in (
            "OLLAMA_MODEL",
            "NVIDIA_API_KEY",
            "GEMINI_API_KEY",
            "OPENAI_API_KEY",
            "GROQ_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(LLMConfigurationError):
            describe_crew_llm()

    def test_ollama_takes_priority(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-demo-super-secret-openai-key")
        info = describe_crew_llm()
        assert info == {"provider": "ollama", "model": "qwen2.5"}
        _no_secrets_anywhere(info)

    def test_openai_selected_when_only_that_key_is_set(self, monkeypatch):
        for var in ("OLLAMA_MODEL", "NVIDIA_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-demo-super-secret-openai-key")
        info = describe_crew_llm()
        assert info == {"provider": "openai", "model": "gpt-4o-mini"}
        _no_secrets_anywhere(info)

    def test_gemini_model_override_is_reflected(self, monkeypatch):
        for var in ("OLLAMA_MODEL", "NVIDIA_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "AIzaDemoSuperSecretGeminiKey")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-custom")
        info = describe_crew_llm()
        assert info == {"provider": "gemini", "model": "gemini-custom"}
        _no_secrets_anywhere(info)


class TestDescribeQaLLM:
    def test_without_qa_model_mirrors_crew_llm(self, monkeypatch):
        monkeypatch.delenv("QA_LLM_MODEL", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-demo-super-secret-openai-key")
        for var in ("OLLAMA_MODEL", "NVIDIA_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        assert describe_qa_llm() == describe_crew_llm()

    def test_qa_model_with_provider_prefix(self, monkeypatch):
        monkeypatch.setenv("QA_LLM_MODEL", "openai/gpt-4o")
        monkeypatch.setenv("QA_LLM_API_KEY", "sk-demo-super-secret-openai-key")
        fake_url = "https://user:pw@example.com/v1"  # pragma: allowlist secret
        monkeypatch.setenv("QA_LLM_BASE_URL", fake_url)
        info = describe_qa_llm()
        assert info == {"provider": "openai", "model": "openai/gpt-4o"}
        _no_secrets_anywhere(info)

    def test_qa_model_without_prefix_is_custom(self, monkeypatch):
        monkeypatch.setenv("QA_LLM_MODEL", "some-bare-model-name")
        info = describe_qa_llm()
        assert info == {"provider": "custom", "model": "some-bare-model-name"}


# ── AuditFlow generation_runs (DEMO_MODE) ───────────────────────────────


class TestGenerationRunsDemoMode:
    def test_demo_mode_records_demo_provider(self, demo_env, no_real_crews):
        flow = AuditFlow()
        flow.state.theme = "S3 exposure"
        flow.generate_planning()
        assert flow.state.status == "WAITING_HUMAN_GATE_1"
        runs = flow.state.generation_runs
        assert len(runs) == 1
        run = runs[0]
        assert run.phase == 1
        assert run.provider == "demo"
        assert run.model == "fixed-content"
        assert run.qa_provider == "demo"
        assert run.qa_model == "fixed-content"
        assert run.demo_mode is True
        assert run.outcome == "qa_approved"
        assert run.attempts == 1
        assert run.started_at and run.ended_at
        assert len(run.prompt_fingerprint) == 64
        assert run.crewai_version and run.crewai_version != "unknown"
        assert run.app_version and run.app_version != "unknown"  # from pyproject.toml

    def test_one_run_recorded_per_phase_through_full_audit(
        self, demo_env, no_real_crews
    ):
        flow = AuditFlow()
        flow.generate_planning()
        flow.begin_phase_2("alice")
        flow.generate_fieldwork()
        flow.begin_phase_3("alice")
        flow.generate_reporting()
        flow.finalize_audit("bob")
        phases = [r.phase for r in flow.state.generation_runs]
        assert phases == [1, 2, 3]

    def test_retry_appends_a_new_run_not_replacing_the_old_one(
        self, demo_env, no_real_crews, monkeypatch
    ):
        monkeypatch.setenv("DEMO_QA_REJECT_PHASE", "1")
        flow = AuditFlow()
        flow.generate_planning()
        assert flow.state.status == "QA_REJECTED_PHASE_1"
        first_run_id = flow.state.generation_runs[0].run_id
        assert flow.state.generation_runs[0].outcome == "qa_rejected"
        flow.retry_phase(1, "sup")
        flow.generate_planning()
        assert flow.state.status == "WAITING_HUMAN_GATE_1"
        assert len(flow.state.generation_runs) == 2
        assert flow.state.generation_runs[0].run_id == first_run_id
        assert flow.state.generation_runs[1].run_id != first_run_id
        assert flow.state.generation_runs[1].outcome == "qa_approved"

    def test_no_secrets_in_generation_runs(self, demo_env, no_real_crews, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-demo-super-secret-openai-key")
        flow = AuditFlow()
        flow.generate_planning()
        _no_secrets_anywhere([r.model_dump() for r in flow.state.generation_runs])


# ── Trail entries reference the generation run they reviewed ────────────


class TestTrailProvenanceLinkage:
    def test_gate_approval_records_the_generation_run(self, demo_env, no_real_crews):
        flow = AuditFlow()
        flow.generate_planning()
        run = flow.state.generation_runs[0]
        flow.begin_phase_2("alice")
        entry = flow.state.approval_trail[-1]
        assert entry["gate"] == "Gate 1 (Planning)"
        assert entry["generation_run_id"] == run.run_id
        assert entry["generation_prompt_fingerprint"] == run.prompt_fingerprint
        assert entry["generation_model"] == f"{run.provider}/{run.model}"

    def test_retry_records_the_rejected_generation_run(
        self, demo_env, no_real_crews, monkeypatch
    ):
        monkeypatch.setenv("DEMO_QA_REJECT_PHASE", "1")
        flow = AuditFlow()
        flow.generate_planning()
        rejected_run = flow.state.generation_runs[0]
        flow.retry_phase(1, "sup")
        entry = flow.state.approval_trail[-1]
        assert entry["action"] == "retry"
        assert entry["generation_run_id"] == rejected_run.run_id

    def test_qa_override_records_the_rejected_generation_run(
        self, demo_env, no_real_crews, monkeypatch
    ):
        monkeypatch.setenv("DEMO_QA_REJECT_PHASE", "1")
        flow = AuditFlow()
        flow.generate_planning()
        rejected_run = flow.state.generation_runs[0]
        flow.override_qa_rejection(1, "sup", "Reviewed manually")
        entry = flow.state.approval_trail[-1]
        assert entry["action"] == "qa_override"
        assert entry["generation_run_id"] == rejected_run.run_id

    def test_return_for_rework_records_the_generation_run(
        self, demo_env, no_real_crews
    ):
        flow = AuditFlow()
        flow.generate_planning()
        run = flow.state.generation_runs[0]
        assert flow.state.status == "WAITING_HUMAN_GATE_1"
        flow.return_for_rework(1, "carol", "Please add more risks.")
        entry = flow.state.approval_trail[-1]
        assert entry["action"] == "return_for_rework"
        assert entry["generation_run_id"] == run.run_id

    def test_no_run_yet_does_not_break_stamping(self, demo_env, no_real_crews):
        # A legacy trail / a phase never run: gate approval still works, just
        # without provenance fields (they are simply absent).
        flow = AuditFlow()
        flow.state.racm_plan = None
        flow.record_preparer  # sanity: attribute exists, not called here
        # Directly stamp a retry-like path with no generation run recorded.
        assert flow._run_provenance_extra(99) == {}

    def test_trail_still_hash_chains_and_verifies(self, demo_env, no_real_crews):
        flow = AuditFlow()
        flow.generate_planning()
        flow.begin_phase_2("alice")
        result = flow.verify_trail()
        assert result["ok"] is True

    def test_tampering_with_generation_fields_breaks_verification(
        self, demo_env, no_real_crews
    ):
        flow = AuditFlow()
        flow.generate_planning()
        flow.begin_phase_2("alice")
        flow.state.approval_trail[-1]["generation_model"] = "openai/gpt-4o-tampered"
        result = flow.verify_trail()
        assert result["ok"] is False
        assert result["status"] == "broken"


# ── Persistence round trip ──────────────────────────────────────────────


class TestGenerationRunsPersistence:
    def test_round_trips_through_flow_repository(
        self, demo_env, no_real_crews, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            session_manager, "SESSIONS_PATH", str(tmp_path / "audit_sessions.json")
        )
        flow = AuditFlow()
        flow.record_preparer("alice")
        flow.begin_phase_1()
        flow.generate_planning()

        session_manager.save_session(
            "sess-1", "Test audit", "scope text", status=flow.state.status
        )
        repo = FlowRepository()
        assert repo.save("sess-1", flow) is True

        loaded = repo.load("sess-1")
        assert loaded is not None
        assert loaded.is_clean
        runs = loaded.flow.state.generation_runs
        assert len(runs) == 1
        assert runs[0].run_id == flow.state.generation_runs[0].run_id
        assert runs[0].provider == "demo"
        assert (
            runs[0].prompt_fingerprint
            == flow.state.generation_runs[0].prompt_fingerprint
        )


# ── Evidence collection metadata ─────────────────────────────────────────


class TestEvidenceMetadata:
    def test_metadata_is_stored_and_sanitized(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence(
            "some evidence payload here",
            "aws.iam.get_account_password_policy",
            metadata={
                "tool": "get_iam_password_policy",
                "operation": "iam:GetAccountPasswordPolicy",
                "region": "us-east-1",
                "app_version": "0.1.0",
                "caller_identity": "arn:aws:iam::123456789012:user/auditor",
            },
        )
        record = json.loads((tmp_path / f"{result['vault_id']}.json").read_text())
        assert record["metadata"]["region"] == "us-east-1"
        assert "123456789012" not in json.dumps(record)
        assert "[REDACTED]" in record["metadata"]["caller_identity"]

    def test_quote_still_verifies_with_metadata_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence(
            "MinimumPasswordLength=14 is configured",
            "op",
            metadata={"tool": "t", "operation": "op", "region": "us-east-1"},
        )
        assert EvidenceAssuranceProtocol.verify_exact_quote(
            result["vault_id"], "MinimumPasswordLength=14"
        )

    def test_tampering_with_metadata_breaks_verification(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence(
            "MinimumPasswordLength=14 is configured",
            "op",
            metadata={"tool": "t", "operation": "op", "region": "us-east-1"},
        )
        vault_id = result["vault_id"]
        filepath = tmp_path / f"{vault_id}.json"
        record = json.loads(filepath.read_text())
        record["metadata"]["region"] = "eu-west-1"  # tampered after the fact
        filepath.write_text(json.dumps(record))
        assert not EvidenceAssuranceProtocol.verify_exact_quote(
            vault_id, "MinimumPasswordLength=14"
        )

    def test_no_metadata_is_backward_compatible(self, tmp_path, monkeypatch):
        # Records written before metadata existed have none: digest covers
        # the payload alone, exactly as before.
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        result = EvidenceAssuranceProtocol.register_evidence(
            "legacy payload with no metadata", "op"
        )
        assert EvidenceAssuranceProtocol.verify_exact_quote(
            result["vault_id"], "legacy payload"
        )
        record = json.loads((tmp_path / f"{result['vault_id']}.json").read_text())
        assert "metadata" not in record

    def test_migration_preserves_metadata_and_still_verifies(
        self, tmp_path, monkeypatch
    ):
        import base64

        key = base64.urlsafe_b64encode(b"k" * 32).decode()
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        monkeypatch.delenv("VAULT_ENCRYPTION_KEY", raising=False)
        # Simulate a legacy encrypted record with metadata but a plain
        # sha256 (pre-HMAC), then migrate it.
        monkeypatch.setenv("VAULT_ENCRYPTION_KEY", key)
        from swarm.evidence import _get_fernet, _digest_input
        import hashlib as _hashlib

        payload = "legacy encrypted payload"
        metadata = {"tool": "t", "region": "us-east-1"}
        fernet = _get_fernet()
        digest_input = _digest_input(payload, metadata)
        record = {
            "vault_id": "12345678-1234-1234-1234-123456789012",
            "sha256": _hashlib.sha256(digest_input.encode("utf-8")).hexdigest(),
            "mcp_source": "op",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "raw_payload": fernet.encrypt(payload.encode("utf-8")).decode("ascii"),
            "encrypted": True,
            "metadata": metadata,
        }
        filepath = tmp_path / f"{record['vault_id']}.json"
        filepath.write_text(json.dumps(record))

        counts = EvidenceAssuranceProtocol.migrate_legacy_digests()
        assert counts["migrated"] == 1
        assert EvidenceAssuranceProtocol.verify_exact_quote(
            record["vault_id"], "legacy encrypted payload"
        )
        migrated = json.loads(filepath.read_text())
        assert "sha256" not in migrated
        assert "hmac_sha256" in migrated
        assert migrated["metadata"] == metadata
