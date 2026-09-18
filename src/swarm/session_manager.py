"""
Audit Session Manager
----------------------------
Stores a mapping of LangGraph thread_ids → human-readable audit names
in a simple JSON file on disk so sessions survive Streamlit restarts.

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
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_SESSIONS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "../../data/audit_sessions.json"
)
SESSIONS_PATH = os.environ.get("SESSIONS_PATH", _DEFAULT_SESSIONS_PATH)

# Guards the read-modify-write sequence in save_session/update_session/delete_session
# against lost updates from concurrent requests within this process.
_LOCK = threading.Lock()


def _load() -> Dict:
    if os.path.exists(SESSIONS_PATH):
        try:
            with open(SESSIONS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.exception(
                "Failed to load sessions file at %s — returning empty session map",
                SESSIONS_PATH,
            )
            return {}
    return {}


def _save(data: Dict) -> None:
    dir_path = os.path.dirname(SESSIONS_PATH)
    os.makedirs(dir_path, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=dir_path, delete=False, suffix=".tmp", encoding="utf-8"
        ) as tmp:
            tmp_path = tmp.name
            json.dump(data, tmp, indent=2)
        os.replace(tmp_path, SESSIONS_PATH)
    except OSError:
        logger.exception("Failed to save sessions file at %s", SESSIONS_PATH)
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def save_session(
    thread_id: str,
    name: str,
    scope_text: str = "",
    chat_history: Optional[list] = None,
) -> None:
    """Register/update an audit session by thread_id."""
    with _LOCK:
        data = _load()
        existing = data.get(thread_id, {})
        data[thread_id] = {
            "name": name,
            "created_at": existing.get(
                "created_at", datetime.now().isoformat(timespec="seconds")
            ),
            "scope_preview": scope_text[:200],  # keep it short for display
            "scope_text": scope_text,
            "chat_history": chat_history or existing.get("chat_history", []),
        }
        _save(data)


def update_session(thread_id: str, **fields) -> None:
    """Merge selected session fields for an existing thread."""
    with _LOCK:
        data = _load()
        if thread_id not in data:
            return

        data[thread_id].update(fields)
        _save(data)


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


def get_session(thread_id: str) -> Optional[Dict]:
    return _load().get(thread_id)
