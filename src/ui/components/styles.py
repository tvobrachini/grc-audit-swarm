import streamlit as st


def inject_swarm_css():
    """Injects custom CSS for the Swarm GRC Dashboard."""
    st.markdown(
        """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    /* ── Base ─────────────────────────────────────────────────────────────── */
    .stApp { background:#0d0d0f; color:#e8e8e8; font-family:'Inter',sans-serif; }
    h1,h2,h3 { color:#fff!important; font-weight:700!important; letter-spacing:-0.02em; }

    /* ── Sidebar ──────────────────────────────────────────────────────────── */
    [data-testid="stSidebar"] {
        background:linear-gradient(180deg,#111116 0%,#0d0d0f 100%);
        border-right:1px solid #1e1e2e;
    }

    /* ── Chat / Activity Log ──────────────────────────────────────────────── */
    .stChatMessage { background:#16161e!important; border:1px solid #1e1e2e; border-radius:12px; margin-bottom:8px; }

    /* ── Buttons ──────────────────────────────────────────────────────────── */
    .stButton>button {
        background:linear-gradient(135deg,#6366f1,#4f46e5);
        color:#fff; border:none; border-radius:8px;
        font-weight:600; transition:all .2s;
    }
    .stButton>button:hover { transform:translateY(-1px); box-shadow:0 4px 20px rgba(99,102,241,.4); }

    /* ── Tabs ─────────────────────────────────────────────────────────────── */
    .stTabs [role="tab"] { background:#16161e; border:1px solid #1e1e2e; border-radius:8px 8px 0 0; }
    .stTabs [role="tab"][aria-selected="true"] { background:#6366f1; color:#fff; }

    /* ── Expanders / Inputs ───────────────────────────────────────────────── */
    .stExpander { background:#16161e; border:1px solid #1e1e2e; border-radius:8px; }
    .stTextInput>div>div>input,
    .stTextArea>div>div>textarea {
        background:#16161e; color:#e8e8e8;
        border:1px solid #2e2e3e; border-radius:8px;
    }

    /* ── Hero / KPI ───────────────────────────────────────────────────────── */
    .hero-badge {
        display:inline-block;
        background:linear-gradient(135deg,#6366f1,#8b5cf6);
        color:#fff; padding:4px 12px; border-radius:20px;
        font-size:12px; font-weight:600; margin-bottom:8px;
    }
    .kpi-card { background:#16161e; border:1px solid #2e2e3e; border-radius:10px; padding:16px; text-align:center; }

    /* ── Finding rows ─────────────────────────────────────────────────────── */
    .finding-row { padding:10px 14px; border-radius:8px; margin-bottom:6px; border:1px solid #2e2e3e; }
    .finding-pass { border-left:4px solid #22c55e; }
    .finding-exception { border-left:4px solid #f59e0b; }
    .finding-fail { border-left:4px solid #ef4444; }

    /* ── Step badges ──────────────────────────────────────────────────────── */
    .step-pass { color:#22c55e; font-weight:600; }
    .step-fail { color:#ef4444; font-weight:600; }
    .step-exception { color:#f59e0b; font-weight:600; }

    /* ── Phase progress strip ─────────────────────────────────────────────── */
    .phase-strip {
        display:flex; align-items:center;
        padding:18px 0 22px 0; gap:0;
        overflow-x:auto; scrollbar-width:none;
    }
    .phase-strip::-webkit-scrollbar { display:none; }
    .phase-step { display:flex; flex-direction:column; align-items:center; gap:7px; min-width:72px; }
    .phase-dot {
        width:30px; height:30px; border-radius:50%;
        display:flex; align-items:center; justify-content:center;
        font-size:11px; font-weight:700;
        border:2px solid #2e2e3e; background:#16161e; color:#4b5563;
        transition:all .3s;
    }
    .phase-dot.done { background:#6366f1; border-color:#6366f1; color:#fff; }
    .phase-dot.active {
        background:#0d0d0f; border-color:#6366f1; color:#6366f1;
        box-shadow:0 0 0 4px rgba(99,102,241,.15);
        animation:phase-pulse 2s ease-in-out infinite;
    }
    @keyframes phase-pulse {
        0%,100% { box-shadow:0 0 0 4px rgba(99,102,241,.15); }
        50%      { box-shadow:0 0 0 8px rgba(99,102,241,.05); }
    }
    .phase-label { font-size:10px; color:#4b5563; white-space:nowrap; font-weight:500; letter-spacing:.02em; text-transform:uppercase; }
    .phase-label.done  { color:#a5b4fc; }
    .phase-label.active{ color:#e8e8e8; font-weight:600; }
    .phase-connector { flex:1; height:2px; background:#1e1e2e; margin-bottom:22px; min-width:16px; max-width:60px; transition:background .3s; }
    .phase-connector.done { background:#6366f1; }

    /* ── Skill badges ─────────────────────────────────────────────────────── */
    .skill-pill {
        display:inline-block;
        background:rgba(99,102,241,.12); border:1px solid rgba(99,102,241,.3);
        color:#a5b4fc; padding:3px 10px; border-radius:20px;
        font-size:11px; font-weight:600; margin:2px 4px 2px 0;
        letter-spacing:.02em;
    }
    .skill-strip { margin:8px 0 16px 0; }

    /* ── Finding card sections ────────────────────────────────────────────── */
    .finding-section-label {
        font-size:10px; font-weight:700; text-transform:uppercase;
        letter-spacing:.08em; color:#4b5563; margin-bottom:4px;
    }
    .finding-narrative { color:#d1d5db; line-height:1.65; }

    /* ── Scope char counter ───────────────────────────────────────────────── */
    .char-ok   { color:#22c55e; font-size:12px; }
    .char-warn { color:#f59e0b; font-size:12px; }
    .char-info { color:#6b7280; font-size:12px; }

    /* ── Status badge (sidebar) ───────────────────────────────────────────── */
    .status-badge {
        display:inline-block; font-size:10px; font-weight:600;
        padding:1px 7px; border-radius:10px; margin-left:6px;
        vertical-align:middle; letter-spacing:.02em;
    }
    .status-planning  { background:rgba(99,102,241,.15); color:#a5b4fc; border:1px solid rgba(99,102,241,.3); }
    .status-review    { background:rgba(245,158,11,.12);  color:#fcd34d; border:1px solid rgba(245,158,11,.3); }
    .status-complete  { background:rgba(34,197,94,.12);   color:#86efac; border:1px solid rgba(34,197,94,.3); }
    .status-draft     { background:rgba(107,114,128,.12); color:#9ca3af; border:1px solid rgba(107,114,128,.3); }
    </style>
    """,
        unsafe_allow_html=True,
    )
