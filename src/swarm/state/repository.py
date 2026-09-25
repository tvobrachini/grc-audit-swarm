import logging
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from pydantic import ValidationError

if TYPE_CHECKING:
    from swarm.audit_flow import AuditFlow

from swarm.schema import FinalReportSchema, RiskControlMatrixSchema, WorkingPaperSchema
from swarm.session_manager import get_session, update_session
from swarm.state.machine import AuditStatus

logger = logging.getLogger(__name__)

_ARTIFACT_FIELDS: dict[str, type] = {
    "racm_plan": RiskControlMatrixSchema,
    "working_papers": WorkingPaperSchema,
    "final_report": FinalReportSchema,
}


@dataclass
class LoadResult:
    flow: "AuditFlow"
    skipped_fields: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return len(self.skipped_fields) == 0


class FlowRepository:
    def save(self, session_id: str, flow: "AuditFlow") -> bool:
        """Persist the flow snapshot into an existing session entry.

        A single locked read-modify-write that preserves every other field
        (name, created_at, ui_phase …). It never creates the entry, so a phase
        thread finishing after ``DELETE /sessions/{id}`` cannot resurrect the
        deleted session. Returns False when the session no longer exists.
        """
        scope_text = flow.state.business_context
        saved = update_session(
            session_id,
            scope_text=scope_text,
            scope_preview=scope_text[:200],
            status=flow.state.status,
            state_snapshot=flow.state.model_dump(mode="json"),
        )
        if not saved:
            logger.info(
                "Session %s no longer exists (deleted?) — snapshot not saved",
                session_id,
            )
        return saved

    def load(self, session_id: str) -> Optional[LoadResult]:
        from swarm.audit_flow import AuditFlow

        meta = get_session(session_id)
        if not meta:
            return None
        snapshot = meta.get("state_snapshot") or {}
        if not snapshot:
            return None

        skipped: list[str] = []

        # Reconstruct flow with correct initial machine status
        initial_status = AuditStatus(
            snapshot.get("status", AuditStatus.WAITING_FOR_SCOPE)
        )
        flow = AuditFlow(initial_status=initial_status.value)

        # Validate and set typed artifact fields
        for field_name, schema_cls in _ARTIFACT_FIELDS.items():
            raw = snapshot.get(field_name)
            if raw is not None:
                try:
                    setattr(flow.state, field_name, schema_cls.model_validate(raw))
                except ValidationError as exc:
                    logger.warning(
                        "Schema mismatch loading %s for session %s: %s",
                        field_name,
                        session_id,
                        exc,
                    )
                    skipped.append(field_name)

        # Set remaining scalar fields
        scalar_fields = [
            k for k in snapshot if k not in _ARTIFACT_FIELDS and k != "status"
        ]
        for k in scalar_fields:
            try:
                setattr(flow.state, k, snapshot[k])
            except Exception as exc:
                logger.warning(
                    "Failed to set field %s for session %s: %s", k, session_id, exc
                )
                skipped.append(k)

        return LoadResult(flow=flow, skipped_fields=skipped)
