import yaml
from pathlib import Path
from crewai import Agent, Crew, Process, Task, LLM
from swarm.schema import RiskControlMatrixSchema, QA_PushbackSchema
from swarm.llm_factory import get_crew_llm

class PlanningCrew:
    def __init__(self):
        base_dir = Path(__file__).parent.parent
        with open(base_dir / "config" / "planning_agents.yaml", 'r') as f:
            self.agents_config = yaml.safe_load(f)
        with open(base_dir / "config" / "planning_tasks.yaml", 'r') as f:
            self.tasks_config = yaml.safe_load(f)

    def crew(self) -> Crew:
        # Dynamically map the LLM based on user's active API keys
        base_llm = get_crew_llm(temperature=0.1)
        
        # Instantiate Agents based on YAML configs
        orchestrator = Agent(**self.agents_config['orchestrator'], verbose=True, llm=base_llm)
        analyst = Agent(**self.agents_config['analyst'], verbose=True, llm=base_llm)
        specialist = Agent(**self.agents_config['specialist'], verbose=True, llm=base_llm)
        auditor = Agent(**self.agents_config['auditor'], verbose=True, llm=base_llm)
        
        # IIA Anti-Hallucination: QA Reviewer runs strictly at Temperature 0.0
        qa_llm = get_crew_llm(temperature=0.0)
        qa_reviewer = Agent(**self.agents_config['qa_reviewer'], verbose=True, llm=qa_llm)

        # Instantiate Tasks dynamically
        context_task = Task(**self.tasks_config['context_task'], agent=orchestrator)
        crosswalk_task = Task(**self.tasks_config['crosswalk_task'], agent=analyst)
        weighting_task = Task(**self.tasks_config['weighting_task'], agent=specialist)
        
        # Pydantic Enforcement
        racm_task = Task(**self.tasks_config['racm_drafting_task'], agent=auditor, output_pydantic=RiskControlMatrixSchema)
        qa_task = Task(**self.tasks_config['qa_gate_task'], agent=qa_reviewer, output_pydantic=QA_PushbackSchema)

        return Crew(
            agents=[orchestrator, analyst, specialist, auditor, qa_reviewer],
            tasks=[context_task, crosswalk_task, weighting_task, racm_task, qa_task],
            process=Process.sequential,
            verbose=True
        )
