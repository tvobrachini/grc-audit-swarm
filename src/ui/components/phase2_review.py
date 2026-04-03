from collections.abc import Callable
import streamlit as st


def render_phase2_review(
    working_papers: dict,
    on_finalize: Callable[[], None],
):
    if not working_papers:
        st.warning("No Working Papers available.")
        return

    findings = working_papers.get("findings", [])
    total = len(findings)

    passes = sum(1 for f in findings if f.get("severity") == "Pass")
    excepts = sum(
        1
        for f in findings
        if f.get("severity") in ("Control Deficiency", "Significant Deficiency")
    )
    fails = sum(1 for f in findings if f.get("severity") == "Material Weakness")

    st.markdown("## ⚙️ Phase 2 — Execution Findings Command Center")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("🔬 Controls Evaluated", total)
    k2.metric("✅ Pass", passes)
    k3.metric("⚠️ Deficiencies", excepts)
    k4.metric("❌ Material Weaknesses", fails)

    st.markdown("---")
    st.markdown("### 📋 Working Papers (IIA 2330 / PCAOB AS 1215 Immutable Vault)")

    for f in findings:
        cid = f.get("control_id")
        sev = f.get("severity", "Unknown")
        quote = f.get("exact_quote_from_evidence", "")
        conclusion = f.get("test_conclusion", "")
        vault_id = f.get("vault_id_reference", "")

        icon = "✅" if sev == "Pass" else "❌" if sev == "Material Weakness" else "⚠️"

        with st.expander(f"{icon} **{cid}** — {sev}", expanded=(sev != "Pass")):
            st.markdown(f"**Conclusion:** {conclusion}")
            st.markdown(f"**Vault-ID Reference:** `{vault_id}`")
            st.markdown(f'> *"{quote}"*')

    st.markdown("---")
    _, c_center, _ = st.columns([1, 2, 1])

    with c_center:
        if st.button(
            "✅ Approve Working Papers & Generate Final Report (IIA 2340)",
            type="primary",
            use_container_width=True,
        ):
            on_finalize()
