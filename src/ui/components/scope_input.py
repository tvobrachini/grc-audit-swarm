import os
from collections.abc import Callable

import pdfplumber
import streamlit as st


def render_scope_input(
    lab_dir: str,
    suggested_audit_name: str,
    suggest_audit_name: Callable[[str], str],
    on_scope_change: Callable[[str], None],
    on_launch: Callable[[str, str], None],
):
    st.markdown(
        "Submit a scope narrative and let the AI swarm research, map controls, execute tests, and present findings for your review."
    )

    scope_text = ""
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("### 📄 Upload Scope")
        upload = st.file_uploader("PDF or TXT", type=["pdf", "txt"], key="scope_up")
        if upload:
            if upload.name.endswith(".pdf"):
                with pdfplumber.open(upload) as pdf:
                    scope_text = "\n".join(
                        [
                            page.extract_text()
                            for page in pdf.pages
                            if page.extract_text()
                        ]
                    )
            else:
                try:
                    scope_text = upload.getvalue().decode("utf-8")
                except UnicodeDecodeError:
                    st.error(
                        "File encoding not supported. Please upload a UTF-8 encoded file."
                    )
                    scope_text = ""

        lab_files = list_lab_files(lab_dir, ".txt")
        selected_lab = st.selectbox(
            "Or Lab Data", ["None"] + lab_files, key="scope_lab"
        )
        if selected_lab != "None" and not scope_text:
            resolved = os.path.realpath(os.path.join(lab_dir, selected_lab))
            if not resolved.startswith(os.path.realpath(lab_dir) + os.sep):
                st.error("Invalid file selection.")
            else:
                with open(resolved, "r", encoding="utf-8") as handle:
                    scope_text = handle.read()

    with col_b:
        st.markdown("### 🔍 Preview")
        if scope_text:
            st.text_area(
                "Scope (Editable)", value=scope_text, height=200, key="scope_ta"
            )
            scope_text = st.session_state.scope_ta
        else:
            st.info("Upload or select lab data to see the scope.")

    if scope_text:
        suggestion = suggest_audit_name(scope_text)
        if suggestion and suggested_audit_name != suggestion:
            on_scope_change(suggestion)

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
        if not scope_text:
            st.warning("Please provide scope text.")
        else:
            on_launch(scope_text, audit_name)


def list_lab_files(lab_dir: str, ext: str | None = None) -> list[str]:
    if not os.path.exists(lab_dir):
        return []
    files = [
        name
        for name in os.listdir(lab_dir)
        if os.path.isfile(os.path.join(lab_dir, name))
    ]
    return [name for name in files if name.endswith(ext)] if ext else files
