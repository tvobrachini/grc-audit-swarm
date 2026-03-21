"""
GRC Audit Swarm — Streamlit Application
=========================================
Phase 1: AI Planning (Orchestrator → Researcher → Mapper → Specialist → Challenger → Human)
Phase 2: AI Execution (Workers per control → Concluder → Human)
"""

import os
import sys
import uuid
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

from swarm.app_flow import derive_app_view_state  # noqa: E402
from swarm.graph import app as swarm_app  # noqa: E402
from swarm.review_actions import (  # noqa: E402
    build_phase1_review_patch,
    flag_control_for_finding,
    mark_control_clean,
    request_control_rerun,
    submit_phase2_feedback,
)
from swarm.session_manager import save_session, update_session  # noqa: E402
from swarm.session_sync import append_chat_message, build_session_update  # noqa: E402

from ui.components.common import get_value, step_badge  # noqa: E402
from ui.components.phase1_review import render_phase1_review  # noqa: E402
from ui.components.phase2_review import render_phase2_review  # noqa: E402
from ui.components.scope_input import render_scope_input  # noqa: E402
from ui.components.styles import inject_swarm_css  # noqa: E402
from ui.components.sidebar import render_sidebar  # noqa: E402
from ui.components.state import initialize_session_state, reset_session_state  # noqa: E402

# ─── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="GRC Audit Swarm",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Initialization ───────────────────────────────────────────────────────────
inject_swarm_css()
render_sidebar()
initialize_session_state()

config = {"configurable": {"thread_id": st.session_state.thread_id}}
LAB_DIR = os.path.join(os.path.dirname(__file__), "lab_data")


def _append_chat_message(role: str, content: str, reasoning=None):
    st.session_state.chat_history = append_chat_message(
        st.session_state.chat_history,
        role,
        content,
        reasoning=reasoning,
    )
    update_session(
        st.session_state.thread_id,
        **build_session_update(
            st.session_state.scope_text_cache, st.session_state.chat_history
        ),
    )


def _suggest_audit_name(scope_text: str) -> str:
    """Extract a concise audit name from the first meaningful line of the scope."""
    import re

    for line in scope_text.splitlines():
        line = line.strip().strip("-=")
        # Skip banners, blanks, and field labels like 'Organization:'
        if (
            not line
            or len(line) < 8
            or re.match(r"^(organization|prepared|audit period|period):", line, re.I)
        ):
            continue
        # Strip AUDIT/SCOPE/NARRATIVE boilerplate prefixes
        line = re.sub(
            r"^(AUDIT SCOPE NARRATIVE[\s\—\-–]+|AUDIT SCOPE[\s\—\-–]+|SCOPE[:\s]+)",
            "",
            line,
            flags=re.I,
        ).strip()
        if line:
            # Trim if very long
            return line[:80].strip()
    return ""


# ─── Header ────────────────────────────────────────────────────────────────────
st.title("🎯 Swarm Audit Command Center")

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 0 — Scope Input
# ══════════════════════════════════════════════════════════════════════════════
if not st.session_state.scope_submitted:

    def _handle_scope_suggestion(suggestion: str):
        st.session_state.suggested_audit_name = suggestion

    def _handle_scope_launch(scope_text: str, audit_name: str):
        name = audit_name.strip() or f"Audit {st.session_state.thread_id[:8]}"
        st.session_state.scope_text_cache = scope_text
        st.session_state.scope_submitted = True
        _append_chat_message("user", f"**🚀 {name}** — Scope loaded.")
        save_session(
            st.session_state.thread_id,
            name,
            scope_text,
            st.session_state.chat_history,
        )
        st.rerun()

    render_scope_input(
        lab_dir=LAB_DIR,
        suggested_audit_name=st.session_state.suggested_audit_name,
        suggest_audit_name=_suggest_audit_name,
        on_scope_change=_handle_scope_suggestion,
        on_launch=_handle_scope_launch,
    )

# ══════════════════════════════════════════════════════════════════════════════
# ACTIVE SESSION
# ══════════════════════════════════════════════════════════════════════════════
else:
    # New Audit button
    _, col_reset = st.columns([5, 1])
    if col_reset.button("🔄 New Audit", use_container_width=True):
        reset_session_state(str(uuid.uuid4()))
        st.rerun()

    current_state = swarm_app.get_state(config)
    view_state = derive_app_view_state(current_state)
    state_vals = view_state["state_vals"]
    at_phase1_review = view_state["at_phase1_review"]
    at_phase2_review = view_state["at_phase2_review"]
    is_fully_done = view_state["is_fully_done"]
    is_phase1 = view_state["is_phase1"]

    # ── Agent Progress Rail (collapsible) ─────────────────────────────────────
    if st.session_state.chat_history:
        with st.expander(
            "📡 Agent Activity Log",
            expanded=(not at_phase2_review and not is_fully_done),
        ):
            for msg in st.session_state.chat_history:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])
                    if msg.get("reasoning"):
                        with st.expander("🔍 Reasoning"):
                            st.markdown(msg["reasoning"])

    # ════════════════════════════════════════════════════
    # RUNNING — stream agent nodes
    # ════════════════════════════════════════════════════
    if view_state["should_stream"] or st.session_state.get("resume_swarm"):
        st.session_state.resume_swarm = False
        if is_phase1:
            spinner_msg = "🔍 Phase 1: Swarm researching and planning..."
        else:
            spinner_msg = "⚙️ Phase 2: Swarm executing tests and analyzing findings..."

        with st.spinner(spinner_msg):
            stream_input = (
                {
                    "audit_scope_narrative": st.session_state.scope_text_cache,
                    "audit_trail": [],
                }
                if not state_vals
                else None
            )
            for event in swarm_app.stream(
                stream_input, config=config, stream_mode="updates"
            ):
                # s is the node update dictionary. LangGraph stream can sometimes return tuples for internals.
                for node, s in event.items():
                    if not isinstance(s, dict):
                        continue
                    reasoning = None
                    if s.get("audit_trail"):
                        last = s["audit_trail"][-1]
                        reasoning = (
                            last.get("reasoning_snapshot")
                            if isinstance(last, dict)
                            else getattr(last, "reasoning_snapshot", None)
                        )
                    txt = f"🟢 **`{node}`** completed"
                    _append_chat_message("assistant", txt, reasoning=reasoning)
            st.rerun()

    # ════════════════════════════════════════════════════
    # PHASE 1 HUMAN REVIEW — Planning artifacts
    # ════════════════════════════════════════════════════
    elif at_phase1_review:

        def _handle_phase1_feedback(feedback: str):
            _append_chat_message("user", feedback)
            swarm_app.update_state(config, build_phase1_review_patch(feedback))
            st.session_state.resume_swarm = True
            st.rerun()

        render_phase1_review(state_vals, get_value, _handle_phase1_feedback)

    # ════════════════════════════════════════════════════
    # PHASE 2 HUMAN REVIEW — Findings Command Center
    # ════════════════════════════════════════════════════
    elif at_phase2_review:

        def _handle_mark_clean(control_id: str):
            swarm_app.update_state(
                config,
                mark_control_clean(state_vals.get("execution_status"), control_id),
            )
            st.rerun()

        def _handle_flag_finding(control_id: str):
            swarm_app.update_state(
                config,
                flag_control_for_finding(
                    state_vals.get("execution_status"), control_id
                ),
            )
            st.rerun()

        def _handle_rerun_control(control_id: str, feedback: str):
            swarm_app.update_state(
                config,
                request_control_rerun(
                    state_vals.get("control_feedback"), control_id, feedback
                ),
            )
            st.session_state.resume_swarm = True
            st.rerun()

        def _handle_submit_feedback(control_feedback: dict[str, str]):
            swarm_app.update_state(config, submit_phase2_feedback(control_feedback))
            _append_chat_message(
                "user",
                f"Submitted feedback on {len(control_feedback)} controls for re-evaluation.",
            )
            st.session_state.control_feedback = {}
            st.session_state.resume_swarm = True
            st.rerun()

        def _handle_finalize():
            swarm_app.update_state(config, {"control_feedback": {}})
            _append_chat_message("user", "✅ Audit approved. Final report generated.")
            st.session_state.resume_swarm = True
            st.rerun()

        render_phase2_review(
            state_vals=state_vals,
            session_control_feedback=st.session_state.control_feedback,
            get_value=get_value,
            step_badge=step_badge,
            on_mark_clean=_handle_mark_clean,
            on_flag_finding=_handle_flag_finding,
            on_rerun_control=_handle_rerun_control,
            on_submit_feedback=_handle_submit_feedback,
            on_finalize=_handle_finalize,
        )

    # ════════════════════════════════════════════════════
    # FULLY DONE
    # ════════════════════════════════════════════════════
    elif is_fully_done:
        findings = state_vals.get("testing_findings", [])
        passes = sum(1 for f in findings if get_value(f, "status") == "Pass")
        fails = sum(1 for f in findings if get_value(f, "status") == "Fail")

        st.success(
            f"✅ **Audit Complete!** {passes} controls passed · {fails} controls failed."
        )
        st.balloons()

        summary = state_vals.get("executive_summary", "")
        if summary:
            st.markdown("### 📊 Executive Summary")
            st.markdown(summary)

        if st.button("🆕 Start New Audit", type="primary"):
            reset_session_state(str(uuid.uuid4()))
            st.rerun()
