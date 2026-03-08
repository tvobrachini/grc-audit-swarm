import uuid
import streamlit as st


def initialize_session_state():
    """Initializes the required variables in Streamlit's session_state."""
    for k, v in [
        ("thread_id", str(uuid.uuid4())),
        ("chat_history", []),
        ("scope_submitted", False),
        ("scope_text_cache", ""),
        ("control_feedback", {}),
        ("suggested_audit_name", ""),
        ("resume_swarm", False),
    ]:
        if k not in st.session_state:
            st.session_state[k] = v
