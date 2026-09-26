from enum import Enum


class AuditStatus(str, Enum):
    WAITING_FOR_SCOPE = "WAITING_FOR_SCOPE"
    RUNNING_PHASE_1 = "RUNNING_PHASE_1"
    WAITING_HUMAN_GATE_1 = "WAITING_HUMAN_GATE_1"
    QA_REJECTED_PHASE_1 = "QA_REJECTED_PHASE_1"
    ERROR_PHASE_1 = "ERROR_PHASE_1"
    RUNNING_PHASE_2 = "RUNNING_PHASE_2"
    WAITING_HUMAN_GATE_2 = "WAITING_HUMAN_GATE_2"
    QA_REJECTED_PHASE_2 = "QA_REJECTED_PHASE_2"
    ERROR_PHASE_2 = "ERROR_PHASE_2"
    RUNNING_PHASE_3 = "RUNNING_PHASE_3"
    WAITING_HUMAN_GATE_3 = "WAITING_HUMAN_GATE_3"
    QA_REJECTED_PHASE_3 = "QA_REJECTED_PHASE_3"
    ERROR_PHASE_3 = "ERROR_PHASE_3"
    COMPLETED = "COMPLETED"


_TRANSITIONS: dict[AuditStatus, set[AuditStatus]] = {
    AuditStatus.WAITING_FOR_SCOPE: {AuditStatus.RUNNING_PHASE_1},
    AuditStatus.RUNNING_PHASE_1: {
        AuditStatus.WAITING_HUMAN_GATE_1,
        AuditStatus.QA_REJECTED_PHASE_1,
        AuditStatus.ERROR_PHASE_1,
    },
    AuditStatus.WAITING_HUMAN_GATE_1: {
        AuditStatus.RUNNING_PHASE_2,
        AuditStatus.RUNNING_PHASE_1,
    },
    # WAITING_HUMAN_GATE_n → RUNNING_PHASE_n is a reviewer returning the phase
    # for rework with review notes (see return_for_rework).
    # QA_REJECTED → RUNNING is a retry; QA_REJECTED → WAITING_HUMAN_GATE is a
    # supervisor override (accept the artifact despite the QA rejection).
    AuditStatus.QA_REJECTED_PHASE_1: {
        AuditStatus.RUNNING_PHASE_1,
        AuditStatus.WAITING_HUMAN_GATE_1,
    },
    AuditStatus.ERROR_PHASE_1: {AuditStatus.RUNNING_PHASE_1},
    AuditStatus.RUNNING_PHASE_2: {
        AuditStatus.WAITING_HUMAN_GATE_2,
        AuditStatus.QA_REJECTED_PHASE_2,
        AuditStatus.ERROR_PHASE_2,
    },
    AuditStatus.WAITING_HUMAN_GATE_2: {
        AuditStatus.RUNNING_PHASE_3,
        AuditStatus.RUNNING_PHASE_2,
    },
    AuditStatus.QA_REJECTED_PHASE_2: {
        AuditStatus.RUNNING_PHASE_2,
        AuditStatus.WAITING_HUMAN_GATE_2,
    },
    AuditStatus.ERROR_PHASE_2: {AuditStatus.RUNNING_PHASE_2},
    AuditStatus.RUNNING_PHASE_3: {
        AuditStatus.WAITING_HUMAN_GATE_3,
        AuditStatus.QA_REJECTED_PHASE_3,
        AuditStatus.ERROR_PHASE_3,
    },
    AuditStatus.WAITING_HUMAN_GATE_3: {
        AuditStatus.COMPLETED,
        AuditStatus.RUNNING_PHASE_3,
    },
    AuditStatus.QA_REJECTED_PHASE_3: {
        AuditStatus.RUNNING_PHASE_3,
        AuditStatus.WAITING_HUMAN_GATE_3,
    },
    AuditStatus.ERROR_PHASE_3: {AuditStatus.RUNNING_PHASE_3},
    AuditStatus.COMPLETED: set(),
}


class InvalidTransitionError(Exception):
    def __init__(self, current: AuditStatus, target: AuditStatus) -> None:
        super().__init__(f"Invalid transition: {current.value} → {target.value}")
        self.current = current
        self.target = target


# Alias — callers (API, UI) may refer to the shorter name.
InvalidTransition = InvalidTransitionError


def _running(phase: int) -> AuditStatus:
    return AuditStatus(f"RUNNING_PHASE_{phase}")


def _waiting_gate(phase: int) -> AuditStatus:
    return AuditStatus(f"WAITING_HUMAN_GATE_{phase}")


def _qa_rejected(phase: int) -> AuditStatus:
    return AuditStatus(f"QA_REJECTED_PHASE_{phase}")


def _error(phase: int) -> AuditStatus:
    return AuditStatus(f"ERROR_PHASE_{phase}")


class AuditStateMachine:
    """Explicit audit lifecycle.

    Every named transition checks both the *source* state (so e.g.
    ``approve_gate_1`` cannot be used to restart a QA-rejected Phase 2, and
    ``retry_phase_2`` cannot skip the Gate 1 human approval) and the global
    ``_TRANSITIONS`` table. Invalid calls raise :class:`InvalidTransitionError`
    and leave the status unchanged.
    """

    def __init__(self, initial: AuditStatus = AuditStatus.WAITING_FOR_SCOPE) -> None:
        self._status = initial

    @property
    def status(self) -> AuditStatus:
        return self._status

    def can(self, target: AuditStatus, *sources: AuditStatus) -> bool:
        if sources and self._status not in sources:
            return False
        return target in _TRANSITIONS.get(self._status, set())

    def _transition(self, target: AuditStatus, *sources: AuditStatus) -> None:
        if not self.can(target, *sources):
            raise InvalidTransitionError(self._status, target)
        self._status = target

    # ── Generic, phase-parameterised transitions ────────────────────────────

    def complete_phase(self, phase: int) -> None:
        self._transition(_waiting_gate(phase), _running(phase))

    def reject_phase(self, phase: int) -> None:
        self._transition(_qa_rejected(phase), _running(phase))

    def error_phase(self, phase: int) -> None:
        self._transition(_error(phase), _running(phase))

    def retry_phase(self, phase: int) -> None:
        self._transition(_running(phase), _qa_rejected(phase), _error(phase))

    def return_for_rework(self, phase: int) -> None:
        """Reviewer sends the phase back: WAITING_HUMAN_GATE_n → RUNNING_PHASE_n."""
        self._transition(_running(phase), _waiting_gate(phase))

    def override_qa(self, phase: int) -> None:
        """Supervisor accepts a QA-rejected artifact: → WAITING_HUMAN_GATE_n."""
        self._transition(_waiting_gate(phase), _qa_rejected(phase))

    # Phase 1
    def start_phase_1(self) -> None:
        self._transition(AuditStatus.RUNNING_PHASE_1, AuditStatus.WAITING_FOR_SCOPE)

    def complete_phase_1(self) -> None:
        self.complete_phase(1)

    def reject_phase_1(self) -> None:
        self.reject_phase(1)

    def error_phase_1(self) -> None:
        self.error_phase(1)

    def retry_phase_1(self) -> None:
        self.retry_phase(1)

    # Phase 2
    def approve_gate_1(self) -> None:
        self._transition(AuditStatus.RUNNING_PHASE_2, AuditStatus.WAITING_HUMAN_GATE_1)

    def complete_phase_2(self) -> None:
        self.complete_phase(2)

    def reject_phase_2(self) -> None:
        self.reject_phase(2)

    def error_phase_2(self) -> None:
        self.error_phase(2)

    def retry_phase_2(self) -> None:
        self.retry_phase(2)

    # Phase 3
    def approve_gate_2(self) -> None:
        self._transition(AuditStatus.RUNNING_PHASE_3, AuditStatus.WAITING_HUMAN_GATE_2)

    def complete_phase_3(self) -> None:
        self.complete_phase(3)

    def reject_phase_3(self) -> None:
        self.reject_phase(3)

    def error_phase_3(self) -> None:
        self.error_phase(3)

    def retry_phase_3(self) -> None:
        self.retry_phase(3)

    def approve_gate_3(self) -> None:
        self._transition(AuditStatus.COMPLETED, AuditStatus.WAITING_HUMAN_GATE_3)
