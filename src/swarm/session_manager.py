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
import os
import tempfile
from datetime import datetime
from typing import Dict, Optional

_DEFAULT_SESSIONS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "../../data/audit_sessions.json"
)
SESSIONS_PATH = os.environ.get("SESSIONS_PATH", _DEFAULT_SESSIONS_PATH)


def _load() -> Dict:
    if os.path.exists(SESSIONS_PATH):
        try:
            with open(SESSIONS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save(data: Dict) -> None:
    dir_path = os.path.dirname(SESSIONS_PATH)
    os.makedirs(dir_path, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=dir_path, delete=False, suffix=".tmp", encoding="utf-8"
    ) as tmp:
        json.dump(data, tmp, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, SESSIONS_PATH)


def save_session(
    thread_id: str,
    name: str,
    scope_text: str = "",
    chat_history: Optional[list] = None,
) -> None:
    """Register/update an audit session by thread_id."""
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
    data = _load()
    data.pop(thread_id, None)
    _save(data)


def get_session(thread_id: str) -> Optional[Dict]:
    return _load().get(thread_id)
