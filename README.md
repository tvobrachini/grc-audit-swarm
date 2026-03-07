# GRC Audit Swarm

> AI Multi-Agent GRC Audit Planning System

An interactive, stateful AI **Audit Planning Copilot** powered by a LangGraph Swarm of specialized agents. Upload a scope narrative, and a team of AI agents will research real-world risk data, map SCF controls, design detailed test procedures, and present the artifacts for your human review — all running on **free, open-source Groq LLMs**.

## 🎯 Key Features

- **🤖 Multi-Agent Swarm** — 5 specialized agents (Orchestrator, Researcher, Control Mapper, Specialist, Challenger) collaborate and push back on each other before surfacing artifacts
- **📡 Real-World Research** — The Researcher agent uses live web search (DuckDuckGo) to extract recent breaches, fines, and regulatory actions to ground the audit in facts
- **📄 1-Pager Risk Context** — Fact-based document with citations proving why specific controls matter *right now*
- **📋 Control Matrix** — SCF-mapped controls with TOD, TOE, Substantive, and Evidence Request List (ERL) steps
- **🔁 Human-in-the-Loop** — Review → Provide feedback → Agents revise → Repeat until approved
- **💾 Persistent Sessions** — Audit state saved to SQLite; resume any past audit from the sidebar
- **🆓 Groq-First (Free)** — Defaults to `llama-3.3-70b-versatile` via Groq free tier; falls back to OpenAI if configured

## 🚀 Quickstart

```bash
# 1. Clone
git clone https://github.com/YOUR_HANDLE/grc-audit-swarm
cd grc-audit-swarm

# 2. Install (uv recommended)
uv install

# 3. Configure
cp .env.example .env
# Edit .env → add your GROQ_API_KEY (free at console.groq.com)

# 4. Run
uv run streamlit run app.py
```

## 🏗️ Architecture

```
Scope → Orchestrator → Researcher (web search + 1-Pager)
      → Control Mapper (SCF + risk context) → Specialist → Challenger
      → Human Review (checkpoint) → Feedback Loop → Approved
```

## 🔑 Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | Recommended | Free at [console.groq.com](https://console.groq.com) |
| `OPENAI_API_KEY` | Optional | Fallback if no Groq key |

> **No API key?** The app runs in mock mode with simulated agent outputs — ideal for UI testing.
