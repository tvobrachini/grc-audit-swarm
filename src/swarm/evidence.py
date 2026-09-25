import hashlib
import hmac
import json
import logging
import re
import uuid
import os
import datetime
import base64
from functools import lru_cache
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from cryptography.fernet import InvalidToken
else:
    try:
        from cryptography.fernet import InvalidToken
    except ImportError:  # cryptography is an optional dependency (encryption is opt-in)

        class InvalidToken(Exception):
            pass


# Configurable via env var for Docker volume mounts.
_DEFAULT_EVIDENCE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "evidence_vault"
)
EVIDENCE_DIR = os.environ.get("EVIDENCE_VAULT_PATH", _DEFAULT_EVIDENCE_DIR)

# Matches 12-digit AWS account IDs (standalone — not part of longer numbers),
# in either bare form (123456789012) or the hyphenated 4-4-4 form some AWS
# consoles/CLIs display (1234-5678-9012).
_ACCOUNT_ID_RE = re.compile(r"(?<!\d)\d{12}(?!\d)|(?<!\d)\d{4}-\d{4}-\d{4}(?!\d)")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# A quote shorter than this is too weak an anti-hallucination check — it would
# match almost any payload by chance (e.g. a lone word or punctuation).
_MIN_QUOTE_LENGTH = 8


def _get_fernet():
    """Return a Fernet instance if VAULT_ENCRYPTION_KEY is set, else None."""
    key_b64 = os.environ.get("VAULT_ENCRYPTION_KEY")
    if not key_b64:
        return None
    return _build_fernet(key_b64)


@lru_cache(maxsize=4)
def _build_fernet(key_b64: str):
    """Build (and cache per key value) a Fernet instance for the given base64 key."""
    try:
        from cryptography.fernet import Fernet

        key_bytes = base64.urlsafe_b64decode(key_b64.encode())
        if len(key_bytes) != 32:
            raise ValueError("VAULT_ENCRYPTION_KEY must be 32 bytes (base64-encoded).")
        return Fernet(key_b64.encode())
    except ImportError:
        raise RuntimeError(
            "cryptography package is required for vault encryption. "
            "Install it with: pip install cryptography"
        )


# Domain-separation label for deriving the HMAC key from VAULT_ENCRYPTION_KEY,
# so the Fernet key itself is never reused directly for a second purpose.
_HMAC_KEY_LABEL = b"grc-audit-swarm/evidence-vault/hmac-sha256/v1"


def _derive_hmac_key(key_b64: str) -> bytes:
    """Derive the vault's HMAC key from the base64 VAULT_ENCRYPTION_KEY."""
    raw_key = base64.urlsafe_b64decode(key_b64.encode())
    return hmac.new(raw_key, _HMAC_KEY_LABEL, hashlib.sha256).digest()


def _keyed_digest(payload: str, key_b64: str) -> str:
    """HMAC-SHA256 of the payload, keyed from VAULT_ENCRYPTION_KEY.

    Used instead of a bare SHA-256 when the vault is encrypted: evidence
    payloads are small and guessable (a password policy, a user's MFA flag),
    so a plain hash stored next to the ciphertext would let anyone holding the
    file confirm a guessed payload without the key.
    """
    return hmac.new(
        _derive_hmac_key(key_b64), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _redact_account_ids(text: str) -> str:
    """Replace 12-digit AWS account IDs with [REDACTED] before storing."""
    return _ACCOUNT_ID_RE.sub("[REDACTED]", text)


class EvidenceAssuranceProtocol:
    """Integrity digest (SHA-256, or HMAC-SHA256 when encrypted) plus exact-quote check for collected audit evidence."""

    @staticmethod
    def _evidence_dir() -> str:
        return os.environ.get("EVIDENCE_VAULT_PATH", _DEFAULT_EVIDENCE_DIR)

    @staticmethod
    def register_evidence(raw_payload: str, source_mcp_operation: str) -> dict:
        """
        Receives raw payload from an MCP, scrubs AWS account IDs, computes an
        integrity digest (SHA-256, or HMAC-SHA256 keyed from VAULT_ENCRYPTION_KEY
        when the vault is encrypted), stores it on disk, and returns the
        Vault-ID and digest.
        """
        evidence_dir = EvidenceAssuranceProtocol._evidence_dir()
        os.makedirs(evidence_dir, exist_ok=True)

        # Redact 12-digit AWS account IDs before they leave the environment.
        sanitized_payload = _redact_account_ids(raw_payload)

        vault_id = str(uuid.uuid4())
        fernet = _get_fernet()
        encrypted = fernet is not None
        if fernet is not None:
            # Keyed digest: a bare SHA-256 beside the ciphertext would leak
            # guessable payloads (see _keyed_digest).
            digest_field = "hmac_sha256"
            digest = _keyed_digest(
                sanitized_payload, os.environ["VAULT_ENCRYPTION_KEY"]
            )
            stored_payload = fernet.encrypt(sanitized_payload.encode("utf-8")).decode(
                "ascii"
            )
        else:
            # Plaintext is stored alongside, so the hash reveals nothing extra;
            # it detects accidental corruption only.
            digest_field = "sha256"
            digest = hashlib.sha256(sanitized_payload.encode("utf-8")).hexdigest()
            stored_payload = sanitized_payload

        evidence_record = {
            "vault_id": vault_id,
            digest_field: digest,
            "mcp_source": source_mcp_operation,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "raw_payload": stored_payload,
            "encrypted": encrypted,
        }

        filepath = os.path.join(evidence_dir, f"{vault_id}.json")
        with open(filepath, "w") as f:
            json.dump(evidence_record, f, indent=2)

        return {"vault_id": vault_id, digest_field: digest}

    @staticmethod
    def verify_exact_quote(vault_id: str, exact_quote_claim: str) -> bool:
        """
        Deterministic Anti-Hallucination check.
        Returns True ONLY if the exact_quote mathematically exists within the hashed raw payload.
        """
        if not _UUID_RE.fullmatch(vault_id):
            return False

        if not exact_quote_claim or not exact_quote_claim.strip():
            return False
        if len(exact_quote_claim) < _MIN_QUOTE_LENGTH:
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

            if "hmac_sha256" in evidence_record:
                key_b64 = os.environ.get("VAULT_ENCRYPTION_KEY")
                if not key_b64:
                    return False
                expected = _keyed_digest(payload, key_b64)
                stored = evidence_record["hmac_sha256"]
            else:
                # Unencrypted records, and encrypted records written before
                # the keyed digest was introduced.
                expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                stored = evidence_record["sha256"]
            if not hmac.compare_digest(expected, str(stored)):
                return False

            return exact_quote_claim in payload
        except (
            KeyError,
            json.JSONDecodeError,
            OSError,
            ValueError,
            InvalidToken,
            RuntimeError,
        ) as exc:
            logger.warning(
                "verify_exact_quote failed for vault_id=%s: %s", vault_id, exc
            )
            return False

    @staticmethod
    def migrate_legacy_digests() -> dict:
        """Replace the plain SHA-256 on encrypted records with the keyed HMAC.

        Encrypted records written before the keyed digest existed still carry
        an unkeyed SHA-256 next to the ciphertext, which lets anyone holding
        the file confirm a guessed payload. This rewrites each such record
        with an HMAC-SHA256 and drops the plain hash. A record is only
        migrated if its payload decrypts and still matches its stored SHA-256,
        so corrupted evidence is never re-sealed with a valid digest; those
        records are left untouched and counted as failed. Unencrypted records
        are not changed. Each file is rewritten atomically.

        Returns counts: {"migrated", "already_keyed", "unencrypted", "failed"}.
        """
        key_b64 = os.environ.get("VAULT_ENCRYPTION_KEY")
        fernet = _get_fernet()
        if fernet is None or not key_b64:
            raise RuntimeError(
                "VAULT_ENCRYPTION_KEY must be set to migrate encrypted records."
            )

        counts = {"migrated": 0, "already_keyed": 0, "unencrypted": 0, "failed": 0}
        evidence_dir = EvidenceAssuranceProtocol._evidence_dir()
        if not os.path.isdir(evidence_dir):
            return counts

        for name in sorted(os.listdir(evidence_dir)):
            if not name.endswith(".json") or not _UUID_RE.fullmatch(name[:-5]):
                continue
            filepath = os.path.join(evidence_dir, name)
            try:
                with open(filepath, "r") as f:
                    record = json.load(f)

                if not record.get("encrypted"):
                    counts["unencrypted"] += 1
                    continue
                if "hmac_sha256" in record:
                    counts["already_keyed"] += 1
                    continue

                payload = fernet.decrypt(record["raw_payload"].encode("ascii")).decode(
                    "utf-8"
                )
                plain = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                if not hmac.compare_digest(plain, str(record["sha256"])):
                    logger.error(
                        "Not migrating %s: payload does not match its stored "
                        "SHA-256 (corrupted or tampered)",
                        name,
                    )
                    counts["failed"] += 1
                    continue

                del record["sha256"]
                record["hmac_sha256"] = _keyed_digest(payload, key_b64)
                tmp_path = f"{filepath}.tmp"
                try:
                    with open(tmp_path, "w") as f:
                        json.dump(record, f, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp_path, filepath)
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                counts["migrated"] += 1
            except (
                KeyError,
                json.JSONDecodeError,
                OSError,
                ValueError,
                InvalidToken,
            ) as exc:
                logger.error("Not migrating %s: %s", name, exc)
                counts["failed"] += 1

        return counts


if __name__ == "__main__":
    import sys

    if sys.argv[1:] != ["migrate-digests"]:
        sys.exit("usage: python -m swarm.evidence migrate-digests")
    logging.basicConfig(level=logging.INFO)
    result = EvidenceAssuranceProtocol.migrate_legacy_digests()
    print(json.dumps(result))
    sys.exit(1 if result["failed"] else 0)
