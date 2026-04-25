import yaml
from pathlib import Path
from crewai import Agent, Crew, Process, Task
from swarm.schema import WorkingPaperSchema, QA_PushbackSchema
from swarm.tools.aws_tools import (
    get_iam_password_policy,
    list_iam_users_with_mfa,
    list_public_s3_buckets,
)
from swarm.llm_factory import get_crew_llm


class FieldworkCrew:
    def __init__(self, event_callback=None):
        self._event_callback = event_callback
        base_dir = Path(__file__).parent.parent
        with open(base_dir / "config" / "fieldwork_agents.yaml", "r") as f:
            self.agents_config = yaml.safe_load(f)
        with open(base_dir / "config" / "fieldwork_tasks.yaml", "r") as f:
            self.tasks_config = yaml.safe_load(f)

    def crew(self) -> Crew:
        base_llm = get_crew_llm(temperature=0.1)
        collector = Agent(
            **self.agents_config["evidence_collector"],
            verbose=True,
            llm=base_llm,
            max_iter=5,
            tools=[
                get_iam_password_policy,
                list_iam_users_with_mfa,
                list_public_s3_buckets,
            ],
        )
        auditor = Agent(
            **self.agents_config["field_auditor"],
            verbose=True,
            llm=base_llm,
            max_iter=5,
        )

        # Anti-Hallucination: QA runs at temp 0.0
        qa_llm = get_crew_llm(temperature=0.0)
        qa_reviewer = Agent(
            **self.agents_config["qa_field_reviewer"],
            verbose=True,
            llm=qa_llm,
            max_iter=3,
        )

        collection_task = Task(
            **self.tasks_config["evidence_collection_task"], agent=collector
        )
        evaluation_task = Task(
            **self.tasks_config["execution_evaluation_task"],
            agent=auditor,
            output_pydantic=WorkingPaperSchema,
            context=[collection_task],  # only the collected evidence
        )
        qa_task = Task(
            **self.tasks_config["eval_qa_gate_task"],
            agent=qa_reviewer,
            output_pydantic=QA_PushbackSchema,
            context=[evaluation_task],  # only the working papers
        )

        return Crew(
            agents=[collector, auditor, qa_reviewer],
            tasks=[collection_task, evaluation_task, qa_task],
            process=Process.sequential,
            verbose=True,
            max_rpm=20,
            step_callback=self._event_callback,
        )
