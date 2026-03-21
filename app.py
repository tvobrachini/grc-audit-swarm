"""
GRC Audit Swarm — Streamlit Application
=========================================
Phase 1: AI Planning (Orchestrator → Researcher → Mapper → Specialist → Challenger → Human)
Phase 2: AI Execution (Workers per control → Concluder → Human)
"""

import os
import sys
import uuid
import io
import streamlit as st
import pandas as pd
import pdfplumber
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

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

from ui.components.styles import inject_swarm_css  # noqa: E402
from ui.components.sidebar import render_sidebar  # noqa: E402
from ui.components.state import initialize_session_state  # noqa: E402

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


def lab_files(ext=None):
    if not os.path.exists(LAB_DIR):
        return []
    f = [x for x in os.listdir(LAB_DIR) if os.path.isfile(os.path.join(LAB_DIR, x))]
    return [x for x in f if x.endswith(ext)] if ext else f


def _get(obj, key, default=""):
    return (
        getattr(obj, key, None)
        or (obj.get(key, default) if isinstance(obj, dict) else default)
        or default
    )


def step_badge(result):
    if not result:
        return "—"
    icons = {"Pass": "✅", "Fail": "❌", "Exception": "⚠️"}
    return icons.get(result, result)


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
    st.markdown(
        "Submit a scope narrative and let the AI swarm research, map controls, execute tests, and present findings for your review."
    )
    scope_text = ""
    colA, colB = st.columns(2)
    with colA:
        st.markdown("### 📄 Upload Scope")
        up = st.file_uploader("PDF or TXT", type=["pdf", "txt"], key="scope_up")
        if up:
            if up.name.endswith(".pdf"):
                with pdfplumber.open(up) as pdf:
                    scope_text = "\n".join(
                        [p.extract_text() for p in pdf.pages if p.extract_text()]
                    )
            else:
                try:
                    scope_text = up.getvalue().decode("utf-8")
                except UnicodeDecodeError:
                    st.error(
                        "File encoding not supported. Please upload a UTF-8 encoded file."
                    )
                    scope_text = ""
        labs = lab_files(".txt")
        sel = st.selectbox("Or Lab Data", ["None"] + labs, key="scope_lab")
        if sel != "None" and not scope_text:
            _resolved = os.path.realpath(os.path.join(LAB_DIR, sel))
            if not _resolved.startswith(os.path.realpath(LAB_DIR) + os.sep):
                st.error("Invalid file selection.")
            else:
                with open(_resolved, "r", encoding="utf-8") as f:
                    scope_text = f.read()
    with colB:
        st.markdown("### 🔍 Preview")
        if scope_text:
            st.text_area(
                "Scope (Editable)", value=scope_text, height=200, key="scope_ta"
            )
            scope_text = st.session_state.scope_ta
        else:
            st.info("Upload or select lab data to see the scope.")

    # Auto-suggest name when scope changes
    if scope_text:
        suggestion = _suggest_audit_name(scope_text)
        if suggestion and st.session_state.suggested_audit_name != suggestion:
            st.session_state.suggested_audit_name = suggestion

    st.markdown("---")
    audit_name = st.text_input(
        "📝 Audit Name",
        value=st.session_state.suggested_audit_name,
        placeholder="e.g. AWS Prod Q4 2026 – IAM Review",
        key="audit_name",
        help="Auto-suggested from scope — edit freely before launching.",
    )
    _, c, _ = st.columns([1, 2, 1])
    if c.button("🚀 Launch Swarm", type="primary", use_container_width=True):
        if not scope_text:
            st.warning("Please provide scope text.")
        else:
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

# ══════════════════════════════════════════════════════════════════════════════
# ACTIVE SESSION
# ══════════════════════════════════════════════════════════════════════════════
else:
    # New Audit button
    _, col_reset = st.columns([5, 1])
    if col_reset.button("🔄 New Audit", use_container_width=True):
        st.session_state.update(
            {
                "thread_id": str(uuid.uuid4()),
                "chat_history": [],
                "scope_submitted": False,
                "control_feedback": {},
            }
        )
        st.rerun()

    current_state = swarm_app.get_state(config)
    next_nodes = current_state.next
    state_vals = current_state.values or {}

    # Gate detection
    at_phase1_review = next_nodes and next_nodes[0] == "human_review"
    at_phase2_review = next_nodes and next_nodes[0] == "human_review_execution"
    at_running = (
        not next_nodes
        and not state_vals.get("testing_findings")
        and state_vals.get("audit_scope_narrative", "") == ""
    )
    is_fully_done = not next_nodes and bool(state_vals.get("testing_findings"))

    # Accurate current phase label
    has_matrix = bool(state_vals.get("control_matrix"))
    has_findings = bool(state_vals.get("testing_findings"))
    is_phase1 = not has_matrix or (
        has_matrix and not has_findings and not at_phase2_review
    )

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
    if (
        not at_phase1_review and not at_phase2_review and not is_fully_done
    ) or st.session_state.get("resume_swarm"):
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
        st.info(
            "📋 **Phase 1 Complete** — Review the planning artifacts below, then approve or give feedback."
        )

        tab1, tab2 = st.tabs(["📄 1-Pager Risk Context", "📋 Control Matrix"])

        with tab1:
            doc = state_vals.get("risk_context_document", "")
            st.markdown(doc or "_No risk context document generated._")

        with tab2:
            matrix = state_vals.get("control_matrix", [])
            for ctrl in matrix:
                cid = _get(ctrl, "control_id")
                desc = _get(ctrl, "description")
                procs = _get(ctrl, "procedures")
                with st.expander(f"**{cid}** — {str(desc)[:80]}…"):
                    st.markdown(f"**Domain:** {_get(ctrl, 'domain')}")
                    if procs:
                        tod = _get(procs, "tod_steps") or []
                        toe = _get(procs, "toe_steps") or []
                        sub = _get(procs, "substantive_steps") or []
                        erl = _get(procs, "erl_items") or []
                        c1, c2 = st.columns(2)
                        with c1:
                            st.markdown("**🔵 Test of Design**")
                            for s in tod:
                                st.markdown(f"- {s}")
                            st.markdown("**🟡 Test of Effectiveness**")
                            for s in toe:
                                st.markdown(f"- {s}")
                        with c2:
                            st.markdown("**🔴 Substantive**")
                            for s in sub:
                                st.markdown(f"- {s}")
                            st.markdown("**📎 Evidence Request List**")
                            for s in erl:
                                st.markdown(f"- {s}")

            # Excel download
            if matrix:
                st.markdown("---")
                rows, qrows, erows = [], [], []
                for ctrl in matrix:
                    cid = _get(ctrl, "control_id")
                    desc = _get(ctrl, "description")
                    procs = _get(ctrl, "procedures")
                    rows.append({"Control ID": cid, "Description": desc})
                    if procs:
                        qrows.append(
                            {
                                "Control ID": cid,
                                "TOD Steps": "\n".join(_get(procs, "tod_steps") or []),
                            }
                        )
                        erows.append(
                            {
                                "ERL": "\n".join(_get(procs, "erl_items") or []),
                                "Control": cid,
                            }
                        )
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as w:
                    pd.DataFrame(rows).to_excel(w, sheet_name="Controls", index=False)
                    pd.DataFrame(qrows).to_excel(w, sheet_name="Questions", index=False)
                    pd.DataFrame(erows).to_excel(w, sheet_name="ERL", index=False)
                st.download_button(
                    "📥 Download Audit Plan (Excel)",
                    buf.getvalue(),
                    "audit_plan.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )

        st.markdown("---")
        fb = st.chat_input(
            "Type 'Approve to start execution' or describe what to change..."
        )
        if fb:
            _append_chat_message("user", fb)
            swarm_app.update_state(config, build_phase1_review_patch(fb))
            st.session_state.resume_swarm = True
            st.rerun()

    # ════════════════════════════════════════════════════
    # PHASE 2 HUMAN REVIEW — Findings Command Center
    # ════════════════════════════════════════════════════
    elif at_phase2_review:
        findings = state_vals.get("testing_findings", [])
        summary = state_vals.get("executive_summary", "")

        # --- KPI Bar ---
        total = len(findings)
        passes = sum(1 for f in findings if _get(f, "status") == "Pass")
        excepts = sum(1 for f in findings if _get(f, "status") == "Exception")
        fails = sum(1 for f in findings if _get(f, "status") == "Fail")

        st.markdown("## ⚙️ Phase 2 — Findings Command Center")

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("🔬 Controls Tested", total)
        k2.metric("✅ Pass", passes)
        k3.metric("⚠️ Exception", excepts)
        k4.metric("❌ Fail", fails)

        # --- Filter bar ---
        st.markdown("---")
        col_f1, col_f2, _ = st.columns([1, 1, 2])
        status_filter = col_f1.selectbox(
            "Filter by Status",
            ["All", "Pass", "Exception", "Fail"],
            key="status_filter",
        )
        shown = (
            findings
            if status_filter == "All"
            else [f for f in findings if _get(f, "status") == status_filter]
        )

        # --- Executive Summary ---
        if summary:
            with st.expander("📊 Executive Summary", expanded=True):
                st.markdown(summary)

        st.markdown("---")
        st.markdown("### 📋 Control Findings — Click to Expand")
        st.caption(
            "Review each control, leave targeted feedback, then submit all at the bottom."
        )

        # --- Findings list with inline expand ---
        has_pending_feedback = False
        for finding in shown:
            cid = _get(finding, "control_id")
            status = _get(finding, "status")
            risk = _get(finding, "risk_rating") or "N/A"
            just = _get(finding, "justification")
            evids = _get(finding, "evidence_extracted") or []
            tod_r = _get(finding, "tod_result")
            toe_r = _get(finding, "toe_result")
            sub_r = _get(finding, "substantive_result")

            icon = {"Pass": "✅", "Exception": "⚠️", "Fail": "❌"}.get(status, "❓")
            color_class = {
                "Pass": "finding-pass",
                "Exception": "finding-exception",
                "Fail": "finding-fail",
            }.get(status, "")

            label = f"{icon} **{cid}** — {status}"
            if risk not in ("N/A", None, ""):
                label += f" · Risk: **{risk}**"

            with st.expander(label, expanded=(status in ["Fail", "Exception"])):
                # Test step results row
                r1, r2, r3 = st.columns(3)
                r1.markdown(f"**TOD:** {step_badge(tod_r)}")
                r2.markdown(f"**TOE:** {step_badge(toe_r)}")
                r3.markdown(f"**Substantive:** {step_badge(sub_r)}")
                st.markdown("---")

                # Finding narrative
                st.markdown(f"**🔍 Finding:**  \n{just}")

                # Evidence
                if evids:
                    with st.expander("📎 Evidence Extracted"):
                        for e in evids:
                            st.markdown(f"- {e}")

                st.markdown("---")

                # ── Per-control feedback ───────────────────────────────────
                fb_key = f"fb_{cid}"
                existing_fb = st.session_state.control_feedback.get(cid, "")
                new_fb = st.text_area(
                    f"💬 Your notes / context for {cid}:",
                    value=existing_fb,
                    placeholder="e.g. 'The 3 flagged users were in the Nov 30 offboarding batch — please verify against HR list before marking as exception.'",
                    height=90,
                    key=fb_key,
                )
                st.session_state.control_feedback[cid] = new_fb

                if new_fb.strip():
                    has_pending_feedback = True

                # Quick action buttons
                col_a, col_b, col_c = st.columns(3)
                if col_a.button("✅ Mark Clean", key=f"clean_{cid}"):
                    swarm_app.update_state(
                        config,
                        mark_control_clean(state_vals.get("execution_status"), cid),
                    )
                    st.rerun()
                if col_b.button("🚩 Flag as Finding", key=f"flag_{cid}"):
                    swarm_app.update_state(
                        config,
                        flag_control_for_finding(
                            state_vals.get("execution_status"), cid
                        ),
                    )
                    st.rerun()
                if col_c.button("🔁 Re-test with My Context", key=f"retest_{cid}"):
                    swarm_app.update_state(
                        config,
                        request_control_rerun(
                            state_vals.get("control_feedback"), cid, new_fb
                        ),
                    )
                    st.session_state.resume_swarm = True
                    st.rerun()

        # --- Bottom submit bar ---
        st.markdown("---")
        c_sub1, c_sub2, c_sub3 = st.columns([1, 2, 1])

        with c_sub2:
            if has_pending_feedback:
                st.warning(
                    f"📝 You have notes on {sum(1 for v in st.session_state.control_feedback.values() if v.strip())} control(s). Submit to let the swarm re-evaluate those tests."
                )
                if st.button(
                    "🔁 Submit Feedback & Re-run Flagged Tests",
                    type="primary",
                    use_container_width=True,
                ):
                    swarm_app.update_state(
                        config,
                        submit_phase2_feedback(st.session_state.control_feedback),
                    )
                    _append_chat_message(
                        "user",
                        f"Submitted feedback on {len(st.session_state.control_feedback)} controls for re-evaluation.",
                    )
                    st.session_state.control_feedback = {}
                    st.session_state.resume_swarm = True
                    st.rerun()
            else:
                st.success("All controls reviewed.")

            if st.button(
                "✅ Approve & Finalize Audit Report",
                type="primary",
                use_container_width=True,
            ):
                swarm_app.update_state(config, {"control_feedback": {}})
                _append_chat_message(
                    "user", "✅ Audit approved. Final report generated."
                )
                st.session_state.resume_swarm = True
                st.rerun()

        # Excel export of findings
        st.markdown("---")
        if findings:
            rows = []
            for f in findings:
                rows.append(
                    {
                        "Control ID": _get(f, "control_id"),
                        "Status": _get(f, "status"),
                        "Risk": _get(f, "risk_rating") or "N/A",
                        "TOD": _get(f, "tod_result") or "—",
                        "TOE": _get(f, "toe_result") or "—",
                        "Substantive": _get(f, "substantive_result") or "—",
                        "Finding": _get(f, "justification"),
                        "Evidence": " | ".join(_get(f, "evidence_extracted") or []),
                    }
                )
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as w:
                pd.DataFrame(rows).to_excel(w, sheet_name="Findings", index=False)
            st.download_button(
                "📥 Download Findings Report (Excel)",
                buf.getvalue(),
                "audit_findings.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

    # ════════════════════════════════════════════════════
    # FULLY DONE
    # ════════════════════════════════════════════════════
    elif is_fully_done:
        findings = state_vals.get("testing_findings", [])
        passes = sum(1 for f in findings if _get(f, "status") == "Pass")
        fails = sum(1 for f in findings if _get(f, "status") == "Fail")

        st.success(
            f"✅ **Audit Complete!** {passes} controls passed · {fails} controls failed."
        )
        st.balloons()

        summary = state_vals.get("executive_summary", "")
        if summary:
            st.markdown("### 📊 Executive Summary")
            st.markdown(summary)

        if st.button("🆕 Start New Audit", type="primary"):
            st.session_state.update(
                {
                    "thread_id": str(uuid.uuid4()),
                    "chat_history": [],
                    "scope_submitted": False,
                    "control_feedback": {},
                }
            )
            st.rerun()
