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
            '<div class="hero-badge">🤖 CrewAI Multi-Agent</div>',
            unsafe_allow_html=True,
        )
        st.title("GRC Audit Swarm")
        st.caption("AI Multi-Agent Audit System")
        st.markdown("---")

        # ── LLM status indicator (matches llm_factory priority: Groq → Gemini → OpenAI) ──
        if os.environ.get("GROQ_API_KEY"):
            st.success("✅ Groq llama-3.3-70b-versatile")
        elif os.environ.get("GEMINI_API_KEY"):
            st.success("✅ Gemini 2.0 Flash")
        elif os.environ.get("OPENAI_API_KEY"):
            st.success("✅ OpenAI GPT-4o-mini")
        else:
            st.info("ℹ️ No LLM key — mock mode")

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
                    st.session_state.clear()
                    st.session_state.load_session_id = tid
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
