import io
from collections.abc import Callable

import pandas as pd
import streamlit as st


# ── Finding text parser ────────────────────────────────────────────────────────
def _split_justification(text: str) -> tuple[str, str, str]:
    """
    Split a finding justification into three sections:
      (worker_finding, specialist_annotation, researcher_context)
    The latter two are appended by Phase 2 agents using known markers.
    """
    spec_marker = "**🔬 Specialist Annotation"
    res_marker = "**🌐 Researcher Context:**"

    specialist = ""
    researcher = ""

    spec_idx = text.find(spec_marker)
    res_idx = text.find(res_marker)

    if spec_idx > 0:
        worker = text[:spec_idx].strip()
        remainder = text[spec_idx:]
        inner_res = remainder.find(res_marker)
        if inner_res > 0:
            specialist = remainder[:inner_res].strip()
            researcher = remainder[inner_res:].strip()
        else:
            specialist = remainder.strip()
    elif res_idx > 0:
        worker = text[:res_idx].strip()
        researcher = text[res_idx:].strip()
    else:
        worker = text.strip()

    return worker, specialist, researcher


def render_phase2_review(
    state_vals,
    session_control_feedback,
    get_value: Callable,
    step_badge: Callable,
    on_mark_clean: Callable[[str], None],
    on_flag_finding: Callable[[str], None],
    on_rerun_control: Callable[[str, str], None],  # kept for API compat, not used in UI
    on_submit_feedback: Callable[[dict[str, str]], None],
    on_finalize: Callable[[], None],
):
    findings = state_vals.get("testing_findings", [])
    execution_status = state_vals.get("execution_status", {}) or {}
    summary = state_vals.get("executive_summary", "")

    total = len(findings)
    passes = sum(1 for f in findings if get_value(f, "status") == "Pass")
    excepts = sum(1 for f in findings if get_value(f, "status") == "Exception")
    fails = sum(1 for f in findings if get_value(f, "status") == "Fail")
    actioned = sum(
        1 for s in execution_status.values() if str(s) in ("clean", "flagged")
    )

    st.markdown("## ⚙️ Phase 2 — Findings Command Center")

    # ── KPI strip ─────────────────────────────────────────────────────────────
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("🔬 Controls", total)
    k2.metric("✅ Pass", passes)
    k3.metric("⚠️ Exception", excepts)
    k4.metric("❌ Fail", fails)
    k5.metric("👁️ Reviewed", f"{actioned}/{total}")

    # ── Executive summary ──────────────────────────────────────────────────────
    if summary:
        with st.expander("📊 Executive Summary", expanded=False):
            st.markdown(summary)

    st.markdown("---")

    # ── Filter bar ─────────────────────────────────────────────────────────────
    col_f, col_hint = st.columns([1, 3])
    status_filter = col_f.selectbox(
        "Filter",
        ["All", "Pass", "Exception", "Fail"],
        key="status_filter",
        label_visibility="collapsed",
    )
    col_hint.caption(
        "Review each finding, leave notes in the text box, then **Submit All Feedback** at the bottom to re-run selected tests."
    )

    shown = (
        findings
        if status_filter == "All"
        else [f for f in findings if get_value(f, "status") == status_filter]
    )

    st.markdown("### 📋 Control Findings")

    has_pending_feedback = False

    for finding in shown:
        cid = get_value(finding, "control_id")
        status = get_value(finding, "status")
        risk = get_value(finding, "risk_rating") or "N/A"
        justification = get_value(finding, "justification") or ""
        evids = get_value(finding, "evidence_extracted") or []
        tod_r = get_value(finding, "tod_result")
        toe_r = get_value(finding, "toe_result")
        sub_r = get_value(finding, "substantive_result")
        ctrl_status = str(execution_status.get(cid, ""))

        # Card header
        icon = {"Pass": "✅", "Exception": "⚠️", "Fail": "❌"}.get(status, "❓")
        label = f"{icon} **{cid}** — {status}"
        if risk not in ("N/A", None, ""):
            label += f" · Risk: **{risk}**"
        if ctrl_status == "clean":
            label += " · 🟢 Marked Clean"
        elif ctrl_status == "flagged":
            label += " · 🚩 Flagged"

        with st.expander(label, expanded=(status in ("Fail", "Exception"))):
            # Step result badges
            r1, r2, r3 = st.columns(3)
            r1.markdown(f"**TOD:** {step_badge(tod_r)}")
            r2.markdown(f"**TOE:** {step_badge(toe_r)}")
            r3.markdown(f"**Substantive:** {step_badge(sub_r)}")
            st.markdown("---")

            # Finding narrative (split from expert context)
            worker_text, specialist_text, researcher_text = _split_justification(
                justification
            )

            st.markdown(
                '<p class="finding-section-label">Finding</p>', unsafe_allow_html=True
            )
            st.markdown(worker_text)

            # Evidence
            if evids:
                with st.expander("📎 Evidence Extracted"):
                    for ev in evids:
                        st.markdown(f"- {ev}")

            # Expert context (collapsed by default — depth on demand)
            if specialist_text or researcher_text:
                with st.expander("🔬 Expert Context"):
                    if specialist_text:
                        st.markdown(
                            '<p class="finding-section-label">Specialist Annotation</p>',
                            unsafe_allow_html=True,
                        )
                        st.markdown(specialist_text)
                    if researcher_text:
                        if specialist_text:
                            st.markdown("---")
                        st.markdown(
                            '<p class="finding-section-label">Breach & Enforcement Precedent</p>',
                            unsafe_allow_html=True,
                        )
                        st.markdown(researcher_text)

            st.markdown("---")

            # Status action buttons (marking only — no inline re-test)
            col_a, col_b = st.columns(2)
            if col_a.button(
                "✅ Mark Clean", key=f"clean_{cid}", use_container_width=True
            ):
                on_mark_clean(cid)
            if col_b.button(
                "🚩 Flag as Finding", key=f"flag_{cid}", use_container_width=True
            ):
                on_flag_finding(cid)

            # Per-control notes (feeds the bottom Submit)
            existing_fb = session_control_feedback.get(cid, "")
            new_fb = st.text_area(
                f"💬 Notes for {cid}",
                value=existing_fb,
                placeholder=(
                    "Add context or corrections here. "
                    "Submit all feedback at the bottom to trigger a targeted re-test."
                ),
                height=80,
                key=f"fb_{cid}",
                label_visibility="collapsed",
            )
            new_fb = new_fb or ""
            if new_fb.strip():
                session_control_feedback[cid] = new_fb
                has_pending_feedback = True
            elif cid in session_control_feedback and not new_fb.strip():
                session_control_feedback.pop(cid, None)

    # ── Bottom action bar ──────────────────────────────────────────────────────
    st.markdown("---")
    _, c_center, _ = st.columns([1, 2, 1])

    with c_center:
        if has_pending_feedback:
            pending_count = sum(
                1 for v in session_control_feedback.values() if v.strip()
            )
            st.info(
                f"📝 **{pending_count} control{'s' if pending_count > 1 else ''}** "
                f"have notes — submit to re-run those tests with your context."
            )
            if st.button(
                "🔁 Submit Feedback & Re-run Flagged Tests",
                type="primary",
                use_container_width=True,
            ):
                on_submit_feedback(dict(session_control_feedback))
        else:
            if actioned == total and total > 0:
                st.success(f"All {total} controls reviewed.")
            else:
                remaining = total - actioned
                st.caption(
                    f"**{actioned}/{total}** controls reviewed · "
                    f"{remaining} still awaiting your decision."
                )

        st.markdown(" ")
        if st.button(
            "✅ Approve & Finalise Audit Report",
            type="primary",
            use_container_width=True,
        ):
            on_finalize()

    # ── Report download ────────────────────────────────────────────────────────
    st.markdown("---")
    if findings:
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
        st.download_button(
            "📥 Download Findings Report (Excel)",
            buf.getvalue(),
            "audit_findings.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
