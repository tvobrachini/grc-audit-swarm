import hashlib
import json
import re
import uuid
import os
import datetime
import base64
import functools
from dataclasses import dataclass, field
from typing import List

# Configurable via env var for Docker volume mounts.
_DEFAULT_EVIDENCE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "evidence_vault"
)
EVIDENCE_DIR = os.environ.get("EVIDENCE_VAULT_PATH", _DEFAULT_EVIDENCE_DIR)

# Matches 12-digit AWS account IDs (standalone — not part of longer numbers).
_ACCOUNT_ID_RE = re.compile(r"(?<!\d)\d{12}(?!\d)")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@functools.lru_cache(maxsize=1)
def _get_fernet():
    """Return a Fernet instance if VAULT_ENCRYPTION_KEY is set, else None."""
    key_b64 = os.environ.get("VAULT_ENCRYPTION_KEY")
    if not key_b64:
        return None
    try:
        from cryptography.fernet import Fernet

        key_bytes = base64.urlsafe_b64decode(key_b64.encode())
        if len(key_bytes) != 32:
            raise ValueError("VAULT_ENCRYPTION_KEY must be 32 bytes (base64-encoded).")
        fernet_key = base64.urlsafe_b64encode(key_bytes)
        return Fernet(fernet_key)
    except ImportError:
        raise RuntimeError(
            "cryptography package is required for vault encryption. "
            "Install it with: pip install cryptography"
        )


def _redact_account_ids(text: str) -> str:
    """Replace 12-digit AWS account IDs with [REDACTED] before storing."""
    return _ACCOUNT_ID_RE.sub("[REDACTED]", text)


@dataclass
class ControlEvidence:
    """Raw evidence collected for a single control, keyed by control_id."""

    control_id: str
    vault_ids: List[str] = field(default_factory=list)
    raw_payloads: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)


def serialize_control_evidence(evidence: ControlEvidence) -> str:
    """Produce a text summary of all evidence for a control, for LLM context."""
    parts = []
    for i, payload in enumerate(evidence.raw_payloads):
        source = evidence.sources[i] if i < len(evidence.sources) else "unknown"
        vault_id = evidence.vault_ids[i] if i < len(evidence.vault_ids) else "n/a"
        parts.append(f"[Source: {source} | Vault: {vault_id}]\n{payload}")
    return (
        "\n\n".join(parts)
        if parts
        else f"No evidence collected for {evidence.control_id}."
    )


class EvidenceAssuranceProtocol:
    """Implements PCAOB AS 1215 and IIA 2330 compliance by enforcing immutable hashing of payloads."""

    @staticmethod
    def _evidence_dir() -> str:
        return os.environ.get("EVIDENCE_VAULT_PATH", _DEFAULT_EVIDENCE_DIR)

    @staticmethod
    def register_evidence(raw_payload: str, source_mcp_operation: str) -> dict:
        """
        Receives raw payload from an MCP, scrubs AWS account IDs, calculates SHA-256
        hash, stores it immutably, and returns the Vault-ID/Hash to the agent so it
        cannot hallucinate the evidence.
        """
        evidence_dir = EvidenceAssuranceProtocol._evidence_dir()
        os.makedirs(evidence_dir, exist_ok=True)

        # Redact 12-digit AWS account IDs before they leave the environment.
        sanitized_payload = _redact_account_ids(raw_payload)

        vault_id = str(uuid.uuid4())
        sha256_hash = hashlib.sha256(sanitized_payload.encode("utf-8")).hexdigest()

        fernet = _get_fernet()
        encrypted = fernet is not None
        stored_payload = (
            fernet.encrypt(sanitized_payload.encode("utf-8")).decode("ascii")
            if encrypted
            else sanitized_payload
        )

        evidence_record = {
            "vault_id": vault_id,
            "sha256": sha256_hash,
            "mcp_source": source_mcp_operation,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "raw_payload": stored_payload,
            "encrypted": encrypted,
        }

        filepath = os.path.join(evidence_dir, f"{vault_id}.json")
        with open(filepath, "w") as f:
            json.dump(evidence_record, f, indent=2)

        return {"vault_id": vault_id, "sha256": sha256_hash}

    @staticmethod
    def verify_exact_quote(vault_id: str, exact_quote_claim: str) -> bool:
        """
        Deterministic Anti-Hallucination check.
        Returns True ONLY if the exact_quote mathematically exists within the hashed raw payload.
        """
        if not _UUID_RE.fullmatch(vault_id):
            return False

        evidence_dir = EvidenceAssuranceProtocol._evidence_dir()
        filepath = os.path.join(evidence_dir, f"{vault_id}.json")

        resolved = os.path.realpath(filepath)
        expected_dir = os.path.realpath(evidence_dir)
        if os.path.commonpath([expected_dir, resolved]) != expected_dir:
            return False

        if not os.path.exists(filepath):
            return False

        try:
            with open(filepath, "r") as f:
                evidence_record = json.load(f)

            payload = evidence_record["raw_payload"]
            if evidence_record.get("encrypted"):
                fernet = _get_fernet()
                if fernet is None:
                    return False
                payload = fernet.decrypt(payload.encode("ascii")).decode("utf-8")

            return exact_quote_claim in payload
        except (KeyError, json.JSONDecodeError, OSError, Exception):
            return False
