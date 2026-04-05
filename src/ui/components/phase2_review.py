import io
from collections.abc import Callable
import pandas as pd
import streamlit as st

from swarm.evidence import EvidenceAssuranceProtocol


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

        # Deterministic vault verification
        quote_verified: bool | None = None
        if vault_id and quote:
            quote_verified = EvidenceAssuranceProtocol.verify_exact_quote(
                vault_id, quote
            )

        icon = "✅" if sev == "Pass" else "❌" if sev == "Material Weakness" else "⚠️"

        with st.expander(f"{icon} **{cid}** — {sev}", expanded=(sev != "Pass")):
            st.markdown(f"**Conclusion:** {conclusion}")

            col_vault, col_verify = st.columns([3, 1])
            with col_vault:
                st.markdown(f"**Vault-ID Reference:** `{vault_id}`")
            with col_verify:
                if quote_verified is True:
                    st.success("✅ Quote Verified")
                elif quote_verified is False:
                    st.error("❌ Quote Unverified — potential hallucination")
                else:
                    st.caption("No vault ID to verify")

            st.markdown(f'> *"{quote}"*')

    st.markdown("---")

    # Download working papers as Excel
    if findings:
        rows = []
        for f in findings:
            rows.append(
                {
                    "Control ID": f.get("control_id", ""),
                    "Severity": f.get("severity", ""),
                    "Conclusion": f.get("test_conclusion", ""),
                    "Evidence Quote": f.get("exact_quote_from_evidence", ""),
                    "Vault ID": f.get("vault_id_reference", ""),
                }
            )
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            pd.DataFrame(rows).to_excel(writer, sheet_name="Findings", index=False)
        st.download_button(
            "📥 Download Working Papers (Excel)",
            buf.getvalue(),
            "working_papers.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    st.markdown("---")
    _, c_center, _ = st.columns([1, 2, 1])

    with c_center:
        if st.button(
            "✅ Approve Working Papers & Generate Final Report (IIA 2340)",
            type="primary",
            use_container_width=True,
        ):
            on_finalize()
