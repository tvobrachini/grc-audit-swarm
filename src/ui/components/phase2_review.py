import io
from collections.abc import Callable

import pandas as pd
import streamlit as st


def render_phase2_review(
    state_vals,
    session_control_feedback,
    get_value: Callable,
    step_badge: Callable,
    on_mark_clean: Callable[[str], None],
    on_flag_finding: Callable[[str], None],
    on_rerun_control: Callable[[str, str], None],
    on_submit_feedback: Callable[[dict[str, str]], None],
    on_finalize: Callable[[], None],
):
    findings = state_vals.get("testing_findings", [])
    summary = state_vals.get("executive_summary", "")

    total = len(findings)
    passes = sum(1 for finding in findings if get_value(finding, "status") == "Pass")
    excepts = sum(
        1 for finding in findings if get_value(finding, "status") == "Exception"
    )
    fails = sum(1 for finding in findings if get_value(finding, "status") == "Fail")

    st.markdown("## ⚙️ Phase 2 — Findings Command Center")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("🔬 Controls Tested", total)
    k2.metric("✅ Pass", passes)
    k3.metric("⚠️ Exception", excepts)
    k4.metric("❌ Fail", fails)

    st.markdown("---")
    col_f1, _, _ = st.columns([1, 1, 2])
    status_filter = col_f1.selectbox(
        "Filter by Status",
        ["All", "Pass", "Exception", "Fail"],
        key="status_filter",
    )
    shown = (
        findings
        if status_filter == "All"
        else [
            finding
            for finding in findings
            if get_value(finding, "status") == status_filter
        ]
    )

    if summary:
        with st.expander("📊 Executive Summary", expanded=True):
            st.markdown(summary)

    st.markdown("---")
    st.markdown("### 📋 Control Findings — Click to Expand")
    st.caption(
        "Review each control, leave targeted feedback, then submit all at the bottom."
    )

    has_pending_feedback = False
    for finding in shown:
        cid = get_value(finding, "control_id")
        status = get_value(finding, "status")
        risk = get_value(finding, "risk_rating") or "N/A"
        justification = get_value(finding, "justification")
        evids = get_value(finding, "evidence_extracted") or []
        tod_r = get_value(finding, "tod_result")
        toe_r = get_value(finding, "toe_result")
        sub_r = get_value(finding, "substantive_result")

        icon = {"Pass": "✅", "Exception": "⚠️", "Fail": "❌"}.get(status, "❓")
        label = f"{icon} **{cid}** — {status}"
        if risk not in ("N/A", None, ""):
            label += f" · Risk: **{risk}**"

        with st.expander(label, expanded=(status in ["Fail", "Exception"])):
            r1, r2, r3 = st.columns(3)
            r1.markdown(f"**TOD:** {step_badge(tod_r)}")
            r2.markdown(f"**TOE:** {step_badge(toe_r)}")
            r3.markdown(f"**Substantive:** {step_badge(sub_r)}")
            st.markdown("---")

            st.markdown(f"**🔍 Finding:**  \n{justification}")

            if evids:
                with st.expander("📎 Evidence Extracted"):
                    for evidence in evids:
                        st.markdown(f"- {evidence}")

            st.markdown("---")

            fb_key = f"fb_{cid}"
            existing_fb = session_control_feedback.get(cid, "")
            new_fb = st.text_area(
                f"💬 Your notes / context for {cid}:",
                value=existing_fb,
                placeholder="e.g. 'The 3 flagged users were in the Nov 30 offboarding batch — please verify against HR list before marking as exception.'",
                height=90,
                key=fb_key,
            )
            normalized_fb = new_fb or ""
            session_control_feedback[cid] = normalized_fb

            if normalized_fb.strip():
                has_pending_feedback = True

            col_a, col_b, col_c = st.columns(3)
            if col_a.button("✅ Mark Clean", key=f"clean_{cid}"):
                on_mark_clean(cid)
            if col_b.button("🚩 Flag as Finding", key=f"flag_{cid}"):
                on_flag_finding(cid)
            if col_c.button("🔁 Re-test with My Context", key=f"retest_{cid}"):
                on_rerun_control(cid, normalized_fb)

    st.markdown("---")
    _, c_sub2, _ = st.columns([1, 2, 1])

    with c_sub2:
        if has_pending_feedback:
            pending_count = sum(
                1 for value in session_control_feedback.values() if value.strip()
            )
            st.warning(
                f"📝 You have notes on {pending_count} control(s). Submit to let the swarm re-evaluate those tests."
            )
            if st.button(
                "🔁 Submit Feedback & Re-run Flagged Tests",
                type="primary",
                use_container_width=True,
            ):
                on_submit_feedback(session_control_feedback)
        else:
            st.success("All controls reviewed.")

        if st.button(
            "✅ Approve & Finalize Audit Report",
            type="primary",
            use_container_width=True,
        ):
            on_finalize()

    st.markdown("---")
    if findings:
        rows = []
        for finding in findings:
            rows.append(
                {
                    "Control ID": get_value(finding, "control_id"),
                    "Status": get_value(finding, "status"),
                    "Risk": get_value(finding, "risk_rating") or "N/A",
                    "TOD": get_value(finding, "tod_result") or "—",
                    "TOE": get_value(finding, "toe_result") or "—",
                    "Substantive": get_value(finding, "substantive_result") or "—",
                    "Finding": get_value(finding, "justification"),
                    "Evidence": " | ".join(
                        get_value(finding, "evidence_extracted") or []
                    ),
                }
            )
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            pd.DataFrame(rows).to_excel(writer, sheet_name="Findings", index=False)
        st.download_button(
            "📥 Download Findings Report (Excel)",
            buf.getvalue(),
            "audit_findings.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
