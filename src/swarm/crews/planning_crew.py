import yaml
from pathlib import Path
from crewai import Agent, Crew, Process, Task
from swarm.schema import RiskControlMatrixSchema, QA_PushbackSchema
from swarm.llm_factory import get_crew_llm


class PlanningCrew:
    def __init__(self, event_callback=None, skill_context=None):
        self._event_callback = event_callback
        self._skill_context = skill_context or []
        base_dir = Path(__file__).parent.parent
        with open(base_dir / "config" / "planning_agents.yaml", "r") as f:
            self.agents_config = yaml.safe_load(f)
        with open(base_dir / "config" / "planning_tasks.yaml", "r") as f:
            self.tasks_config = yaml.safe_load(f)

    def crew(self) -> Crew:
        # Dynamically map the LLM based on user's active API keys
        base_llm = get_crew_llm(temperature=0.1)

        # Instantiate Agents based on YAML configs
        orchestrator = Agent(
            **self.agents_config["orchestrator"], verbose=True, llm=base_llm, max_iter=5
        )
        analyst = Agent(
            **self.agents_config["analyst"], verbose=True, llm=base_llm, max_iter=5
        )

        # Augment specialist backstory with domain-specific skill prompts if detected
        specialist_config = dict(self.agents_config["specialist"])
        if self._skill_context:
            from swarm.skill_loader import get_specialist_prompt

            extra = get_specialist_prompt(self._skill_context)
            specialist_config["backstory"] = (
                specialist_config.get("backstory", "") + "\n\n" + extra
            ).strip()
        specialist = Agent(**specialist_config, verbose=True, llm=base_llm, max_iter=5)

        auditor = Agent(
            **self.agents_config["auditor"], verbose=True, llm=base_llm, max_iter=5
        )

        # IIA Anti-Hallucination: QA Reviewer runs strictly at Temperature 0.0
        qa_llm = get_crew_llm(temperature=0.0)
        qa_reviewer = Agent(
            **self.agents_config["qa_reviewer"], verbose=True, llm=qa_llm, max_iter=3
        )

        # Instantiate Tasks with explicit names for result_adapter lookup
        context_task = Task(
            **self.tasks_config["context_task"], name="context_task", agent=orchestrator
        )
        crosswalk_task = Task(
            **self.tasks_config["crosswalk_task"], name="crosswalk_task", agent=analyst
        )
        weighting_task = Task(
            **self.tasks_config["weighting_task"],
            name="weighting_task",
            agent=specialist,
        )

        # Pydantic Enforcement.
        # Context is limited explicitly to avoid stacking all prior outputs
        # into a single prompt — Groq/free-tier providers cap single requests at ~6k tokens.
        racm_task = Task(
            **self.tasks_config["racm_drafting_task"],
            name="racm_drafting_task",
            agent=auditor,
            output_pydantic=RiskControlMatrixSchema,
            context=[],  # no prior task context — inputs injected via kickoff; keeps request under 6k TPM
        )
        qa_task = Task(
            **self.tasks_config["qa_gate_task"],
            name="qa_gate_task",
            agent=qa_reviewer,
            output_pydantic=QA_PushbackSchema,
            context=[racm_task],  # only needs the RACM to review
        )

        return Crew(
            agents=[orchestrator, analyst, specialist, auditor, qa_reviewer],
            tasks=[context_task, crosswalk_task, weighting_task, racm_task, qa_task],
            process=Process.sequential,
            verbose=True,
            max_rpm=20,
            step_callback=self._event_callback,
        )
