import hashlib
import hmac
import json
import logging
import re
import uuid
import os
import datetime
import base64
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Optional

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

# A quote shorter than this proves little: it would match almost any payload
# by chance (e.g. a lone word or punctuation).
_MIN_QUOTE_LENGTH = 15

# Repo root: src/swarm/evidence.py -> src/swarm -> src -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def app_version() -> str:
    """The running app's version — for generation and evidence provenance.

    Tries the installed package's metadata first (the case in a built image);
    falls back to ``pyproject.toml``'s ``project.version`` (the case running
    from a source checkout, as in local dev and tests, where the project is
    never installed as a distribution). ``"unknown"`` if neither is
    available.
    """
    try:
        from importlib.metadata import version as _pkg_version, PackageNotFoundError
        return _pkg_version("grc-audit-swarm")
    except (ImportError, PackageNotFoundError):
        return os.environ.get("APP_VERSION", "unknown")


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


def _derive_hmac_key(key_b64: str, label: bytes = _HMAC_KEY_LABEL) -> bytes:
    """Derive a purpose-specific HMAC key from the base64 VAULT_ENCRYPTION_KEY.

    ``label`` separates the purposes: the evidence vault and the approval
    trail (see ``swarm.trail``) each get their own key, so a digest made for
    one can never be replayed as a valid digest for the other.
    """
    raw_key = base64.urlsafe_b64decode(key_b64.encode())
    return hmac.new(raw_key, label, hashlib.sha256).digest()


def derive_labelled_key(label: bytes) -> Optional[bytes]:
    """HMAC key for ``label`` derived from VAULT_ENCRYPTION_KEY, or None if unset."""
    key_b64 = os.environ.get("VAULT_ENCRYPTION_KEY")
    if not key_b64:
        return None
    return _derive_hmac_key(key_b64, label)


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


def _sanitize_metadata(value: Any) -> Any:
    """Redact AWS account IDs from every string in ``value``, recursively.

    Applied to collection metadata (region, operation, caller identity …)
    before it is stored or hashed. Metadata must already be free of secrets
    and credentials — see :meth:`EvidenceAssuranceProtocol.register_evidence`.
    """
    if isinstance(value, str):
        return _redact_account_ids(value)
    if isinstance(value, dict):
        return {k: _sanitize_metadata(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_metadata(v) for v in value]
    return value


def _digest_input(payload: str, metadata: Optional[dict[str, Any]]) -> str:
    """Text whose hash is the record's integrity digest.

    Binds non-sensitive collection metadata (region, operation, caller
    identity …) into the same digest as the payload, so tampering with either
    is detected the same way (see DECISIONS.md, ADR-010). A record with no
    metadata — every record written before this field existed, and any
    record that is not given one — hashes the payload alone, identical to
    before, so old digests keep verifying unchanged.
    """
    if not metadata:
        return payload
    canonical_metadata = json.dumps(
        metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return payload + "\x00" + canonical_metadata


# Audit session the evidence being collected belongs to. The flow sets it
# around a crew run (see ``evidence_session``) so the evidence tools, which do
# not know the session, bind the records they register to it. CrewAI copies
# the context into the threads it runs tools in.
_CURRENT_SESSION: ContextVar[Optional[str]] = ContextVar(
    "evidence_session_id", default=None
)


@contextmanager
def evidence_session(session_id: Optional[str]) -> Iterator[None]:
    """Bind evidence registered inside the block to ``session_id``."""
    token = _CURRENT_SESSION.set(session_id or None)
    try:
        yield
    finally:
        _CURRENT_SESSION.reset(token)


def record_session_id(record: dict[str, Any]) -> Optional[str]:
    """Session a vault record is bound to, or None for an unbound record.

    ``metadata.session_id`` is written by :meth:`register_evidence`; records
    imported through the API before that field existed carry the session in
    ``metadata.parameters.session_id``. Records with neither (written before
    session binding, or outside a session) are unbound.
    """
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return None
    sid = metadata.get("session_id")
    if not sid:
        params = metadata.get("parameters")
        sid = params.get("session_id") if isinstance(params, dict) else None
    return str(sid) if sid else None


def _write_record_atomically(filepath: str, record: dict[str, Any]) -> None:
    """Write a vault record via a temp file in the same dir + os.replace, so a
    crash never leaves a truncated record behind."""
    dir_path = os.path.dirname(filepath) or "."
    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=dir_path, delete=False, suffix=".tmp", encoding="utf-8"
        ) as tmp:
            tmp_path = tmp.name
            json.dump(record, tmp, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, filepath)
        tmp_path = None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


class EvidenceAssuranceProtocol:
    """Integrity digest (SHA-256, or HMAC-SHA256 when encrypted) plus exact-quote check for collected audit evidence."""

    @staticmethod
    def _evidence_dir() -> str:
        return os.environ.get("EVIDENCE_VAULT_PATH", _DEFAULT_EVIDENCE_DIR)

    @staticmethod
    def register_evidence(
        raw_payload: str,
        source_mcp_operation: str,
        *,
        metadata: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """
        Receives raw payload from an MCP, scrubs AWS account IDs, computes an
        integrity digest (SHA-256, or HMAC-SHA256 keyed from VAULT_ENCRYPTION_KEY
        when the vault is encrypted), stores it on disk, and returns the
        Vault-ID and digest.

        ``metadata`` is optional, non-sensitive collection context (AWS
        region, the API operation(s) called and their non-sensitive
        parameters, the collecting tool's name, the app version, and the
        caller's identity as an ARN with the account id redacted — never a
        key, token or credential). Every string in it is redacted the same
        way as the payload before it is stored. When given, it is folded into
        the same integrity digest as the payload (see ``_digest_input`` and
        ADR-010), so tampering with either is detected the same way; a
        record with no metadata hashes the payload alone, exactly as before.

        ``session_id`` (default: the one set by :func:`evidence_session`, if
        any) is stored as ``metadata.session_id`` and so is covered by the
        digest too; the Gate 2 quote check only accepts a record bound to the
        session it is checking (see :func:`unverified_findings`).
        """
        evidence_dir = EvidenceAssuranceProtocol._evidence_dir()
        os.makedirs(evidence_dir, exist_ok=True)

        # Redact 12-digit AWS account IDs before they leave the environment.
        sanitized_payload = _redact_account_ids(raw_payload)
        sanitized_metadata = _sanitize_metadata(metadata) if metadata else None
        bound_session = session_id or _CURRENT_SESSION.get()
        if bound_session:
            # Added after redaction: a session id is a UUID, never an account
            # id, and must be stored exactly to be compared later.
            sanitized_metadata = dict(sanitized_metadata or {})
            sanitized_metadata["session_id"] = str(bound_session)
        digest_input = _digest_input(sanitized_payload, sanitized_metadata)

        vault_id = str(uuid.uuid4())
        fernet = _get_fernet()
        encrypted = fernet is not None
        if fernet is not None:
            # Keyed digest: a bare SHA-256 beside the ciphertext would leak
            # guessable payloads (see _keyed_digest).
            digest_field = "hmac_sha256"
            digest = _keyed_digest(digest_input, os.environ["VAULT_ENCRYPTION_KEY"])
            stored_payload = fernet.encrypt(sanitized_payload.encode("utf-8")).decode(
                "ascii"
            )
        else:
            # Plaintext is stored alongside, so the hash reveals nothing extra;
            # it detects accidental corruption only.
            digest_field = "sha256"
            digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
            stored_payload = sanitized_payload

        evidence_record = {
            "vault_id": vault_id,
            digest_field: digest,
            "mcp_source": source_mcp_operation,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "raw_payload": stored_payload,
            "encrypted": encrypted,
        }
        if sanitized_metadata is not None:
            evidence_record["metadata"] = sanitized_metadata

        filepath = os.path.join(evidence_dir, f"{vault_id}.json")
        _write_record_atomically(filepath, evidence_record)

        return {"vault_id": vault_id, digest_field: digest}

    @staticmethod
    def verify_exact_quote(
        vault_id: str, exact_quote_claim: str, *, session_id: Optional[str] = None
    ) -> bool:
        """
        Check that a cited quote appears verbatim in a stored evidence record.

        Returns True only if the record exists inside the vault directory, its
        digest still matches the payload, and the quote is an exact substring
        of that payload. It shows the words exist in the evidence, not that the
        conclusion drawn from them is right.

        With ``session_id``, a record bound to a different session (see
        :func:`record_session_id`) is rejected. Unbound records (written
        before session binding) are still accepted.
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

            # Metadata (if any) was folded into the digest at registration
            # time — see _digest_input. Records written before metadata
            # existed have none, so this reproduces the original digest input
            # (the payload alone) for them, unchanged.
            digest_input = _digest_input(payload, evidence_record.get("metadata"))

            if "hmac_sha256" in evidence_record:
                key_b64 = os.environ.get("VAULT_ENCRYPTION_KEY")
                if not key_b64:
                    return False
                expected = _keyed_digest(digest_input, key_b64)
                stored = evidence_record["hmac_sha256"]
            else:
                # Unencrypted records, and encrypted records written before
                # the keyed digest was introduced.
                expected = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
                stored = evidence_record["sha256"]
            if not hmac.compare_digest(expected, str(stored)):
                return False

            if session_id:
                bound = record_session_id(evidence_record)
                # An id stored under metadata.parameters went through account
                # id redaction, so compare its redacted form too.
                if bound and bound not in (
                    session_id,
                    _redact_account_ids(session_id),
                ):
                    logger.warning(
                        "Vault record %s belongs to another session; not "
                        "accepted as evidence for session %s",
                        vault_id,
                        session_id,
                    )
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
                digest_input = _digest_input(payload, record.get("metadata"))
                plain = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
                if not hmac.compare_digest(plain, str(record["sha256"])):
                    logger.error(
                        "Not migrating %s: payload does not match its stored "
                        "SHA-256 (corrupted or tampered)",
                        name,
                    )
                    counts["failed"] += 1
                    continue

                del record["sha256"]
                record["hmac_sha256"] = _keyed_digest(digest_input, key_b64)
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


_NOT_TESTED = "not tested"
# Finding fields that may carry the "Not tested" conclusion (schema-version
# tolerant: read defensively, whichever the working-paper schema defines).
_NOT_TESTED_FIELDS = ("result", "toe_conclusion")


def _normalised_label(value: Any) -> str:
    raw = getattr(value, "value", value)  # Enum members → their value
    if not isinstance(raw, str):
        return ""
    return " ".join(raw.replace("_", " ").replace("-", " ").split()).casefold()


def finding_marked_not_tested(finding: Any) -> bool:
    """True if the finding records that the control was not tested."""
    return any(
        _normalised_label(getattr(finding, name, None)) == _NOT_TESTED
        for name in _NOT_TESTED_FIELDS
    )


def unverified_findings(
    findings: Iterable[Any], *, session_id: Optional[str] = None
) -> list[str]:
    """Control IDs of findings whose evidence quote is not found in the vault.

    Deterministic, no model involved. Every finding with a quote must pass
    :meth:`EvidenceAssuranceProtocol.verify_exact_quote` (for ``session_id``
    when given: a record bound to another session does not count). A finding without a
    quote passes only if it says the control was not tested (``result`` or
    ``toe_conclusion`` equal to "Not tested"); a tested conclusion must be
    backed by a verifiable quote.
    """
    unverified: list[str] = []
    for index, finding in enumerate(findings):
        control_id = getattr(finding, "control_id", None) or f"finding #{index + 1}"
        quote = getattr(finding, "exact_quote_from_evidence", None) or ""
        vault_id = getattr(finding, "vault_id_reference", None) or ""
        if not quote.strip():
            if not finding_marked_not_tested(finding):
                unverified.append(str(control_id))
            continue
        if not EvidenceAssuranceProtocol.verify_exact_quote(
            str(vault_id), quote, session_id=session_id
        ):
            unverified.append(str(control_id))
    return unverified


if __name__ == "__main__":
    import sys

    if sys.argv[1:] != ["migrate-digests"]:
        sys.exit("usage: python -m swarm.evidence migrate-digests")
    logging.basicConfig(level=logging.INFO)
    result = EvidenceAssuranceProtocol.migrate_legacy_digests()
    print(json.dumps(result))
    sys.exit(1 if result["failed"] else 0)
