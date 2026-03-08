"""
Audit Session Manager
----------------------------
Stores a mapping of LangGraph thread_ids → human-readable audit names
in a simple JSON file on disk so sessions survive Streamlit restarts.

File: data/audit_sessions.json
Schema: { "thread_id": {"name": "...", "created_at": "...", "scope_preview": "..."} }
"""

import json
import os
from datetime import datetime
from typing import Dict, Optional

SESSIONS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "../../data/audit_sessions.json"
)


def _load() -> Dict:
    if os.path.exists(SESSIONS_PATH):
        try:
            with open(SESSIONS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save(data: Dict) -> None:
    os.makedirs(os.path.dirname(SESSIONS_PATH), exist_ok=True)
    with open(SESSIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def save_session(thread_id: str, name: str, scope_preview: str = "") -> None:
    """Register/update an audit session by thread_id."""
    data = _load()
    data[thread_id] = {
        "name": name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "scope_preview": scope_preview[:200],  # keep it short for display
    }
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
