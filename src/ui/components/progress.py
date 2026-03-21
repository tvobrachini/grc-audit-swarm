"""Phase progress strip — rendered at the top of every active-session screen."""

import streamlit as st

# Ordered list of (label, phases-where-this-step-is-DONE)
# "done" means the step has been completed and we are past it.
_STEPS = [
    "Scope",
    "Planning",
    "Plan Review",
    "Testing",
    "Findings",
    "Report",
]

# Which ViewPhase string value makes each step "active" (currently in progress)
_ACTIVE_AT: dict[str, str] = {
    "phase1": "Planning",
    "phase1_review": "Plan Review",
    "phase2": "Testing",
    "phase2_review": "Findings",
    "complete": "Report",
}

# Steps that are fully DONE once we reach a given phase (everything before the active step)
_DONE_BEFORE: dict[str, set[str]] = {
    "phase1": {"Scope"},
    "phase1_review": {"Scope", "Planning"},
    "phase2": {"Scope", "Planning", "Plan Review"},
    "phase2_review": {"Scope", "Planning", "Plan Review", "Testing"},
    "complete": {"Scope", "Planning", "Plan Review", "Testing", "Findings"},
}


def render_phase_strip(view_phase: str) -> None:
    """Render a horizontal step-progress strip based on the current ViewPhase value."""
    active_step = _ACTIVE_AT.get(view_phase, "Planning")
    done_steps = _DONE_BEFORE.get(view_phase, {"Scope"})

    parts: list[str] = []
    for i, label in enumerate(_STEPS):
        is_done = label in done_steps
        is_active = label == active_step

        dot_cls = "done" if is_done else ("active" if is_active else "")
        lbl_cls = "done" if is_done else ("active" if is_active else "")
        icon = "✓" if is_done else ("●" if is_active else str(i + 1))

        if i > 0:
            prev_label = _STEPS[i - 1]
            conn_cls = "done" if prev_label in done_steps else ""
            parts.append(f'<div class="phase-connector {conn_cls}"></div>')

        parts.append(
            f'<div class="phase-step">'
            f'<div class="phase-dot {dot_cls}">{icon}</div>'
            f'<div class="phase-label {lbl_cls}">{label}</div>'
            f"</div>"
        )

    html = '<div class="phase-strip">' + "".join(parts) + "</div>"
    st.markdown(html, unsafe_allow_html=True)
