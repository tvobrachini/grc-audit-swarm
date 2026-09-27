"""
Audit Session Manager
----------------------------
Stores audit sessions (name, status, flow state snapshot) keyed by session id
in a simple JSON file on disk so sessions survive process restarts.

File: data/audit_sessions.json
Schema: {
  "thread_id": {
    "name": "...",
    "created_at": "...",
    "scope_preview": "...",
    "state_snapshot": {...}
  }
}
"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_SESSIONS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "../../data/audit_sessions.json"
)
SESSIONS_PATH = os.environ.get("SESSIONS_PATH", _DEFAULT_SESSIONS_PATH)

# Guards the read-modify-write sequence in save_session/update_session/delete_session
# against lost updates from concurrent requests within this process. Re-entrant
# because the corrupt-file backup path in _load (called by those writers while
# they hold the lock) takes it too.
_LOCK = threading.RLock()


class _CorruptSessionsFile(Exception):
    pass


def _read_file() -> Dict:
    """Parse the sessions file. Raises FileNotFoundError or _CorruptSessionsFile."""
    try:
        with open(SESSIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, UnicodeDecodeError) as exc:  # JSONDecodeError ⊂ ValueError
        raise _CorruptSessionsFile(f"{type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict):
        raise _CorruptSessionsFile(
            f"top-level JSON is {type(data).__name__}, not object"
        )
    return data


def _backup_corrupt_file(reason: str) -> None:
    """Move an unreadable sessions file aside so the next save cannot wipe it."""
    backup = f"{SESSIONS_PATH}.corrupt-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"
    os.replace(SESSIONS_PATH, backup)
    logger.error(
        "Sessions file at %s is corrupt (%s) — backed it up to %s and starting "
        "with an empty session map. Restore it manually if needed.",
        SESSIONS_PATH,
        reason,
        backup,
    )


def _load() -> Dict:
    """Read the sessions map.

    A file that exists but cannot be parsed is renamed to
    ``<path>.corrupt-<timestamp>`` before returning an empty map; otherwise the
    next ``_save`` would silently overwrite every stored session. I/O errors
    (permissions, disk) are re-raised rather than treated as "no sessions" for
    the same reason.
    """
    try:
        return _read_file()
    except FileNotFoundError:
        return {}
    except _CorruptSessionsFile:
        pass
    # Re-check under the writer lock before moving the file aside: another
    # thread may already have backed it up and written a fresh, valid file,
    # which must not be mistaken for the corrupt one.
    with _LOCK:
        try:
            return _read_file()
        except FileNotFoundError:
            return {}
        except _CorruptSessionsFile as exc:
            _backup_corrupt_file(str(exc))
            return {}


def _save(data: Dict, path: Optional[str] = None) -> None:
    """Write a JSON map atomically (temp file in the same dir + os.replace)."""
    path = path or SESSIONS_PATH
    dir_path = os.path.dirname(path) or "."
    os.makedirs(dir_path, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=dir_path, delete=False, suffix=".tmp", encoding="utf-8"
        ) as tmp:
            tmp_path = tmp.name
            json.dump(data, tmp, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, path)
    except (OSError, TypeError, ValueError):
        logger.exception("Failed to save JSON file at %s", path)
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def save_session(
    thread_id: str,
    name: str,
    scope_text: str = "",
    chat_history: Optional[list] = None,
    **extra_fields: Any,
) -> None:
    """Register/update an audit session by thread_id.

    Creates the entry if missing. Fields this function does not own
    (``status``, ``ui_phase``, ``state_snapshot`` …) are preserved, and any
    ``extra_fields`` are merged in the same locked write.
    """
    with _LOCK:
        data = _load()
        entry = dict(data.get(thread_id, {}))
        entry.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
        entry.update(
            {
                "name": name,
                "scope_preview": scope_text[:200],  # keep it short for display
                "scope_text": scope_text,
                "chat_history": chat_history or entry.get("chat_history", []),
            }
        )
        entry.update(extra_fields)
        data[thread_id] = entry
        _save(data)


def update_session(thread_id: str, **fields) -> bool:
    """Merge fields into an existing session in a single locked write.

    Never creates a session: returns False (and writes nothing) when the
    thread_id is unknown, e.g. because it was deleted while a phase was running.
    """
    with _LOCK:
        data = _load()
        if thread_id not in data:
            return False

        data[thread_id].update(fields)
        _save(data)
        return True


def list_sessions() -> Dict:
    """Return all saved sessions, newest first."""
    data = _load()
    return dict(
        sorted(data.items(), key=lambda kv: kv[1].get("created_at", ""), reverse=True)
    )


def delete_session(thread_id: str) -> None:
    with _LOCK:
        data = _load()
        data.pop(thread_id, None)
        _save(data)
        try:
            anchors = _load_anchors()
        except (ValueError, UnicodeDecodeError):
            return
        if thread_id in anchors:
            anchors.pop(thread_id)
            _save(anchors, _anchors_path())


def get_session(thread_id: str) -> Optional[Dict]:
    return _load().get(thread_id)


# ── Approval-trail anchors ───────────────────────────────────────────────────
# The entry count and head hash of each session's hash-chained approval trail,
# kept in a separate file (TRAIL_ANCHORS_PATH, default: trail_anchors.json
# next to the sessions file). swarm.trail.verify_trail uses it to detect a
# trail whose tail was cut off, which the chain alone cannot show. It only
# helps against someone who cannot also rewrite this file — put it on
# separate storage for that.


def _anchors_path() -> str:
    return os.environ.get("TRAIL_ANCHORS_PATH") or os.path.join(
        os.path.dirname(SESSIONS_PATH) or ".", "trail_anchors.json"
    )


def _load_anchors() -> Dict:
    try:
        with open(_anchors_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (ValueError, UnicodeDecodeError):
        logger.error("Trail anchors file %s is unreadable", _anchors_path())
        raise
    return data if isinstance(data, dict) else {}


def get_trail_anchor(thread_id: str) -> Optional[Dict]:
    """``{"count", "head_hash", "updated_at"}`` for the session, if anchored."""
    try:
        anchor = _load_anchors().get(thread_id)
    except (ValueError, UnicodeDecodeError):
        return None
    return anchor if isinstance(anchor, dict) else None


def save_trail_anchor(thread_id: str, count: int, head_hash: str) -> bool:
    """Record the trail head. Refuses to move the anchor backwards.

    The trail is append-only, so a lower count than the one recorded means
    the trail being saved was truncated: that is logged and not recorded.
    Returns True when the anchor was written (or already current).
    """
    with _LOCK:
        anchors = _load_anchors()
        current = anchors.get(thread_id) or {}
        current_count = int(current.get("count", 0) or 0)
        if count < current_count:
            logger.error(
                "Not moving trail anchor for %s back from %d to %d entries",
                thread_id,
                current_count,
                count,
            )
            return False
        if count == current_count and current.get("head_hash") == head_hash:
            return True
        anchors[thread_id] = {
            "count": count,
            "head_hash": head_hash,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        _save(anchors, _anchors_path())
        return True
