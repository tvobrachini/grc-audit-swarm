# 🎯 GRC Audit Swarm

> **AI Multi-Agent Swarm for Adaptive Audit Planning & Execution**

GRC Audit Swarm is a stateful, interactive AI platform designed to transform Audit narratives into actionable findings. Powered by **LangGraph**, it coordinates a "Swarm" of specialized agents that research real-world risks, map controls from the **Secure Controls Framework (SCF)**, and autonomously execute audit tests to generate a Findings Command Center.

> [!NOTE]
> **View the Complete Portfolio Case Study:** I've documented the architectural shift from static mapping to recursive autonomous auditing in **[CASE_STUDY.md](CASE_STUDY.md)**.

![Swarm Command Center](assets/ui_demo.png)

---

## 🛠️ The Swarm Ecosystem

### 🧠 Phase 1: Planning & Strategy (The Brain)
A collaborative team of agents researches the problem space and builds a custom audit plan:
- **Researcher:** Uses Google/DuckDuckGo to find real-world breaches and regulatory citations relevant to your scope.
- **Control Mapper:** Direct retrieval from your local SCF database (1,100+ controls) via a RAG heuristic.
- **Dynamic Specialist:** Injects domain-specific procedures (AWS, PCI-DSS, GDPR, HIPAA, ITGC) based on auto-detected scope keywords.
- **QA Challenger:** Acts as a Lead Partner, rejecting and revising weak audit plans until they meet "Partner" standards.
- **Human Snapshot:** Execution pauses for your approval of the 1-Pager Risk Context and the Control Matrix.

### ⚙️ Phase 2: Execution & Findings (The Engine)
Once approved, the swarm shifts from planning to testing:
- **Worker Agents:** Each control is assigned to a worker that "interviews" evidence (simulated or real) and performs Test of Design (TOD) and Test of Effectiveness (TOE).
- **Specialist Annotator:** Tags failed findings with technical root causes and remediation SLAs.
- **Foundations Researcher:** Cross-references failures against historical breach patterns for risk calibration.
- **Execution Challenger:** Validates finding consistency (e.g., flags "Pass" statuses that have zero extracted evidence).
- **Executive Concluder:** Aggregates all data into a high-level risk score and Executive Summary.

---

## 🚀 Key Features

- **📡 Live Research:** Integrated web search (DDGS) for grounding audits in current threat data.
- **🔌 Loadable Skill Modules:** Define specialized audit logic in YAML files (`skills/`) for easy extensibility.
- **📊 Findings Command Center:** Interactive UI with KPI bars, expandable control drill-downs, and per-step result badges (✅/❌/⚠️).
- **🛡️ Guardrail Test Suite:** 69+ unit tests covering agent contracts, graph topology, and schema validation.
- **💾 persistent JSON/SQLite Sessions:** Full history of past audits; resume any session from the sidebar.
- **📥 Open-Source Power:** Built with **LangGraph**, **Pydantic**, and **Streamlit**; optimized for free **Groq Llama-3** models.

---

## 🏃 Quickstart

```bash
# 1. Clone
git clone https://github.com/tvobrachini/grc-audit-swarm
cd grc-audit-swarm

# 2. Install Dependencies (uv recommended)
uv sync

# 3. Environment Setup
cp .env.example .env
# Edit .env and add your GROQ_API_KEY

# 4. Launch the Command Center
uv run streamlit run app.py --server.port 8502
```

---

## 🔬 Reliability & Testing
This project follows an "Audit Engineering" mindset. To run the full verification suite:

```bash
uv run pytest tests/ -v
```

The suite covers:
- **Agents:** Contract compliance for all 12+ agent nodes.
- **Graph:** Topology validation (ensuring Phase 1 -> Phase 2 flow is unbroken).
- **Skills:** Auto-detection accuracy across 5 key domains.
- **Schema:** Strict Pydantic data modeling for findings and state.

---

## 🔗 Repository Roles
- **[scf-auto-crosswalker](https://github.com/tvobrachini/scf-auto-crosswalker):** The Master Data Hub hosting the SCF framework parsed JSON.
- **[grc-audit-swarm](https://github.com/tvobrachini/grc-audit-swarm):** (This Repo) The Recursive Execution Engine for swarm-based auditing.

---
*Developed by TVobrachini. Open-source under CC BY-ND 4.0.*
