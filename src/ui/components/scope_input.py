import os
import re
from collections.abc import Callable

import pdfplumber
import streamlit as st


def suggest_audit_name(scope_text: str) -> str:
    """Extract a concise audit name from the first meaningful line of the scope."""
    for line in scope_text.splitlines():
        line = line.strip().strip("-=")
        if (
            not line
            or len(line) < 8
            or re.match(r"^(organization|prepared|audit period|period):", line, re.I)
        ):
            continue
        line = re.sub(
            r"^(AUDIT SCOPE NARRATIVE[\s\—\-–]+|AUDIT SCOPE[\s\—\-–]+|SCOPE[:\s]+)",
            "",
            line,
            flags=re.I,
        ).strip()
        if line:
            return line[:80].strip()
    return ""


def render_scope_input(
    lab_dir: str,
    suggested_audit_name: str,
    on_scope_change: Callable[[str], None],
    on_launch: Callable[[str, str], None],
):
    st.markdown(
        "Submit a scope narrative and let the AI swarm research, map controls, "
        "execute tests, and present findings for your review."
    )

    # ── Source selectors (compact top row) ────────────────────────────────────
    col_upload, col_lab = st.columns(2)
    scope_from_source = ""

    with col_upload:
        upload = st.file_uploader(
            "📁 Upload scope (PDF or TXT)",
            type=["pdf", "txt"],
            key="scope_up",
            label_visibility="visible",
        )
        if upload:
            scope_from_source = _parse_upload(upload)

    with col_lab:
        lab_files = list_lab_files(lab_dir, ".txt")
        selected_lab = st.selectbox(
            "📋 Or use lab data",
            ["None"] + lab_files,
            key="scope_lab",
        )
        if selected_lab != "None" and not scope_from_source:
            scope_from_source = _read_lab_file(lab_dir, selected_lab)

    # ── Pre-populate the text area when a new source is loaded ────────────────
    # Uses a content hash to detect actual changes — preserves user edits otherwise.
    if scope_from_source:
        src_hash = hash(scope_from_source)
        if st.session_state.get("_scope_src_hash") != src_hash:
            st.session_state["scope_ta"] = scope_from_source
            st.session_state["_scope_src_hash"] = src_hash

    # ── Always-visible editable text area ─────────────────────────────────────
    st.markdown("##### ✏️ Scope Narrative")
    scope_text: str = st.text_area(
        "Scope narrative",
        key="scope_ta",
        height=260,
        placeholder=(
            "Paste or type your audit scope here...\n\n"
            "Include: the system / environment in scope, relevant frameworks "
            "(AWS, PCI-DSS, GDPR, HIPAA, SOX), audit period, and any specific risk areas."
        ),
        label_visibility="collapsed",
    )

    # ── Character count guidance ───────────────────────────────────────────────
    if scope_text:
        char_count = len(scope_text)
        if char_count < 120:
            st.markdown(
                f'<p class="char-warn">⚠️ {char_count} characters — add more context for best results (aim for 200+)</p>',
                unsafe_allow_html=True,
            )
        elif char_count > 3000:
            st.markdown(
                f'<p class="char-warn">⚠️ {char_count} characters — very long; consider trimming to key sections</p>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<p class="char-info">📏 {char_count} characters</p>',
                unsafe_allow_html=True,
            )

    # ── Suggest audit name from content ───────────────────────────────────────
    if scope_text:
        suggestion = suggest_audit_name(scope_text)
        if suggestion and suggested_audit_name != suggestion:
            on_scope_change(suggestion)

    # ── Audit name + launch ────────────────────────────────────────────────────
    st.markdown("---")
    audit_name = st.text_input(
        "📝 Audit Name",
        value=suggested_audit_name,
        placeholder="e.g. AWS Prod Q4 2026 – IAM Review",
        key="audit_name",
        help="Auto-suggested from scope — edit freely before launching.",
    )

    _, center, _ = st.columns([1, 2, 1])
    if center.button("🚀 Launch Swarm", type="primary", use_container_width=True):
        if not scope_text.strip():
            st.warning("Please provide scope text before launching.")
        else:
            on_launch(scope_text, audit_name)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_upload(upload) -> str:
    if upload.name.endswith(".pdf"):
        try:
            with pdfplumber.open(upload) as pdf:
                return "\n".join(
                    p.extract_text() for p in pdf.pages if p.extract_text()
                )
        except Exception:
            st.error("Could not parse PDF. Please try a TXT file.")
            return ""
    try:
        return upload.getvalue().decode("utf-8")
    except UnicodeDecodeError:
        st.error("File encoding not supported. Please upload a UTF-8 encoded file.")
        return ""


def _read_lab_file(lab_dir: str, filename: str) -> str:
    resolved = os.path.realpath(os.path.join(lab_dir, filename))
    if not resolved.startswith(os.path.realpath(lab_dir) + os.sep):
        st.error("Invalid file selection.")
        return ""
    with open(resolved, encoding="utf-8") as f:
        return f.read()


def list_lab_files(lab_dir: str, ext: str | None = None) -> list[str]:
    if not os.path.exists(lab_dir):
        return []
    files = [
        name
        for name in os.listdir(lab_dir)
        if os.path.isfile(os.path.join(lab_dir, name))
    ]
    return [name for name in files if name.endswith(ext)] if ext else files
