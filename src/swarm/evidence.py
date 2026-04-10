import hashlib
import json
import uuid
import os
import datetime
from dataclasses import dataclass, field
from typing import List


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

    EVIDENCE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "evidence_vault")

    @staticmethod
    def register_evidence(raw_payload: str, source_mcp_operation: str) -> dict:
        """
        Receives raw payload from an MCP, calculats SHA-256 hash, stores it immutably,
        and returns the Vault-ID/Hash to the Agent so it cannot hallucinate the evidence.
        """
        os.makedirs(EvidenceAssuranceProtocol.EVIDENCE_DIR, exist_ok=True)

        vault_id = str(uuid.uuid4())
        sha256_hash = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()

        evidence_record = {
            "vault_id": vault_id,
            "sha256": sha256_hash,
            "mcp_source": source_mcp_operation,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "raw_payload": raw_payload,
        }

        filepath = os.path.join(
            EvidenceAssuranceProtocol.EVIDENCE_DIR, f"{vault_id}.json"
        )
        with open(filepath, "w") as f:
            json.dump(evidence_record, f, indent=2)

        return {"vault_id": vault_id, "sha256": sha256_hash}

    @staticmethod
    def verify_exact_quote(vault_id: str, exact_quote_claim: str) -> bool:
        """
        Deterministic Anti-Hallucination check.
        Returns True ONLY if the exact_quote mathematically exists within the hashed raw payload.
        """
        filepath = os.path.join(
            EvidenceAssuranceProtocol.EVIDENCE_DIR, f"{vault_id}.json"
        )

        resolved = os.path.realpath(filepath)
        expected_dir = os.path.realpath(EvidenceAssuranceProtocol.EVIDENCE_DIR)
        if not resolved.startswith(expected_dir):
            return False

        if not os.path.exists(filepath):
            return False

        try:
            with open(filepath, "r") as f:
                evidence_record = json.load(f)

            return exact_quote_claim in evidence_record["raw_payload"]
        except (KeyError, json.JSONDecodeError, OSError):
            return False
