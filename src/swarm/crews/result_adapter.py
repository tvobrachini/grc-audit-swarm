from crewai.crews.crew_output import CrewOutput
from crewai.tasks.task_output import TaskOutput


class CrewResultAdapter:
    """Maps CrewAI task outputs by name instead of array index."""

    def __init__(self, crew_output: CrewOutput) -> None:
        self._output = crew_output
        self._by_name: dict[str, TaskOutput] = {
            t.name: t for t in crew_output.tasks_output if t.name
        }

    def get(self, task_name: str) -> TaskOutput:
        if task_name not in self._by_name:
            available = list(self._by_name.keys())
            raise KeyError(
                f"Task '{task_name}' not found in crew output. "
                f"Available: {available}. "
                "Ensure Task(..., name='{task_name}') is set in the crew definition."
            )
        return self._by_name[task_name]

    @property
    def final(self) -> CrewOutput:
        return self._output
