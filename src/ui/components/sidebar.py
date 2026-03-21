import os
import streamlit as st
from swarm.session_manager import list_sessions, delete_session


# Maps view_phase → (badge_class, display_text)
_PHASE_BADGE: dict[str, tuple[str, str]] = {
    "phase1": ("status-planning", "Planning"),
    "phase1_review": ("status-review", "Awaiting Review"),
    "phase2": ("status-planning", "Testing"),
    "phase2_review": ("status-review", "Findings Review"),
    "complete": ("status-complete", "Complete"),
}


def render_sidebar():
    """Renders the sidebar with audit history and system status."""
    with st.sidebar:
        st.markdown(
            '<div class="hero-badge">🤖 Groq + LangGraph</div>', unsafe_allow_html=True
        )
        st.title("GRC Audit Swarm")
        st.caption("AI Multi-Agent Audit System")
        st.markdown("---")

        # ── LLM status indicator ───────────────────────────────────────────────
        g = os.environ.get("GROQ_API_KEY")
        o = os.environ.get("OPENAI_API_KEY")
        if g:
            st.success("✅ Groq (llama-3.3-70b)")
        elif o:
            st.warning("⚠️ OpenAI fallback")
        else:
            st.info("ℹ️ Mock mode")

        st.markdown("---")
        st.header("🗂️ Audit History")

        sessions = list_sessions()
        if sessions:
            for tid, meta in sessions.items():
                created = meta.get("created_at", "")[:10]
                snapshot = meta.get("state_snapshot") or {}
                view_phase = snapshot.get("view_phase", "")
                badge_cls, badge_text = _PHASE_BADGE.get(
                    view_phase, ("status-draft", "Draft")
                )

                c1, c2 = st.columns([4, 1])
                if c1.button(
                    f"📂 {meta['name']}\n`{created}`",
                    key=f"load_{tid}",
                    use_container_width=True,
                ):
                    st.session_state.thread_id = tid
                    st.session_state.scope_submitted = True
                    st.session_state.chat_history = meta.get("chat_history", [])
                    st.session_state.scope_text_cache = meta.get(
                        "scope_text", meta.get("scope_preview", "")
                    )
                    st.rerun()

                # Status badge rendered as a separate small markdown below the button
                c1.markdown(
                    f'<span class="status-badge {badge_cls}" style="margin-left:4px">'
                    f"{badge_text}</span>",
                    unsafe_allow_html=True,
                )

                if c2.button("🗑️", key=f"del_{tid}", help="Delete"):
                    delete_session(tid)
                    st.rerun()
        else:
            st.caption("No saved audits yet.")

        st.markdown("---")
        st.caption("CC Attribution-NoDerivatives 4.0")
