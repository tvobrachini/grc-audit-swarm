import io
from collections.abc import Callable
import pandas as pd
import streamlit as st


def render_phase1_review(
    racm_plan: dict,
    on_feedback: Callable[[str], None],
):
    if not racm_plan:
        st.warning("No RACM Plan available.")
        return

    st.info(
        "📋 **Phase 1 Complete** — Review the Risk Control Matrix below, then approve or request changes."
    )

    theme = racm_plan.get("theme", "Unknown Theme")
    st.markdown(f"### Theme: {theme}")

    risks = racm_plan.get("risks", [])

    rows = []
    for risk in risks:
        r_id = risk.get("risk_id", "")
        r_desc = risk.get("description", "")
        r_regs = ", ".join(risk.get("regulatory_mapping", []))

        st.markdown(f"#### 🚨 Risk: {r_id} ({r_regs})")
        st.markdown(r_desc)

        controls = risk.get("controls", [])
        for ctrl in controls:
            c_id = ctrl.get("control_id", "")
            c_desc = ctrl.get("description", "")
            with st.expander(f"**{c_id}** — {str(c_desc)[:80]}…"):
                st.markdown(c_desc)
                procs = ctrl.get("testing_procedures", {})

                tod = procs.get("test_of_design", [])
                toe = procs.get("test_of_effectiveness", [])
                sub = procs.get("substantive_testing") or []

                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("**🔵 Test of Design**")
                    for step in tod:
                        st.markdown(
                            f"- {step.get('step_description')} (Expect: {step.get('expected_result')})"
                        )
                    st.markdown("**🟡 Test of Effectiveness**")
                    for step in toe:
                        st.markdown(
                            f"- {step.get('step_description')} (Expect: {step.get('expected_result')})"
                        )
                with c2:
                    st.markdown("**🔴 Substantive Testing**")
                    for step in sub:
                        st.markdown(
                            f"- {step.get('step_description')} (Expect: {step.get('expected_result')})"
                        )

                # Build excel rows with full testing procedure detail
                def _steps(step_list):
                    return "; ".join(
                        f"{s.get('step_description')} (Expect: {s.get('expected_result')})"
                        for s in step_list
                    )

                rows.append(
                    {
                        "Risk ID": r_id,
                        "Regulatory Mapping": r_regs,
                        "Control ID": c_id,
                        "Description": c_desc,
                        "ToD Steps": _steps(tod),
                        "ToE Steps": _steps(toe),
                        "Substantive Steps": _steps(sub),
                    }
                )

    if rows:
        st.markdown("---")
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            pd.DataFrame(rows).to_excel(writer, sheet_name="Controls", index=False)
        st.download_button(
            "📥 Download RACM Matrix (Excel)",
            buf.getvalue(),
            "racm_matrix.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    st.markdown("---")
    st.markdown("### ✅ Ready to proceed?")
    col_approve, col_revise = st.columns([3, 2])

    with col_approve:
        if st.button(
            "🚀 Approve & Target Collection (IIA 2340 Stamping)",
            type="primary",
            use_container_width=True,
        ):
            on_feedback("approve")

    with col_revise:
        show_fb = st.toggle("📝 Request Changes", key="p1_show_feedback")

    if show_fb:
        fb_text = st.text_area("What should be revised in the RACM?", height=100)
        if st.button("📩 Submit Feedback", use_container_width=True):
            on_feedback(fb_text)
