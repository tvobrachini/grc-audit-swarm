import os
import sys

from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_app_initial_screen_renders(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    at = AppTest.from_file("app.py")
    at.run(timeout=10)

    assert any("Swarm Audit Command Center" in title.value for title in at.title)
    assert any(button.label == "🚀 Launch Swarm" for button in at.button)


def test_scope_input_component_loads_lab_file_and_triggers_launch(
    tmp_path, monkeypatch
):
    lab_file = tmp_path / "aws_scope.txt"
    expected_scope = "AWS IAM review\nValidate CloudTrail coverage\n"
    lab_file.write_text(expected_scope, encoding="utf-8")
    monkeypatch.setenv("TEST_LAB_DIR", str(tmp_path))

    def app():
        import os
        import streamlit as st

        from ui.components.scope_input import render_scope_input

        st.session_state.setdefault("captured_suggestion", "")
        st.session_state.setdefault("launch_payload", None)

        def on_scope_change(suggestion: str):
            st.session_state.captured_suggestion = suggestion

        def on_launch(scope_text: str, audit_name: str):
            st.session_state.launch_payload = {
                "scope_text": scope_text,
                "audit_name": audit_name,
            }

        render_scope_input(
            lab_dir=os.environ["TEST_LAB_DIR"],
            suggested_audit_name=st.session_state.captured_suggestion,
            suggest_audit_name=lambda text: text.splitlines()[-1],
            on_scope_change=on_scope_change,
            on_launch=on_launch,
        )

    at = AppTest.from_function(app)
    at.run()
    lab_options = [option for option in at.selectbox[0].options if option != "None"]
    assert lab_options

    selected_lab = lab_options[0]

    at.selectbox[0].set_value(selected_lab)
    at.run()

    assert at.text_area[0].value == expected_scope

    at.button[0].click()
    at.run()

    launch_payload = at.session_state["launch_payload"]
    assert launch_payload["scope_text"] == expected_scope
