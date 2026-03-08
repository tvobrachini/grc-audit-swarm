import sqlite3
import os
from langgraph.checkpoint.sqlite import SqliteSaver

# Centralized Database Configuration
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../data")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "audit_checkpoints.sqlite")

# Keep connection open for the lifecycle of the app
_conn = None


def get_db_connection():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    return _conn


def get_checkpointer() -> SqliteSaver:
    """
    Returns the LangGraph checkpointer interface.
    Abstracted here so swapping to PostgresSaver in the future requires modifying only this file.
    """
    conn = get_db_connection()
    return SqliteSaver(conn)
