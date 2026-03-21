"""
GRC Audit Swarm — Streamlit Application
=========================================
Phase 1: AI Planning (Orchestrator → Researcher → Mapper → Specialist → Challenger → Human)
Phase 2: AI Execution (Workers per control → Concluder → Human)
"""

import io
import os
import sys
import uuid
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

import pandas as pd  # noqa: E402

from swarm.audit_workflow_service import AuditWorkflowService  # noqa: E402
from swarm.graph_service import GraphService  # noqa: E402

from ui.components.common import get_value, step_badge  # noqa: E402
from ui.components.phase1_review import render_phase1_review  # noqa: E402
from ui.components.phase2_review import render_phase2_review  # noqa: E402
from ui.components.progress import render_phase_strip  # noqa: E402
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

graph_service = GraphService()
workflow_service = AuditWorkflowService(graph_service)
config = {"configurable": {"thread_id": st.session_state.thread_id}}
LAB_DIR = os.path.join(os.path.dirname(__file__), "lab_data")


def _append_chat_message(role: str, content: str, reasoning=None):
    workflow_service.append_chat_message(
        st.session_state,
        role,
        content,
        reasoning=reasoning,
    )


def _build_findings_excel(findings) -> bytes:
    rows = [
        {
            "Control ID": get_value(f, "control_id"),
            "Status": get_value(f, "status"),
            "Risk": get_value(f, "risk_rating") or "N/A",
            "TOD": get_value(f, "tod_result") or "—",
            "TOE": get_value(f, "toe_result") or "—",
            "Substantive": get_value(f, "substantive_result") or "—",
            "Finding": get_value(f, "justification"),
            "Evidence": " | ".join(get_value(f, "evidence_extracted") or []),
        }
        for f in findings
    ]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Findings", index=False)
    return buf.getvalue()


# ─── Header ────────────────────────────────────────────────────────────────────
st.title("🎯 Swarm Audit Command Center")

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 0 — Scope Input
# ══════════════════════════════════════════════════════════════════════════════
if not st.session_state.scope_submitted:

    def _handle_scope_suggestion(suggestion: str):
        st.session_state.suggested_audit_name = suggestion

    def _handle_scope_launch(scope_text: str, audit_name: str):
        workflow_service.start_audit(st.session_state, scope_text, audit_name)
        st.rerun()

    render_scope_input(
        lab_dir=LAB_DIR,
        suggested_audit_name=st.session_state.suggested_audit_name,
        on_scope_change=_handle_scope_suggestion,
        on_launch=_handle_scope_launch,
    )

# ══════════════════════════════════════════════════════════════════════════════
# ACTIVE SESSION
# ══════════════════════════════════════════════════════════════════════════════
else:
    # New Audit button (top-right)
    _, col_reset = st.columns([5, 1])
    if col_reset.button("🔄 New Audit", use_container_width=True):
        reset_session_state(str(uuid.uuid4()))
        st.rerun()

    view_state = workflow_service.get_view_state(config)
    workflow_service.sync_state_snapshot(st.session_state, view_state)
    state_vals = view_state["state_vals"]
    at_phase1_review = view_state["at_phase1_review"]
    at_phase2_review = view_state["at_phase2_review"]
    is_fully_done = view_state["is_fully_done"]
    is_phase1 = view_state["is_phase1"]
    view_phase = str(view_state["view_phase"])

    # ── Phase progress strip ───────────────────────────────────────────────────
    render_phase_strip(view_phase)

    # ── Agent Activity Log (collapsible) ───────────────────────────────────────
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
        spinner_msg = (
            "🔍 Phase 1: Swarm researching and planning..."
            if is_phase1
            else "⚙️ Phase 2: Swarm executing tests and analysing findings..."
        )
        with st.spinner(spinner_msg):
            workflow_service.consume_stream(st.session_state, config, state_vals)
            st.rerun()

    # ════════════════════════════════════════════════════
    # PHASE 1 HUMAN REVIEW — Planning artifacts
    # ════════════════════════════════════════════════════
    elif at_phase1_review:

        def _handle_phase1_feedback(feedback: str):
            workflow_service.submit_phase1_review(st.session_state, config, feedback)
            st.rerun()

        render_phase1_review(state_vals, get_value, _handle_phase1_feedback)

    # ════════════════════════════════════════════════════
    # PHASE 2 HUMAN REVIEW — Findings Command Center
    # ════════════════════════════════════════════════════
    elif at_phase2_review:

        def _handle_mark_clean(control_id: str):
            workflow_service.mark_control_clean(
                config, state_vals.get("execution_status"), control_id
            )
            st.rerun()

        def _handle_flag_finding(control_id: str):
            workflow_service.flag_control_for_finding(
                config, state_vals.get("execution_status"), control_id
            )
            st.rerun()

        def _handle_rerun_control(control_id: str, feedback: str):
            workflow_service.rerun_control(
                st.session_state,
                config,
                state_vals.get("control_feedback"),
                control_id,
                feedback,
            )
            st.rerun()

        def _handle_submit_feedback(control_feedback: dict[str, str]):
            workflow_service.submit_phase2_feedback(
                st.session_state, config, control_feedback
            )
            st.rerun()

        def _handle_finalize():
            workflow_service.finalize_audit(st.session_state, config)
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
        exceptions = sum(1 for f in findings if get_value(f, "status") == "Exception")
        fails = sum(1 for f in findings if get_value(f, "status") == "Fail")

        st.success(
            f"**Audit Finalised.** {passes} passed · {exceptions} exceptions · {fails} failed."
        )

        summary = state_vals.get("executive_summary", "")
        if summary:
            st.markdown("### 📊 Executive Summary")
            st.markdown(summary)

        st.markdown("---")

        # ── Actions ───────────────────────────────────────────────────────────
        if findings:
            col_dl, col_new = st.columns(2)
            col_dl.download_button(
                "📥 Download Final Report (Excel)",
                _build_findings_excel(findings),
                "audit_report_final.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
            if col_new.button(
                "🆕 Start New Audit", type="primary", use_container_width=True
            ):
                reset_session_state(str(uuid.uuid4()))
                st.rerun()
        else:
            if st.button("🆕 Start New Audit", type="primary"):
                reset_session_state(str(uuid.uuid4()))
                st.rerun()

        # ── Read-only findings review ──────────────────────────────────────────
        if findings:
            with st.expander("📋 Review All Findings", expanded=False):
                for f in findings:
                    status = get_value(f, "status")
                    cid = get_value(f, "control_id")
                    risk = get_value(f, "risk_rating") or "N/A"
                    justification = get_value(f, "justification") or ""
                    tod_r = get_value(f, "tod_result")
                    toe_r = get_value(f, "toe_result")
                    sub_r = get_value(f, "substantive_result")

                    icon = {"Pass": "✅", "Exception": "⚠️", "Fail": "❌"}.get(
                        status, "❓"
                    )
                    with st.expander(
                        f"{icon} **{cid}** — {status} · Risk: {risk}",
                        expanded=False,
                    ):
                        r1, r2, r3 = st.columns(3)
                        r1.markdown(f"**TOD:** {step_badge(tod_r)}")
                        r2.markdown(f"**TOE:** {step_badge(toe_r)}")
                        r3.markdown(f"**Substantive:** {step_badge(sub_r)}")
                        # Show only the worker finding portion (truncate expert context)
                        worker_text = justification.split("**🔬 Specialist Annotation")[
                            0
                        ].strip()
                        st.markdown(worker_text)
