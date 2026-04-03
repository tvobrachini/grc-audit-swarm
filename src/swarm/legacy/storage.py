import sqlite3
import os
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from swarm.evidence import ControlEvidence, ToolEvidence
from swarm.state.schema import (
    AuditAction,
    AuditFinding,
    AuditProcedure,
    ControlMatrixItem,
)

# Centralized Database Configuration
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../data")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "audit_checkpoints.sqlite")

# Keep connection open for the lifecycle of the app
_conn = None

_LEGACY_MSGPACK_SYMBOLS = (
    ("src.swarm.state.schema", "AuditAction"),
    ("src.swarm.state.schema", "AuditFinding"),
    ("src.swarm.state.schema", "AuditProcedure"),
    ("src.swarm.state.schema", "ControlMatrixItem"),
    ("src.swarm.evidence", "ControlEvidence"),
    ("src.swarm.evidence", "ToolEvidence"),
)

_CURRENT_MSGPACK_TYPES = (
    AuditAction,
    AuditFinding,
    AuditProcedure,
    ControlMatrixItem,
    ControlEvidence,
    ToolEvidence,
)


def get_db_connection():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    return _conn


def build_checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=()).with_msgpack_allowlist(
        [*_CURRENT_MSGPACK_TYPES, *_LEGACY_MSGPACK_SYMBOLS]
    )


def get_checkpointer() -> SqliteSaver:
    """
    Returns the LangGraph checkpointer interface.
    Abstracted here so swapping to PostgresSaver in the future requires modifying only this file.
    """
    conn = get_db_connection()
    return SqliteSaver(conn, serde=build_checkpoint_serializer())
