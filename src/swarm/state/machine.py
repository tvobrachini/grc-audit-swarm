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
    AuditStatus.WAITING_HUMAN_GATE_1: {AuditStatus.RUNNING_PHASE_2},
    AuditStatus.QA_REJECTED_PHASE_1: {AuditStatus.RUNNING_PHASE_1},
    AuditStatus.ERROR_PHASE_1: {AuditStatus.RUNNING_PHASE_1},
    AuditStatus.RUNNING_PHASE_2: {
        AuditStatus.WAITING_HUMAN_GATE_2,
        AuditStatus.QA_REJECTED_PHASE_2,
        AuditStatus.ERROR_PHASE_2,
    },
    AuditStatus.WAITING_HUMAN_GATE_2: {AuditStatus.RUNNING_PHASE_3},
    AuditStatus.QA_REJECTED_PHASE_2: {AuditStatus.RUNNING_PHASE_2},
    AuditStatus.ERROR_PHASE_2: {AuditStatus.RUNNING_PHASE_2},
    AuditStatus.RUNNING_PHASE_3: {
        AuditStatus.WAITING_HUMAN_GATE_3,
        AuditStatus.QA_REJECTED_PHASE_3,
        AuditStatus.ERROR_PHASE_3,
        AuditStatus.COMPLETED,
    },
    AuditStatus.WAITING_HUMAN_GATE_3: {AuditStatus.COMPLETED},
    AuditStatus.QA_REJECTED_PHASE_3: {AuditStatus.RUNNING_PHASE_3},
    AuditStatus.ERROR_PHASE_3: {AuditStatus.RUNNING_PHASE_3},
    AuditStatus.COMPLETED: set(),
}


class InvalidTransitionError(Exception):
    def __init__(self, current: AuditStatus, target: AuditStatus) -> None:
        super().__init__(f"Invalid transition: {current.value} → {target.value}")
        self.current = current
        self.target = target


class AuditStateMachine:
    def __init__(self, initial: AuditStatus = AuditStatus.WAITING_FOR_SCOPE) -> None:
        self._status = initial

    @property
    def status(self) -> AuditStatus:
        return self._status

    def _transition(self, target: AuditStatus) -> None:
        allowed = _TRANSITIONS.get(self._status, set())
        if target not in allowed:
            raise InvalidTransitionError(self._status, target)
        self._status = target

    # Phase 1
    def start_phase_1(self) -> None:
        self._transition(AuditStatus.RUNNING_PHASE_1)

    def complete_phase_1(self) -> None:
        self._transition(AuditStatus.WAITING_HUMAN_GATE_1)

    def reject_phase_1(self) -> None:
        self._transition(AuditStatus.QA_REJECTED_PHASE_1)

    def error_phase_1(self) -> None:
        self._transition(AuditStatus.ERROR_PHASE_1)

    def retry_phase_1(self) -> None:
        self._transition(AuditStatus.RUNNING_PHASE_1)

    # Phase 2
    def approve_gate_1(self) -> None:
        self._transition(AuditStatus.RUNNING_PHASE_2)

    def complete_phase_2(self) -> None:
        self._transition(AuditStatus.WAITING_HUMAN_GATE_2)

    def reject_phase_2(self) -> None:
        self._transition(AuditStatus.QA_REJECTED_PHASE_2)

    def error_phase_2(self) -> None:
        self._transition(AuditStatus.ERROR_PHASE_2)

    def retry_phase_2(self) -> None:
        self._transition(AuditStatus.RUNNING_PHASE_2)

    # Phase 3
    def approve_gate_2(self) -> None:
        self._transition(AuditStatus.RUNNING_PHASE_3)

    def complete_phase_3(self) -> None:
        self._transition(AuditStatus.WAITING_HUMAN_GATE_3)

    def reject_phase_3(self) -> None:
        self._transition(AuditStatus.QA_REJECTED_PHASE_3)

    def error_phase_3(self) -> None:
        self._transition(AuditStatus.ERROR_PHASE_3)

    def retry_phase_3(self) -> None:
        self._transition(AuditStatus.RUNNING_PHASE_3)

    def complete_audit(self) -> None:
        self._transition(AuditStatus.COMPLETED)

    def approve_gate_3(self) -> None:
        self._transition(AuditStatus.COMPLETED)
