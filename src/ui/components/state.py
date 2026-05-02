import uuid
import streamlit as st

SESSION_DEFAULTS = {
    "thread_id": lambda: str(uuid.uuid4()),
    "chat_history": list,
    "scope_submitted": lambda: False,
    "scope_text_cache": str,
    "control_feedback": dict,
    "suggested_audit_name": str,
    "resume_swarm": lambda: False,
}

TRANSIENT_SESSION_KEYS = {
    "scope_up",
    "scope_lab",
    "scope_ta",
    "audit_name",
    "status_filter",
}
TRANSIENT_SESSION_PREFIXES = ("fb_",)


def initialize_session_state():
    """Initializes the required variables in Streamlit's session_state."""
    for key, factory in SESSION_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = factory()


def build_session_reset(thread_id: str, existing_keys) -> tuple[dict, list[str]]:
    update_values = {
        "thread_id": thread_id,
        "chat_history": [],
        "scope_submitted": False,
        "scope_text_cache": "",
        "control_feedback": {},
        "suggested_audit_name": "",
        "resume_swarm": False,
    }
    clear_keys = [
        key
        for key in existing_keys
        if key in TRANSIENT_SESSION_KEYS or key.startswith(TRANSIENT_SESSION_PREFIXES)
    ]
    return update_values, clear_keys


def reset_session_state(thread_id: str) -> None:
    update_values, clear_keys = build_session_reset(thread_id, st.session_state.keys())
    for key in clear_keys:
        st.session_state.pop(key, None)
    st.session_state.update(update_values)
