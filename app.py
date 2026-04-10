"""
GRC Audit Swarm — Streamlit Application (CrewAI Engine)
=========================================
Phase 1: AI Planning Phase (RACM Generation)
Phase 2: AI Execution Phase (Evidence Hashing)
Phase 3: AI Reporting Phase (Tone QA)

Set DEMO_MODE=1 in .env to skip LLM crew execution and use hardcoded schemas.
"""

import streamlit as st
import os
import sys
import uuid

from dotenv import load_dotenv

load_dotenv(override=True)
# google-genai SDK reads GEMINI_API_KEY directly; no remapping needed

sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

from swarm.audit_flow import AuditFlow  # noqa: E402
from swarm.session_manager import save_session, get_session, update_session  # noqa: E402
from ui.components.styles import inject_swarm_css  # noqa: E402
from ui.components.sidebar import render_sidebar  # noqa: E402
from ui.components.phase1_review import render_phase1_review  # noqa: E402
from ui.components.phase2_review import render_phase2_review  # noqa: E402

DEMO_MODE = os.environ.get("DEMO_MODE", "0") == "1"

st.set_page_config(
    page_title="GRC Audit Swarm",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_swarm_css()
render_sidebar()

st.title("🎯 Swarm Audit Command Center (CrewAI Native)")

if DEMO_MODE:
    st.caption("🔧 DEMO MODE — Crews are bypassed; hardcoded schemas are used.")

# ─── Session bootstrap ────────────────────────────────────────────────────────
# When the sidebar loads a saved session, it sets st.session_state.load_session_id.
# We restore the flow state from the saved snapshot before rendering anything else.
if "load_session_id" in st.session_state:
    tid = st.session_state.pop("load_session_id")
    meta = get_session(tid)
    if meta:
        restored = AuditFlow()
        snapshot = meta.get("state_snapshot") or {}
        if snapshot:
            for k, v in snapshot.items():
                try:
                    setattr(restored.state, k, v)
                except Exception:  # nosec B110
                    pass
        st.session_state.flow = restored
        st.session_state.phase = meta.get("ui_phase", 0)
        st.session_state.audit_session_id = tid

if "flow" not in st.session_state:
    st.session_state.flow = AuditFlow()
    st.session_state.phase = 0

if "audit_session_id" not in st.session_state:
    st.session_state.audit_session_id = str(uuid.uuid4())

flow = st.session_state.flow


def _persist_session(name: str = ""):
    """Save current flow state + UI phase to the session store."""
    tid = st.session_state.audit_session_id
    state_snapshot = flow.state.model_dump()
    save_session(
        thread_id=tid,
        name=name or flow.state.theme or "Untitled Audit",
        scope_text=flow.state.business_context,
    )
    update_session(tid, state_snapshot=state_snapshot, ui_phase=st.session_state.phase)


def _show_error_state(phase_label: str):
    """Render an error recovery widget when a crew sets status=ERROR."""
    st.error(
        f"🚨 {phase_label} crew failed: {flow.state.qa_rejection_reason or 'Unknown error'}\n\n"
        "Fix the issue above and click **Retry** to try again."
    )
    if st.button("🔄 Retry"):
        flow.state.status = (
            "WAITING_FOR_SCOPE" if st.session_state.phase == 0 else flow.state.status
        )
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 0 — Scope Input
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.phase == 0:
    st.markdown("### Step 1: Define Target & Business Context")
    theme = st.text_input("Audit Theme", "Public S3 Buckets Exposure")
    context = st.text_area(
        "Context",
        "We are a fintech handling sensitive data. Ensure no buckets are publicly accessible.",
    )

    if st.button("🚀 Launch Execution (CrewAI Planning Phase)", type="primary"):
        if not theme.strip() or not context.strip():
            st.warning("Please fill in both Audit Theme and Context before launching.")
            st.stop()

        flow.state.theme = theme
        flow.state.business_context = context
        flow.state.frameworks = ["Basic Best Practices"]

        if DEMO_MODE:
            with st.spinner("Demo mode: injecting hardcoded RACM..."):
                try:
                    from swarm.schema import (
                        RiskControlMatrixSchema,
                        Risk,
                        Control,
                        ControlTesting,
                        ControlTestStep,
                    )

                    flow.state.racm_plan = RiskControlMatrixSchema(
                        theme="AWS Cloud Security",
                        risks=[
                            Risk(
                                risk_id="RISK-01",
                                description="Unauthorized access to IAM or S3 resources.",
                                regulatory_mapping=["CIS AWS Foundations"],
                                controls=[
                                    Control(
                                        control_id="CTRL-01",
                                        description="Enforce strict IAM Password Policy.",
                                        testing_procedures=ControlTesting(
                                            test_of_design=[
                                                ControlTestStep(
                                                    step_description="Verify that a password policy is defined in IAM.",
                                                    expected_result="Password policy exists and is configured.",
                                                )
                                            ],
                                            test_of_effectiveness=[
                                                ControlTestStep(
                                                    step_description="Run get_iam_password_policy tool.",
                                                    expected_result="Policy conforms to CIS requirements.",
                                                )
                                            ],
                                        ),
                                    )
                                ],
                            )
                        ],
                    ).model_dump()
                    flow.state.status = "WAITING_HUMAN_GATE_1"
                    st.session_state.phase = 1
                    _persist_session()
                    st.rerun()
                except Exception as e:
                    import traceback

                    st.error(f"🚨 Engine Error (Phase 1 Demo): {str(e)}")
                    with st.expander("Show Detailed Error Log", expanded=True):
                        st.code(traceback.format_exc(), language="python")
        else:
            with st.spinner(
                "Planning Phase: AI crew is building the Risk Control Matrix..."
            ):
                try:
                    flow.generate_planning()

                    if flow.state.status == "ERROR":
                        _show_error_state("Planning")
                    elif flow.state.status == "QA_REJECTED_PHASE_1":
                        st.error(
                            f"🚫 QA Gate rejected the RACM: {flow.state.qa_rejection_reason}\n\n"
                            "Adjust your scope and try again."
                        )
                    else:
                        st.session_state.phase = 1
                        _persist_session()
                        st.rerun()
                except Exception as e:
                    import traceback

                    st.error(f"🚨 Engine Error (Phase 1): {str(e)}")
                    with st.expander("Show Detailed Error Log", expanded=True):
                        st.code(traceback.format_exc(), language="python")

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — Planning Phase Gate (RACM)
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.phase == 1:

    def on_phase1_feedback(command: str):
        if command != "approve":
            st.session_state["p1_feedback_submitted"] = command
            st.rerun()
            return

        if DEMO_MODE:
            with st.spinner("Demo mode: injecting hardcoded working papers..."):
                try:
                    from swarm.schema import WorkingPaperSchema, AuditFindingSchema
                    from datetime import datetime

                    flow.state.working_papers = WorkingPaperSchema(
                        theme="AWS Cloud Security",
                        findings=[
                            AuditFindingSchema(
                                control_id="CTRL-01",
                                vault_id_reference="demo-vault-0000-0000-0000",
                                exact_quote_from_evidence="PasswordPolicy: MinimumPasswordLength=14",
                                test_conclusion="IAM Password Policy meets CIS AWS Foundations requirements.",
                                severity="Pass",
                            )
                        ],
                    ).model_dump()

                    flow.state.approval_trail.append(
                        {
                            "gate": "Gate 1 (Planning)",
                            "human": "AUDITOR_UI_001",
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                    )
                    flow.state.status = "WAITING_HUMAN_GATE_2"
                    st.session_state.phase = 2
                    _persist_session()
                    st.rerun()
                except Exception as e:
                    import traceback

                    st.error(f"🚨 Engine Error (Phase 2 Demo): {str(e)}")
                    with st.expander("Show Detailed Error Log", expanded=True):
                        st.code(traceback.format_exc(), language="python")
            return

        with st.spinner("Phase 2 Execution Crew querying vaults & hashing evidence..."):
            try:
                flow.generate_fieldwork(human_id="AUDITOR_UI_001")

                if flow.state.status == "ERROR":
                    _show_error_state("Fieldwork")
                elif flow.state.status == "QA_REJECTED_PHASE_2":
                    st.error(
                        f"🚫 QA Gate rejected the Working Papers: {flow.state.qa_rejection_reason}\n\n"
                        "Review the RACM and re-run fieldwork."
                    )
                else:
                    st.session_state.phase = 2
                    _persist_session()
                    st.rerun()
            except Exception as e:
                import traceback

                st.error(f"🚨 Engine Error (Phase 2): {str(e)}")
                with st.expander("Show Detailed Error Log", expanded=True):
                    st.code(traceback.format_exc(), language="python")

    # Show feedback acknowledgement if submitted
    if st.session_state.get("p1_feedback_submitted"):
        st.info(
            f'📝 Feedback received: *"{st.session_state["p1_feedback_submitted"]}"*\n\n'
            "To incorporate this feedback, return to Phase 0 and re-launch with an updated scope."
        )
        if st.button("↩️ Return to Scope Input"):
            st.session_state.clear()
            st.rerun()

    render_phase1_review(flow.state.racm_plan, on_phase1_feedback)

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — Execution Phase Gate (Working Papers)
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.phase == 2:

    def on_phase2_finalize():
        if DEMO_MODE:
            with st.spinner("Demo mode: injecting hardcoded final report..."):
                try:
                    from swarm.schema import FinalReportSchema
                    from datetime import datetime

                    flow.state.final_report = FinalReportSchema(
                        executive_summary=(
                            "Audit successfully completed. IAM Password policy is active "
                            "and S3 buckets were scanned."
                        ),
                        detailed_report=(
                            "Technical findings indicate compliance with CIS AWS Foundations. "
                            "Vault hashes have been verified against raw evidence."
                        ),
                        compliance_tone_approved=True,
                    ).model_dump()

                    flow.state.approval_trail.append(
                        {
                            "gate": "Gate 2 (Fieldwork)",
                            "human": "AUDITOR_UI_001",
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                    )

                    st.session_state.phase = 3
                    _persist_session()
                    st.rerun()
                except Exception as e:
                    import traceback

                    st.error(f"🚨 Engine Error (Phase 3 Demo): {str(e)}")
                    with st.expander("Show Detailed Error Log", expanded=True):
                        st.code(traceback.format_exc(), language="python")
        else:
            with st.spinner(
                "Reporting Phase: AI crew is generating the final audit report..."
            ):
                try:
                    flow.generate_reporting(human_id="AUDITOR_UI_001")

                    if flow.state.status == "ERROR":
                        _show_error_state("Reporting")
                    elif flow.state.status == "QA_REJECTED_PHASE_3":
                        st.error(
                            f"🚫 Tone QA rejected the report: {flow.state.qa_rejection_reason}\n\n"
                            "The crew will auto-revise on retry."
                        )
                    else:
                        st.session_state.phase = 3
                        _persist_session()
                        st.rerun()
                except Exception as e:
                    import traceback

                    st.error(f"🚨 Engine Error (Phase 3): {str(e)}")
                    with st.expander("Show Detailed Error Log", expanded=True):
                        st.code(traceback.format_exc(), language="python")

    render_phase2_review(flow.state.working_papers, on_phase2_finalize)

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — Final Report and Immutable Audit Trail
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.phase == 3:
    st.success("🎉 Swarm Run Finished. Phase 3 (Reporting) Output Received!")

    rep = flow.state.final_report or {}

    # KPI summary from working papers
    findings = (flow.state.working_papers or {}).get("findings", [])
    if findings:
        total = len(findings)
        passes = sum(1 for f in findings if f.get("severity") == "Pass")
        deficiencies = sum(
            1
            for f in findings
            if f.get("severity") in ("Control Deficiency", "Significant Deficiency")
        )
        weaknesses = sum(
            1 for f in findings if f.get("severity") == "Material Weakness"
        )
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Controls Evaluated", total)
        k2.metric("✅ Pass", passes)
        k3.metric("⚠️ Deficiencies", deficiencies)
        k4.metric("❌ Material Weaknesses", weaknesses)
        st.markdown("---")

    st.markdown("### 📊 Board Executive Summary")

    import re

    def sanitize_report(text: str) -> str:
        # Strip markdown images to prevent blind SSRF / Data Exfiltration (GRC-01)
        return re.sub(r"!\[.*?\]\(.*?\)", "[Image Removed for Security]", text)

    st.info(sanitize_report(rep.get("executive_summary", "No summary provided.")))

    with st.expander("📄 Detailed Engineering Report", expanded=False):
        st.markdown(sanitize_report(rep.get("detailed_report", "No details provided.")))

    # Download final report
    executive_summary = rep.get("executive_summary", "")
    detailed_report = rep.get("detailed_report", "")
    report_text = f"# GRC Audit Report\n\n## Executive Summary\n\n{executive_summary}\n\n## Detailed Report\n\n{detailed_report}\n"
    st.download_button(
        "📥 Download Final Report (.md)",
        report_text.encode("utf-8"),
        "grc_audit_report.md",
        "text/markdown",
        use_container_width=True,
    )

    st.markdown("---")
    st.markdown("### 📝 Engagement Supervision Audit Trail (IIA 2340 Stamping)")
    trail = flow.state.approval_trail
    if trail:
        for entry in trail:
            st.markdown(
                f"**{entry.get('gate')}** — Approved by `{entry.get('human')}` at `{entry.get('timestamp')}`"
            )
    else:
        st.info("No approval trail recorded.")

    st.markdown("---")
    if st.button("🔄 Start New Audit", type="primary"):
        st.session_state.clear()
        st.rerun()
