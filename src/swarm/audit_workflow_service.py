from collections.abc import MutableMapping
from typing import Any

from swarm.audit_snapshot import build_audit_state_snapshot
from swarm.app_flow import derive_app_view_state
from swarm.graph_service import GraphService
from swarm.review_actions import (
    build_phase1_review_patch,
    flag_control_for_finding,
    mark_control_clean,
    request_control_rerun,
    submit_phase2_feedback,
)
from swarm.session_manager import save_session, update_session
from swarm.session_sync import append_chat_message, build_session_update

# ── Human-readable activity log labels ────────────────────────────────────────
_NODE_LABELS: dict[str, str] = {
    "orchestrator": "🔍 Scope analysed",
    "researcher": "📰 Risk & breach research complete",
    "control_mapper": "🗂️ Control matrix built",
    "dynamic_specialists": "🎯 Specialist procedures injected",
    "challenger": "⚖️ QA review",
    "evidence_collector": "🔬 Evidence collected",
    "run_all_workers": "⚙️ Control tests complete",
    "phase2_specialist": "🔬 Regulatory annotations added",
    "phase2_researcher": "📰 Breach precedent research complete",
    "phase2_challenger": "⚖️ Findings quality review complete",
    "concluder": "📊 Executive summary drafted",
}


def _describe_node_event(node: str, update: dict) -> str:
    """Return a rich human-readable description of a completed graph node event."""
    base = _NODE_LABELS.get(node, f"✅ {node} complete")

    if node == "orchestrator":
        themes = update.get("risk_themes") or []
        skills = update.get("active_skill_names") or []
        if themes:
            base += f" — {', '.join(themes)}"
        if skills:
            base += f"\n> 🎯 Skills loaded: **{'  ·  '.join(skills)}**"

    elif node == "control_mapper":
        matrix = update.get("control_matrix") or []
        if matrix:
            ids = ", ".join(
                (
                    m.get("control_id") or ""
                    if isinstance(m, dict)
                    else getattr(m, "control_id", "")
                )
                for m in matrix
            )
            base += (
                f" — {len(matrix)} control{'s' if len(matrix) != 1 else ''}: *{ids}*"
            )

    elif node == "challenger":
        feedback = update.get("challenger_feedback", "")
        base += " — revisions requested" if feedback else " — approved ✓"

    elif node == "run_all_workers":
        findings = update.get("testing_findings") or []
        if findings:

            def _s(f):
                return (
                    f.get("status") if isinstance(f, dict) else getattr(f, "status", "")
                )

            p = sum(1 for f in findings if _s(f) == "Pass")
            e = sum(1 for f in findings if _s(f) == "Exception")
            fa = sum(1 for f in findings if _s(f) == "Fail")
            base += f" — {p} ✅  {e} ⚠️  {fa} ❌"

    return base


class AuditWorkflowService:
    def __init__(self, graph_service: GraphService | None = None):
        self._graph_service = graph_service or GraphService()

    def get_view_state(self, config: dict[str, Any]) -> dict[str, Any]:
        return derive_app_view_state(self._graph_service.get_state(config))

    def sync_state_snapshot(
        self,
        session_state: MutableMapping[str, Any],
        view_state: dict[str, Any],
    ) -> None:
        update_session(
            session_state["thread_id"],
            state_snapshot=build_audit_state_snapshot(
                view_state["view_phase"],
                view_state["next_nodes"],
                view_state["state_vals"],
            ).model_dump(),
        )

    def append_chat_message(
        self,
        session_state: MutableMapping[str, Any],
        role: str,
        content: str,
        reasoning: str | None = None,
    ) -> None:
        session_state["chat_history"] = append_chat_message(
            session_state["chat_history"],
            role,
            content,
            reasoning=reasoning,
        )
        update_session(
            session_state["thread_id"],
            **build_session_update(
                session_state["scope_text_cache"], session_state["chat_history"]
            ),
        )

    def start_audit(
        self,
        session_state: MutableMapping[str, Any],
        scope_text: str,
        audit_name: str,
    ) -> None:
        name = audit_name.strip() or f"Audit {session_state['thread_id'][:8]}"
        session_state["scope_text_cache"] = scope_text
        session_state["scope_submitted"] = True
        self.append_chat_message(
            session_state, "user", f"**🚀 {name}** — Scope loaded."
        )
        save_session(
            session_state["thread_id"],
            name,
            scope_text,
            session_state["chat_history"],
        )

    def consume_stream(
        self,
        session_state: MutableMapping[str, Any],
        config: dict[str, Any],
        state_vals: dict[str, Any],
    ) -> list[dict[str, Any]]:
        stream_input = (
            {
                "audit_scope_narrative": session_state["scope_text_cache"],
                "audit_trail": [],
            }
            if not state_vals
            else None
        )
        events = list(self._graph_service.stream_updates(stream_input, config))
        for event in events:
            for node, update in event.items():
                if not isinstance(update, dict):
                    continue
                reasoning = None
                if update.get("audit_trail"):
                    last = update["audit_trail"][-1]
                    reasoning = (
                        last.get("reasoning_snapshot")
                        if isinstance(last, dict)
                        else getattr(last, "reasoning_snapshot", None)
                    )
                self.append_chat_message(
                    session_state,
                    "assistant",
                    _describe_node_event(node, update),
                    reasoning=reasoning,
                )
        session_state["resume_swarm"] = False
        return events

    def submit_phase1_review(
        self,
        session_state: MutableMapping[str, Any],
        config: dict[str, Any],
        feedback: str,
    ) -> None:
        self.append_chat_message(session_state, "user", feedback)
        self._graph_service.update_state(config, build_phase1_review_patch(feedback))
        session_state["resume_swarm"] = True

    def mark_control_clean(
        self,
        config: dict[str, Any],
        execution_status: dict[str, Any] | None,
        control_id: str,
    ) -> None:
        self._graph_service.update_state(
            config, mark_control_clean(execution_status, control_id)
        )

    def flag_control_for_finding(
        self,
        config: dict[str, Any],
        execution_status: dict[str, Any] | None,
        control_id: str,
    ) -> None:
        self._graph_service.update_state(
            config, flag_control_for_finding(execution_status, control_id)
        )

    def rerun_control(
        self,
        session_state: MutableMapping[str, Any],
        config: dict[str, Any],
        control_feedback: dict[str, str] | None,
        control_id: str,
        feedback: str,
    ) -> None:
        self._graph_service.update_state(
            config, request_control_rerun(control_feedback, control_id, feedback)
        )
        session_state["resume_swarm"] = True

    def submit_phase2_feedback(
        self,
        session_state: MutableMapping[str, Any],
        config: dict[str, Any],
        control_feedback: dict[str, str],
    ) -> None:
        self._graph_service.update_state(
            config, submit_phase2_feedback(control_feedback)
        )
        self.append_chat_message(
            session_state,
            "user",
            f"Submitted feedback on {len(control_feedback)} controls for re-evaluation.",
        )
        session_state["control_feedback"] = {}
        session_state["resume_swarm"] = True

    def finalize_audit(
        self,
        session_state: MutableMapping[str, Any],
        config: dict[str, Any],
    ) -> None:
        self._graph_service.update_state(config, {"control_feedback": {}})
        self.append_chat_message(
            session_state,
            "user",
            "✅ Audit approved. Final report generated.",
        )
        session_state["resume_swarm"] = True
