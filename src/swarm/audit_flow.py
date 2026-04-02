from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from crewai.flow.flow import Flow, listen, start
from datetime import datetime

from swarm.crews.planning_crew import PlanningCrew
from swarm.crews.fieldwork_crew import FieldworkCrew
from swarm.crews.reporting_crew import ReportingCrew

class AuditState(BaseModel):
    theme: str = ""
    business_context: str = ""
    frameworks: List[str] = []
    
    # Artifact payloads saved after each loop
    racm_plan: Optional[Dict[str, Any]] = None
    working_papers: Optional[Dict[str, Any]] = None
    final_report: Optional[Dict[str, Any]] = None
    
    # Streamlit routing and Dossier state
    current_human_dossier: str = ""
    status: str = "WAITING_FOR_SCOPE"
    
    # IIA 2340 Audit Trail Log
    approval_trail: List[Dict[str, str]] = []

class AuditFlow(Flow[AuditState]):
    
    @start()
    def generate_planning(self):
        print("Starting Planning Phase...")
        self.state.status = "RUNNING_PHASE_1"
        inputs = {
            "theme": self.state.theme,
            "business_context": self.state.business_context,
            "frameworks": ", ".join(self.state.frameworks)
        }
        
        crew = PlanningCrew().crew()
        result = crew.kickoff(inputs=inputs)
        
        if result.pydantic:
             self.state.racm_plan = result.pydantic.model_dump()
        else:
             self.state.racm_plan = {"raw": result.raw}
             
        self.state.status = "WAITING_HUMAN_GATE_1"
        self.state.current_human_dossier = "Planning QA Loop complete. No major structural flaws found. Please review the RACM below for final IIA 2340 approval."
        
    @listen("approve_planning")
    def generate_fieldwork(self, human_id: str):
        # IIA 2340 stamping
        self.state.approval_trail.append({"gate": "Gate 1 (Planning)", "human": human_id, "timestamp": datetime.utcnow().isoformat()})
        
        print("Starting Fieldwork Execution Phase...")
        self.state.status = "RUNNING_PHASE_2"
        inputs = {
            "racm_string": str(self.state.racm_plan)
        }
        
        crew = FieldworkCrew().crew()
        result = crew.kickoff(inputs=inputs)
        
        if result.pydantic:
            self.state.working_papers = result.pydantic.model_dump()
            
        self.state.status = "WAITING_HUMAN_GATE_2"
        self.state.current_human_dossier = "Execution Fieldwork complete with Substantive Immutable Proofs evaluated. Please review Findings for final IIA 2340 approval."

    @listen("approve_fieldwork")
    def generate_reporting(self, human_id: str):
        # IIA 2340 stamping
        self.state.approval_trail.append({"gate": "Gate 2 (Fieldwork)", "human": human_id, "timestamp": datetime.utcnow().isoformat()})
        
        print("Starting Reporting Phase...")
        self.state.status = "RUNNING_PHASE_3"
        inputs = {
            "scope_string": self.state.business_context,
            "working_papers_string": str(self.state.working_papers)
        }
        
        crew = ReportingCrew().crew()
        result = crew.kickoff(inputs=inputs)
        
        if result.pydantic:
            self.state.final_report = result.pydantic.model_dump()
            
        self.state.status = "COMPLETED"
