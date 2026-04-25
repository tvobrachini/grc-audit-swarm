import yaml
from pathlib import Path
from crewai import Agent, Crew, Process, Task
from swarm.schema import FinalReportSchema, QA_PushbackSchema, OSCAL_SAR_Schema
from swarm.llm_factory import get_crew_llm


class ReportingCrew:
    def __init__(self):
        base_dir = Path(__file__).parent.parent
        with open(base_dir / "config" / "reporting_agents.yaml", "r") as f:
            self.agents_config = yaml.safe_load(f)
        with open(base_dir / "config" / "reporting_tasks.yaml", "r") as f:
            self.tasks_config = yaml.safe_load(f)

    def crew(self) -> Crew:
        base_llm = get_crew_llm(temperature=0.1)
        writer = Agent(
            **self.agents_config["lead_writer"], verbose=True, llm=base_llm, max_iter=5
        )
        concluder = Agent(
            **self.agents_config["concluder"], verbose=True, llm=base_llm, max_iter=5
        )
        # Tone adherence must be perfectly objective (temperature 0)
        qa_llm = get_crew_llm(temperature=0.0)
        qa_reviewer = Agent(
            **self.agents_config["qa_tone_reviewer"],
            verbose=True,
            llm=qa_llm,
            max_iter=3,
        )
        oscal_engineer = Agent(
            **self.agents_config["oscal_engineer"],
            verbose=True,
            llm=base_llm,
            max_iter=5,
        )

        drafting = Task(**self.tasks_config["drafting_task"], agent=writer)
        summary = Task(
            **self.tasks_config["executive_summary_task"],
            agent=concluder,
            context=[drafting],  # only the full report, not working papers too
        )
        qa = Task(
            **self.tasks_config["tone_qa_task"],
            agent=qa_reviewer,
            output_pydantic=QA_PushbackSchema,
            context=[drafting, summary],  # review both sections
        )
        oscal = Task(
            **self.tasks_config["generate_oscal_sar_task"],
            agent=oscal_engineer,
            output_pydantic=OSCAL_SAR_Schema,
            context=[drafting],  # Needs the technical details to map to OSCAL
        )
        assembly = Task(
            **self.tasks_config["final_report_assembly_task"],
            agent=writer,
            output_pydantic=FinalReportSchema,
            context=[drafting, summary, oscal],  # assemble from text and OSCAL sections
        )

        return Crew(
            agents=[writer, concluder, qa_reviewer, oscal_engineer],
            tasks=[drafting, summary, qa, oscal, assembly],
            process=Process.sequential,
            verbose=True,
            max_rpm=20,
        )
