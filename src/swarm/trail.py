"""
Hash-chained approval trail.

Every trail entry is appended through :func:`append_entry`, which stores

* ``prev_hash``  — the ``entry_hash`` of the previous entry (a fixed genesis
  value for the first one), and
* ``entry_hash`` — a digest over the canonical JSON of the entry itself
  (every field except ``entry_hash``, so ``prev_hash`` and ``hash_alg`` are
  covered), and
* ``hash_alg``   — ``hmac-sha256`` when ``VAULT_ENCRYPTION_KEY`` is set
  (key derived from it under a trail-specific label), else ``sha256``.

:func:`verify_trail` recomputes the chain.

What this detects
    * an edited entry (its digest no longer matches),
    * reordered entries and a removed entry anywhere before the last one
      (the next entry's ``prev_hash`` no longer matches),
    * with the keyed variant: all of the above even when the editor
      recomputed every digest, unless they also hold the key.

What it does not detect on its own
    * removal of entries from the *end* of the trail, or of the whole trail
      (the remaining chain is still valid) — unless the head is anchored
      elsewhere: :func:`verify_trail` accepts an ``anchor`` (entry count and
      head hash, stored by the API in a separate file) and reports a trail
      shorter than, or diverging from, the anchor. The anchor only helps if an
      editor cannot also rewrite the anchor file (ship it to separate storage
      for that);
    * with the unkeyed variant, an editor who recomputes the whole chain;
    * anything about *who* acted: identities are self-declared.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Iterable, Mapping, Optional

from swarm.evidence import derive_labelled_key

TRAIL_KEY_LABEL = b"grc-audit-swarm/approval-trail/hmac-sha256/v1"
GENESIS_HASH = "0" * 64
ALG_KEYED = "hmac-sha256"
ALG_PLAIN = "sha256"
_HASH_FIELDS = ("prev_hash", "entry_hash", "hash_alg")

# Verification statuses (``ok`` is True only for STATUS_OK).
STATUS_OK = "ok"
STATUS_LEGACY = "legacy_unchained"
STATUS_BROKEN = "broken"
STATUS_TRUNCATED = "truncated"
STATUS_UNKEYED = "unkeyed"
STATUS_KEY_UNAVAILABLE = "key_unavailable"
STATUS_ARTIFACT_CHANGED = "artifact_changed"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_entry(entry: Mapping[str, Any]) -> bytes:
    """Canonical bytes an entry's digest covers: every field but entry_hash."""
    return _canonical({k: v for k, v in entry.items() if k != "entry_hash"})


def _trail_key() -> Optional[bytes]:
    return derive_labelled_key(TRAIL_KEY_LABEL)


def _digest(data: bytes, alg: str, key: Optional[bytes]) -> str:
    if alg == ALG_KEYED:
        if key is None:
            raise ValueError("hmac-sha256 digest requires the trail key")
        return hmac.new(key, data, hashlib.sha256).hexdigest()
    return hashlib.sha256(data).hexdigest()


def _is_chained(entry: Mapping[str, Any]) -> bool:
    return any(f in entry for f in _HASH_FIELDS)


def _legacy_seal(prefix: list[Mapping[str, Any]], alg: str, key: Optional[bytes]):
    """``prev_hash`` of the first chained entry.

    GENESIS for a trail that was chained from the start; otherwise a digest
    over the unchained (pre-upgrade) entries, which seals them from then on.
    """
    if not prefix:
        return GENESIS_HASH
    return _digest(_canonical([dict(e) for e in prefix]), alg, key)


def append_entry(trail: list[dict[str, str]], entry: dict[str, str]) -> dict:
    """Chain ``entry`` onto ``trail`` (in place) and return the stored entry.

    The only sanctioned way to add to the approval trail. Any hash fields on
    the incoming entry are replaced.
    """
    key = _trail_key()
    alg = ALG_KEYED if key is not None else ALG_PLAIN
    stored = {k: str(v) for k, v in entry.items() if k not in _HASH_FIELDS}
    last_chained = next(
        (i for i in range(len(trail) - 1, -1, -1) if _is_chained(trail[i])), None
    )
    if last_chained is None:
        prev = _legacy_seal(trail, alg, key)
    else:
        prev = str(trail[-1].get("entry_hash", ""))
    stored["hash_alg"] = alg
    stored["prev_hash"] = prev
    stored["entry_hash"] = _digest(canonical_entry(stored), alg, key)
    trail.append(stored)
    return stored


def head(trail: list[Mapping[str, Any]]) -> tuple[int, Optional[str]]:
    """(entry count, entry_hash of the last entry or None if unchained/empty)."""
    if not trail:
        return 0, None
    last = trail[-1].get("entry_hash")
    return len(trail), (str(last) if last else None)


def artifact_digest(artifact: Any) -> str:
    """SHA-256 over the canonical JSON of an artifact (model or plain dict)."""
    if hasattr(artifact, "model_dump"):
        artifact = artifact.model_dump(mode="json")
    return hashlib.sha256(_canonical(artifact)).hexdigest()


def _result(
    status: str,
    trail: list[Mapping[str, Any]],
    *,
    first_broken_index: Optional[int] = None,
    legacy_entries: int = 0,
    keyed: bool = False,
    anchored: bool = False,
    detail: str = "",
    changed: Optional[list[str]] = None,
) -> dict[str, Any]:
    count, head_hash = head(trail)
    return {
        "ok": status == STATUS_OK,
        "status": status,
        "entries": count,
        "legacy_entries": legacy_entries,
        "first_broken_index": first_broken_index,
        "keyed": keyed,
        "anchored": anchored,
        "head_hash": head_hash,
        "changed_since_approval": changed or [],
        "detail": detail,
    }


def verify_trail(
    trail: list[Mapping[str, Any]],
    anchor: Optional[Mapping[str, Any]] = None,
    artifacts: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Recompute the chain and report the first problem found.

    Args:
        trail: the approval trail entries, oldest first.
        anchor: optional ``{"count": n, "head_hash": h}`` recorded elsewhere
            when the trail had ``n`` entries; lets truncation of the tail be
            detected.
        artifacts: optional ``{field_name: artifact}`` (``racm_plan`` …); each
            gate approval records the digest of the artifact it approved, and
            a mismatch is reported as ``artifact_changed``.

    Returns a dict with ``ok``, ``status`` (see the ``STATUS_*`` constants),
    ``first_broken_index`` and diagnostic fields.
    """
    try:
        anchor_count = max(int((anchor or {}).get("count", 0) or 0), 0)
    except (TypeError, ValueError):
        anchor_count = 0  # malformed anchor record: treated as no anchor
    anchor_head = str((anchor or {}).get("head_hash", "") or "")
    anchored = anchor_count > 0
    first = next((i for i, e in enumerate(trail) if _is_chained(e)), None)

    if first is None:
        if anchored:
            return _result(
                STATUS_TRUNCATED if not trail else STATUS_BROKEN,
                trail,
                first_broken_index=0,
                legacy_entries=len(trail),
                anchored=True,
                detail=(
                    f"The anchor records {anchor_count} chained entries, but the "
                    "trail has no chained entries (removed or stripped of hashes)."
                ),
            )
        if not trail:
            return _result(STATUS_OK, trail, detail="No trail entries yet.")
        return _result(
            STATUS_LEGACY,
            trail,
            legacy_entries=len(trail),
            detail=(
                "Unchained (legacy): these entries were written before the trail "
                "was hash-chained and cannot be verified."
            ),
        )

    key = _trail_key()
    keyed_entries = 0
    last_plain: Optional[int] = None
    expected_prev: Optional[str] = None
    for i in range(first, len(trail)):
        entry = trail[i]
        stored_hash = entry.get("entry_hash")
        alg = entry.get("hash_alg")

        def broken(reason: str, index: int = i) -> dict[str, Any]:
            return _result(
                STATUS_BROKEN,
                trail,
                first_broken_index=index,
                legacy_entries=first,
                anchored=anchored,
                detail=f"Entry {index}: {reason}",
            )

        if not stored_hash or alg not in (ALG_KEYED, ALG_PLAIN):
            return broken("missing or unknown hash fields.")
        if alg == ALG_KEYED and key is None:
            return _result(
                STATUS_KEY_UNAVAILABLE,
                trail,
                first_broken_index=None,
                legacy_entries=first,
                anchored=anchored,
                detail=(
                    f"Entry {i} is keyed (hmac-sha256) but VAULT_ENCRYPTION_KEY is "
                    "not set, so the trail cannot be verified."
                ),
            )
        if expected_prev is None:
            expected_prev = _legacy_seal(list(trail[:first]), alg, key)
        if not hmac.compare_digest(str(entry.get("prev_hash", "")), expected_prev):
            return broken(
                "prev_hash does not match the previous entry "
                "(an entry was removed, inserted or reordered before this one)."
            )
        if not hmac.compare_digest(
            _digest(canonical_entry(entry), alg, key), str(stored_hash)
        ):
            return broken("content does not match its entry_hash (edited).")
        if alg == ALG_KEYED:
            keyed_entries += 1
            last_plain = None
        elif last_plain is None:
            last_plain = i
        expected_prev = str(stored_hash)

    if anchored:
        if len(trail) < anchor_count:
            return _result(
                STATUS_TRUNCATED,
                trail,
                first_broken_index=len(trail),
                legacy_entries=first,
                anchored=True,
                detail=(
                    f"The anchor records {anchor_count} entries but the trail has "
                    f"{len(trail)}: entries were removed from the end."
                ),
            )
        at = trail[anchor_count - 1].get("entry_hash", "")
        if not hmac.compare_digest(str(at), anchor_head):
            return _result(
                STATUS_BROKEN,
                trail,
                first_broken_index=anchor_count - 1,
                legacy_entries=first,
                anchored=True,
                detail=(
                    f"Entry {anchor_count - 1} does not match the anchored head "
                    "hash: the trail was rewritten."
                ),
            )

    all_keyed = keyed_entries == len(trail) - first
    if key is not None and last_plain is not None:
        # Unkeyed entries not sealed by a later keyed entry: indistinguishable
        # from entries recomputed by someone without the key.
        return _result(
            STATUS_UNKEYED,
            trail,
            legacy_entries=first,
            anchored=anchored,
            detail=(
                f"Entries from index {last_plain} carry an unkeyed sha256 chain "
                "although a key is configured: they may predate the key, or were "
                "recomputed without it — they cannot be verified as untampered."
            ),
        )

    changed = changed_since_approval(trail, artifacts) if artifacts is not None else []
    if changed:
        return _result(
            STATUS_ARTIFACT_CHANGED,
            trail,
            legacy_entries=first,
            keyed=all_keyed,
            anchored=anchored,
            changed=changed,
            detail=(
                "The chain is intact, but these approved artifacts no longer match "
                "the digest recorded at approval: " + ", ".join(changed) + "."
            ),
        )

    note = (
        f" Entries 0-{first - 1} predate chaining; they are sealed from entry "
        f"{first} on, but their earlier history cannot be verified."
        if first
        else ""
    )
    return _result(
        STATUS_OK,
        trail,
        legacy_entries=first,
        keyed=all_keyed,
        anchored=anchored,
        detail=(
            f"Chain intact ({'hmac-sha256' if all_keyed else 'sha256'})"
            + ("" if anchored else "; head not anchored")
            + "."
            + note
        ),
    )


def changed_since_approval(
    trail: Iterable[Mapping[str, Any]], artifacts: Mapping[str, Any]
) -> list[str]:
    """Gate labels whose approved artifact no longer matches its digest.

    Only the latest approval per artifact counts.
    """
    latest: dict[str, tuple[str, str]] = {}
    for e in trail:
        if e.get("action") == "gate_approval" and e.get("artifact_digest"):
            latest[str(e.get("artifact", ""))] = (
                str(e.get("gate", "")),
                str(e["artifact_digest"]),
            )
    changed = []
    for field, (gate, digest) in latest.items():
        current = artifacts.get(field)
        if current is None or not hmac.compare_digest(artifact_digest(current), digest):
            changed.append(gate)
    return changed
