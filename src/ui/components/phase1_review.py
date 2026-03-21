import io
from collections.abc import Callable

import pandas as pd
import streamlit as st


def render_phase1_review(
    state_vals,
    get_value: Callable,
    on_feedback: Callable[[str], None],
):
    st.info(
        "📋 **Phase 1 Complete** — Review the planning artifacts below, then approve or request changes."
    )

    # ── Active skill badges ────────────────────────────────────────────────────
    skill_names: list = state_vals.get("active_skill_names") or []
    if skill_names:
        pills = "".join(
            f'<span class="skill-pill">🎯 {name}</span>' for name in skill_names
        )
        st.markdown(
            f'<div class="skill-strip"><span style="font-size:12px;color:#6b7280;font-weight:600;'
            f'text-transform:uppercase;letter-spacing:.06em;margin-right:8px;">Skills active:</span>'
            f"{pills}</div>",
            unsafe_allow_html=True,
        )

    # ── Planning artifact tabs ─────────────────────────────────────────────────
    tab1, tab2 = st.tabs(["📄 1-Pager Risk Context", "📋 Control Matrix"])

    with tab1:
        doc = state_vals.get("risk_context_document", "")
        st.markdown(doc or "_No risk context document generated._")

    with tab2:
        matrix = state_vals.get("control_matrix", [])
        for ctrl in matrix:
            cid = get_value(ctrl, "control_id")
            desc = get_value(ctrl, "description")
            procs = get_value(ctrl, "procedures")
            with st.expander(f"**{cid}** — {str(desc)[:80]}…"):
                st.markdown(f"**Domain:** {get_value(ctrl, 'domain')}")
                if procs:
                    tod = get_value(procs, "tod_steps") or []
                    toe = get_value(procs, "toe_steps") or []
                    sub = get_value(procs, "substantive_steps") or []
                    erl = get_value(procs, "erl_items") or []
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown("**🔵 Test of Design**")
                        for step in tod:
                            st.markdown(f"- {step}")
                        st.markdown("**🟡 Test of Effectiveness**")
                        for step in toe:
                            st.markdown(f"- {step}")
                    with c2:
                        st.markdown("**🔴 Substantive**")
                        for step in sub:
                            st.markdown(f"- {step}")
                        st.markdown("**📎 Evidence Request List**")
                        for step in erl:
                            st.markdown(f"- {step}")

        if matrix:
            st.markdown("---")
            rows, qrows, erows = [], [], []
            for ctrl in matrix:
                cid = get_value(ctrl, "control_id")
                desc = get_value(ctrl, "description")
                procs = get_value(ctrl, "procedures")
                rows.append({"Control ID": cid, "Description": desc})
                if procs:
                    qrows.append(
                        {
                            "Control ID": cid,
                            "TOD Steps": "\n".join(get_value(procs, "tod_steps") or []),
                        }
                    )
                    erows.append(
                        {
                            "ERL": "\n".join(get_value(procs, "erl_items") or []),
                            "Control": cid,
                        }
                    )
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                pd.DataFrame(rows).to_excel(writer, sheet_name="Controls", index=False)
                pd.DataFrame(qrows).to_excel(
                    writer, sheet_name="Questions", index=False
                )
                pd.DataFrame(erows).to_excel(writer, sheet_name="ERL", index=False)
            st.download_button(
                "📥 Download Audit Plan (Excel)",
                buf.getvalue(),
                "audit_plan.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

    # ── Approval action area ───────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### ✅ Ready to proceed?")
    st.caption(
        "Approve to begin evidence collection and test execution, "
        "or request changes to the control matrix before testing starts."
    )

    col_approve, col_revise = st.columns([3, 2])

    with col_approve:
        if st.button(
            "🚀 Approve & Start Testing",
            type="primary",
            use_container_width=True,
            help="Accepts the current control matrix and triggers Phase 2 test execution.",
        ):
            on_feedback("approve")

    with col_revise:
        show_fb = st.toggle("📝 Request Changes", key="p1_show_feedback")

    if show_fb:
        fb_text = st.text_area(
            "What should be revised?",
            placeholder=(
                "e.g. 'The procedures for AC-01 are too generic — "
                "add specific AWS CLI commands and CIS benchmark checks.'"
            ),
            height=100,
            key="p1_feedback_text",
        )
        if st.button("📩 Submit Feedback", use_container_width=True):
            if fb_text.strip():
                on_feedback(fb_text)
            else:
                st.warning("Please describe what should be changed.")
