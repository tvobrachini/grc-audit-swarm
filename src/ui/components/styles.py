import streamlit as st


def inject_swarm_css():
    """Injects custom CSS for the Swarm GRC Dashboard."""
    st.markdown(
        """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    .stApp { background:#0d0d0f; color:#e8e8e8; font-family:'Inter',sans-serif; }
    h1,h2,h3 { color:#fff!important; font-weight:700!important; letter-spacing:-0.02em; }
    [data-testid="stSidebar"] { background:linear-gradient(180deg,#111116 0%,#0d0d0f 100%); border-right:1px solid #1e1e2e; }
    .stChatMessage { background:#16161e!important; border:1px solid #1e1e2e; border-radius:12px; margin-bottom:8px; }
    .stButton>button { background:linear-gradient(135deg,#6366f1,#4f46e5); color:#fff; border:none; border-radius:8px; font-weight:600; transition:all .2s; }
    .stButton>button:hover { transform:translateY(-1px); box-shadow:0 4px 20px rgba(99,102,241,.4); }
    .stTabs [role="tab"] { background:#16161e; border:1px solid #1e1e2e; border-radius:8px 8px 0 0; }
    .stTabs [role="tab"][aria-selected="true"] { background:#6366f1; color:#fff; }
    .stExpander { background:#16161e; border:1px solid #1e1e2e; border-radius:8px; }
    .stTextInput>div>div>input,.stTextArea>div>div>textarea { background:#16161e; color:#e8e8e8; border:1px solid #2e2e3e; border-radius:8px; }
    .hero-badge { display:inline-block; background:linear-gradient(135deg,#6366f1,#8b5cf6); color:#fff; padding:4px 12px; border-radius:20px; font-size:12px; font-weight:600; margin-bottom:8px; }
    .finding-row { padding:10px 14px; border-radius:8px; margin-bottom:6px; border:1px solid #2e2e3e; }
    .finding-pass { border-left:4px solid #22c55e; }
    .finding-exception { border-left:4px solid #f59e0b; }
    .finding-fail { border-left:4px solid #ef4444; }
    .kpi-card { background:#16161e; border:1px solid #2e2e3e; border-radius:10px; padding:16px; text-align:center; }
    .step-pass { color:#22c55e; font-weight:600; }
    .step-fail { color:#ef4444; font-weight:600; }
    .step-exception { color:#f59e0b; font-weight:600; }
    </style>
    """,
        unsafe_allow_html=True,
    )
