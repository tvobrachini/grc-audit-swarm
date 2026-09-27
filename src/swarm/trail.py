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

Reviewer decisions (ADR-011)
    each decision is a ``review_decision`` entry holding its
    ``decision_digest``, and a gate approval records a ``decisions_digest``
    over that phase's decisions recorded before it. Given the stored
    decisions, :func:`verify_trail` reports one that was edited, removed or
    added without an entry (``decision_changed``) and a gate whose sealed
    decisions changed (``artifact_changed``).

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
STATUS_DECISION_CHANGED = "decision_changed"

# Phase whose reviewer decisions a gate approval of each artifact seals.
ARTIFACT_PHASE = {"racm_plan": 1, "working_papers": 2, "final_report": 3}


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


def decision_digest(decision: Any) -> str:
    """SHA-256 over the canonical JSON of one reviewer decision."""
    return artifact_digest(decision)


def decisions_digest(decisions: Iterable[Any]) -> str:
    """SHA-256 over the canonical JSON list of reviewer decisions, in order."""
    return hashlib.sha256(
        _canonical(
            [
                d.model_dump(mode="json") if hasattr(d, "model_dump") else d
                for d in decisions
            ]
        )
    ).hexdigest()


def _decision_fields(decision: Any) -> Mapping[str, Any]:
    if hasattr(decision, "model_dump"):
        return decision.model_dump(mode="json")
    return decision if isinstance(decision, Mapping) else {}


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
    decisions_changed: Optional[list[str]] = None,
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
        "decisions_changed": decisions_changed or [],
        "detail": detail,
    }


def verify_trail(
    trail: list[Mapping[str, Any]],
    anchor: Optional[Mapping[str, Any]] = None,
    artifacts: Optional[Mapping[str, Any]] = None,
    decisions: Optional[Iterable[Any]] = None,
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
        decisions: optional reviewer decisions as stored (models or dicts).
            Each must match the ``decision_digest`` of its ``review_decision``
            entry (else ``decision_changed``), and each gate approval's
            ``decisions_digest`` must still match the decisions it sealed
            (else ``artifact_changed`` for that gate).

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

    decision_list = list(decisions) if decisions is not None else None
    changed = (
        changed_since_approval(trail, artifacts, decision_list)
        if artifacts is not None
        else []
    )
    decision_problems = (
        decisions_changed(trail, decision_list) if decision_list is not None else []
    )
    if changed:
        return _result(
            STATUS_ARTIFACT_CHANGED,
            trail,
            legacy_entries=first,
            keyed=all_keyed,
            anchored=anchored,
            changed=changed,
            decisions_changed=decision_problems,
            detail=(
                "The chain is intact, but these approved artifacts (or the "
                "reviewer decisions sealed with them) no longer match the digest "
                "recorded at approval: " + ", ".join(changed) + "."
            ),
        )
    if decision_problems:
        return _result(
            STATUS_DECISION_CHANGED,
            trail,
            legacy_entries=first,
            keyed=all_keyed,
            anchored=anchored,
            decisions_changed=decision_problems,
            detail=(
                "The chain is intact, but reviewer decisions do not match their "
                "trail entries: " + "; ".join(decision_problems) + "."
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
    trail: Iterable[Mapping[str, Any]],
    artifacts: Mapping[str, Any],
    decisions: Optional[Iterable[Any]] = None,
) -> list[str]:
    """Gate labels whose approved artifact no longer matches its digest.

    Only the latest approval per artifact counts. With ``decisions``, an
    approval that recorded a ``decisions_digest`` is also checked: the
    decisions of that phase recorded before the approval (in trail order)
    must still exist and hash to it. Decisions recorded after the approval
    (management responses after COMPLETED) are not part of it.
    """
    latest: dict[str, tuple[str, str, Optional[str], list[str]]] = {}
    recorded: dict[int, list[str]] = {}
    for e in trail:
        if e.get("action") == "review_decision":
            try:
                phase = int(str(e.get("phase", "")))
            except ValueError:
                continue
            recorded.setdefault(phase, []).append(str(e.get("decision_id", "")))
        elif e.get("action") == "gate_approval" and e.get("artifact_digest"):
            field = str(e.get("artifact", ""))
            sealed = e.get("decisions_digest")
            latest[field] = (
                str(e.get("gate", "")),
                str(e["artifact_digest"]),
                str(sealed) if sealed else None,
                list(recorded.get(ARTIFACT_PHASE.get(field, 0), [])),
            )
    by_id: dict[str, Mapping[str, Any]] = {}
    for d in decisions or []:
        fields = _decision_fields(d)
        by_id[str(fields.get("decision_id", ""))] = fields
    changed = []
    for field, (gate, digest, sealed, ids) in latest.items():
        current = artifacts.get(field)
        if current is None or not hmac.compare_digest(artifact_digest(current), digest):
            changed.append(gate)
            continue
        if sealed is None or decisions is None:
            continue
        if any(i not in by_id for i in ids) or not hmac.compare_digest(
            decisions_digest([by_id[i] for i in ids]), sealed
        ):
            changed.append(gate)
    return changed


def decisions_changed(
    trail: Iterable[Mapping[str, Any]], decisions: Iterable[Any]
) -> list[str]:
    """Reviewer decisions that do not match their ``review_decision`` entry.

    Reports a decision with no trail entry (added outside the flow), one
    whose content no longer hashes to its recorded ``decision_digest``
    (edited), and a trail entry whose decision is gone (removed).
    """
    recorded: dict[str, str] = {}
    for e in trail:
        if e.get("action") == "review_decision" and e.get("decision_id"):
            recorded[str(e["decision_id"])] = str(e.get("decision_digest", ""))
    problems: list[str] = []
    seen: set[str] = set()
    for d in decisions:
        fields = _decision_fields(d)
        did = str(fields.get("decision_id", ""))
        seen.add(did)
        label = f"decision {did} ({fields.get('decision_type', '?')} on {fields.get('subject_id', '?')})"
        if did not in recorded:
            problems.append(f"{label} has no trail entry")
        elif not hmac.compare_digest(decision_digest(fields), recorded[did]):
            problems.append(f"{label} was changed after it was recorded")
    for did in recorded:
        if did not in seen:
            problems.append(f"decision {did} is in the trail but was removed")
    return problems
