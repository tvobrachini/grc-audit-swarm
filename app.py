"""
GRC Audit Swarm — Standalone Streamlit Application
====================================================
Multi-agent LangGraph system for AI-powered IT Audit Planning.

Agents:
  1. Orchestrator   — extracts themes from scope
  6. Researcher     — builds 1-Pager with real-world citations (Groq + DuckDuckGo)
  2/3. Mapper       — maps SCF controls using the 1-Pager context
  4. Specialist     — injects deep technical test steps
  5. Challenger     — QA review before human checkpoint

Human-in-the-loop via LangGraph MemorySaver → sqliteSaver (persists across sessions).
"""
import os
import sys
import uuid
from dotenv import load_dotenv

# Load .env variables (GROQ_API_KEY, OPENAI_API_KEY, etc.)
load_dotenv()

sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

import streamlit as st
import json
import pandas as pd
import pdfplumber

from swarm.graph import app as swarm_app
from swarm.session_manager import save_session, list_sessions, delete_session

# ─── Page Config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="GRC Audit Swarm",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ─── Premium Dark CSS ─────────────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    .stApp { background: #0d0d0f; color: #e8e8e8; font-family: 'Inter', sans-serif; }
    h1, h2, h3 { color: #ffffff !important; font-weight: 700 !important; letter-spacing: -0.02em; }
    
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #111116 0%, #0d0d0f 100%);
        border-right: 1px solid #1e1e2e;
    }

    .stChatMessage { background: #16161e !important; border: 1px solid #1e1e2e; border-radius: 12px; margin-bottom: 8px; }
    
    .stButton > button {
        background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
        color: white; border: none; border-radius: 8px;
        font-weight: 600; transition: all 0.2s ease;
    }
    .stButton > button:hover { transform: translateY(-1px); box-shadow: 0 4px 20px rgba(99,102,241,0.4); }

    .stTabs [role="tab"] { background: #16161e; border: 1px solid #1e1e2e; border-radius: 8px 8px 0 0; }
    .stTabs [role="tab"][aria-selected="true"] { background: #6366f1; color: white; }

    .stExpander { background: #16161e; border: 1px solid #1e1e2e; border-radius: 8px; }
    .stTextInput > div > div > input { background: #16161e; color: #e8e8e8; border: 1px solid #2e2e3e; border-radius: 8px; }
    .stTextArea > div > div > textarea { background: #16161e; color: #e8e8e8; border: 1px solid #2e2e3e; border-radius: 8px; }
    .stSelectbox > div > div { background: #16161e; color: #e8e8e8; border: 1px solid #2e2e3e; border-radius: 8px; }
    
    div[data-testid="stDataFrame"] { border: 1px solid #1e1e2e; border-radius: 8px; }

    .hero-badge {
        display: inline-block; background: linear-gradient(135deg, #6366f1, #8b5cf6);
        color: white; padding: 4px 12px; border-radius: 20px; font-size: 12px;
        font-weight: 600; margin-bottom: 8px;
    }
</style>
""", unsafe_allow_html=True)

# ─── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="hero-badge">🤖 Powered by Groq + LangGraph</div>', unsafe_allow_html=True)
    st.title("GRC Audit Swarm")
    st.caption("AI Multi-Agent Audit Planning System")
    st.markdown("---")

    # LLM status indicator
    groq_key = os.environ.get("GROQ_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    if groq_key:
        st.success("✅ Groq (llama-3.3-70b)")
    elif openai_key:
        st.warning("⚠️ OpenAI fallback active (Groq key missing)")
    else:
        st.info("ℹ️ Mock mode — no API key found")

    st.markdown("---")
    st.header("🗂️ Audit History")

    sessions = list_sessions()
    if sessions:
        for tid, meta in sessions.items():
            created = meta.get("created_at", "")[:10]
            c1, c2 = st.columns([4, 1])
            if c1.button(f"📂 **{meta['name']}**\n`{created}`", key=f"load_{tid}", use_container_width=True):
                st.session_state.thread_id = tid
                st.session_state.scope_submitted = True
                st.session_state.chat_history = meta.get("chat_history", [])
                st.rerun()
            if c2.button("🗑️", key=f"del_{tid}", help="Delete this audit"):
                delete_session(tid)
                st.rerun()
    else:
        st.caption("No saved audits yet. Run your first swarm to get started.")

    st.markdown("---")
    st.caption("Licensed under CC Attribution-NoDerivatives 4.0")

# ─── Session State Defaults ────────────────────────────────────────────────────
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "scope_submitted" not in st.session_state:
    st.session_state.scope_submitted = False
if "scope_text_cache" not in st.session_state:
    st.session_state.scope_text_cache = ""

config = {"configurable": {"thread_id": st.session_state.thread_id}}

LAB_DATA_DIR = os.path.join(os.path.dirname(__file__), "lab_data")

def load_lab_files(extension=None):
    if not os.path.exists(LAB_DATA_DIR):
        return []
    files = [f for f in os.listdir(LAB_DATA_DIR) if os.path.isfile(os.path.join(LAB_DATA_DIR, f))]
    if extension:
        files = [f for f in files if f.endswith(extension)]
    return files

# ─── Main Area ─────────────────────────────────────────────────────────────────
st.title("🎯 Swarm Audit Command Center")
st.markdown("Submit a scope narrative and let the AI multi-agent swarm research, map controls, and design an industry-grade audit plan — ready for your human review.")

# ── Phase 0: Scope Input ────────────────────────────────────────────────────────
if not st.session_state.scope_submitted:
    scope_text = ""
    colA, colB = st.columns([1, 1])
    with colA:
        st.markdown("### 📄 Upload Scope Document")
        uploaded_scope = st.file_uploader("Upload PDF or TXT", type=["pdf", "txt"], key="scope_up")
        if uploaded_scope:
            if uploaded_scope.name.endswith(".pdf"):
                with pdfplumber.open(uploaded_scope) as pdf:
                    scope_text = "\n".join([p.extract_text() for p in pdf.pages if p.extract_text()])
            else:
                scope_text = uploaded_scope.getvalue().decode("utf-8")

        lab_txt = load_lab_files(extension=".txt")
        selected_lab = st.selectbox("Or pick from Lab Data", ["None"] + lab_txt, key="scope_lab")
        if selected_lab != "None" and not scope_text:
            with open(os.path.join(LAB_DATA_DIR, selected_lab), "r", encoding="utf-8") as f:
                scope_text = f.read()

    with colB:
        st.markdown("### 🔍 Scope Preview")
        if scope_text:
            st.text_area("Scope Content (Editable)", value=scope_text, height=200, key="scope_text_area")
            scope_text = st.session_state.scope_text_area
        else:
            st.info("Upload a document or select lab data to preview scope.")

    st.markdown("---")
    audit_name = st.text_input(
        "📝 Audit Name",
        placeholder="e.g.  AWS Prod Q4 2026 – IAM & Access Control Review",
        key="audit_name_input"
    )
    colbtn1, colbtn2, colbtn3 = st.columns([1, 2, 1])
    if colbtn2.button("🚀 Launch Swarm", type="primary", use_container_width=True, key="scope_btn"):
        if not scope_text:
            st.warning("Please provide scope text first.")
        else:
            session_name = audit_name.strip() if audit_name.strip() else f"Audit {st.session_state.thread_id[:8]}"
            save_session(
                thread_id=st.session_state.thread_id,
                name=session_name,
                scope_preview=scope_text
            )
            st.session_state.scope_text_cache = scope_text
            st.session_state.scope_submitted = True
            st.session_state.chat_history.append({
                "role": "user",
                "content": f"**🚀 Launching swarm:** {session_name}\n\n*Scope loaded. Agents initializing...*"
            })
            st.rerun()

# ── Phase 1+: Chat Command Center ───────────────────────────────────────────────
else:
    colHeader, colReset = st.columns([4, 1])
    with colReset:
        if st.button("🔄 New Audit", use_container_width=True):
            st.session_state.thread_id = str(uuid.uuid4())
            st.session_state.chat_history = []
            st.session_state.scope_submitted = False
            st.rerun()

    # Render chat history
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("reasoning"):
                with st.expander("🔍 View Agent Reasoning"):
                    st.markdown(msg["reasoning"])

    current_state = swarm_app.get_state(config)
    is_interrupted = len(current_state.next) > 0 and current_state.next[0] == "human_review"
    is_finished = len(current_state.next) == 0 and bool(current_state.values.get("audit_trail"))

    # ── RUNNING ──────────────────────────────────────────────────────────────────
    if not is_interrupted and not is_finished:
        with st.spinner("🤖 Swarm agents are working — researching, mapping, and debating..."):
            stream_input = (
                {"audit_scope_narrative": st.session_state.scope_text_cache, "audit_trail": []}
                if not current_state.values
                else None
            )
            for event in swarm_app.stream(stream_input, config=config, stream_mode="updates"):
                for node, state in event.items():
                    reasoning = None
                    if "audit_trail" in state and state["audit_trail"]:
                        last = state["audit_trail"][-1]
                        reasoning = last.reasoning_snapshot if hasattr(last, "reasoning_snapshot") else last.get("reasoning_snapshot")
                    msg_text = f"🟢 **`{node}`** completed"
                    st.session_state.chat_history.append({"role": "assistant", "content": msg_text, "reasoning": reasoning})
                    with st.chat_message("assistant"):
                        st.markdown(msg_text)
                        if reasoning:
                            with st.expander("🔍 View Agent Reasoning"):
                                st.markdown(reasoning)
            st.rerun()

    # ── HUMAN REVIEW ─────────────────────────────────────────────────────────────
    elif is_interrupted:
        final_state = current_state.values

        review_banner = "## 🔔 Swarm Has Completed — Your Review Required"
        if not any(m.get("content", "").startswith("## 🔔") for m in st.session_state.chat_history):
            st.session_state.chat_history.append({"role": "assistant", "content": review_banner})

        st.info("The agents have finished. Review the two artifacts below, then approve or provide feedback.")

        tab1, tab2 = st.tabs(["📄 1-Pager Risk Context", "📋 Control Matrix"])

        with tab1:
            doc = final_state.get("risk_context_document", "")
            st.markdown(doc if doc else "_No risk context document was generated._")

        with tab2:
            controls_data = []
            scf_dict = st.session_state.get("scf_dict", {})
            for ctrl in final_state.get("control_matrix", []):
                cid = ctrl.control_id if hasattr(ctrl, "control_id") else ctrl.get("control_id", "")
                desc = ctrl.description if hasattr(ctrl, "description") else ctrl.get("description", "")
                procs = ctrl.procedures if hasattr(ctrl, "procedures") else ctrl.get("procedures")
                weight = scf_dict.get(cid, {}).get("weight", "—")

                with st.expander(f"**{cid}** — {desc[:80]}…"):
                    st.markdown(f"**Domain:** {ctrl.domain if hasattr(ctrl, 'domain') else ctrl.get('domain','')}")
                    st.markdown(f"**Weight:** {weight}")
                    if procs:
                        tod = procs.tod_steps if hasattr(procs, "tod_steps") else procs.get("tod_steps", [])
                        toe = procs.toe_steps if hasattr(procs, "toe_steps") else procs.get("toe_steps", [])
                        sub = procs.substantive_steps if hasattr(procs, "substantive_steps") else procs.get("substantive_steps", [])
                        erl = procs.erl_items if hasattr(procs, "erl_items") else procs.get("erl_items", [])
                        c1, c2 = st.columns(2)
                        with c1:
                            st.markdown("**🔵 Test of Design (TOD)**")
                            for s in tod: st.markdown(f"- {s}")
                            st.markdown("**🟡 Test of Effectiveness (TOE)**")
                            for s in toe: st.markdown(f"- {s}")
                        with c2:
                            st.markdown("**🔴 Substantive Testing**")
                            for s in sub: st.markdown(f"- {s}")
                            st.markdown("**📎 Evidence Request List (ERL)**")
                            for s in erl: st.markdown(f"- {s}")

                controls_data.append({"Control ID": cid, "Description": desc, "Weight": weight})

            if controls_data:
                st.markdown("---")
                # Excel export
                import io
                questions_data, requests_data = [], []
                for ctrl in final_state.get("control_matrix", []):
                    cid = ctrl.control_id if hasattr(ctrl, "control_id") else ctrl.get("control_id", "")
                    desc = ctrl.description if hasattr(ctrl, "description") else ctrl.get("description", "")
                    procs = ctrl.procedures if hasattr(ctrl, "procedures") else ctrl.get("procedures")
                    if procs:
                        tod = procs.tod_steps if hasattr(procs, "tod_steps") else procs.get("tod_steps", [])
                        erl = procs.erl_items if hasattr(procs, "erl_items") else procs.get("erl_items", [])
                        questions_data.append({"Control ID": cid, "Test of Design Steps": "\n".join(tod)})
                        requests_data.append({"Evidence Request List Items": "\n".join(erl), "Related Controls": cid, "Request Context": desc})

                excel_buf = io.BytesIO()
                with pd.ExcelWriter(excel_buf, engine="openpyxl") as writer:
                    pd.DataFrame(controls_data).to_excel(writer, sheet_name="Controls", index=False)
                    pd.DataFrame(questions_data).to_excel(writer, sheet_name="Questions", index=False)
                    pd.DataFrame(requests_data).to_excel(writer, sheet_name="Evidence Request List", index=False)
                st.download_button(
                    "📥 Download Audit Plan (Excel)",
                    data=excel_buf.getvalue(),
                    file_name="audit_plan.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )

        st.markdown("---")
        user_feedback = st.chat_input("Type 'Approve' to finalize, or describe what to change...")
        if user_feedback:
            st.session_state.chat_history.append({"role": "user", "content": user_feedback})
            if user_feedback.strip().lower() in ["approve", "approved", "looks good", "ok", "yes", "lgtm"]:
                swarm_app.update_state(config, {"revision_feedback": ""})
            else:
                swarm_app.update_state(config, {"revision_feedback": user_feedback})
            st.rerun()

    # ── FINISHED ──────────────────────────────────────────────────────────────────
    elif is_finished:
        st.success("✅ **Phase 1 Planning Approved!** The audit plan has been finalized.")
        st.balloons()
        if st.button("🚀 Start New Audit"):
            st.session_state.thread_id = str(uuid.uuid4())
            st.session_state.chat_history = []
            st.session_state.scope_submitted = False
            st.rerun()
