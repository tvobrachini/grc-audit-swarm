import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.storage import build_checkpoint_serializer


def test_checkpoint_serializer_allows_current_and_legacy_symbols():
    serializer = build_checkpoint_serializer()
    allowlist = serializer._allowed_msgpack_modules

    assert ("swarm.state.schema", "AuditAction") in allowlist
    assert ("swarm.state.schema", "ControlMatrixItem") in allowlist
    assert ("swarm.evidence", "ControlEvidence") in allowlist
    assert ("src.swarm.state.schema", "AuditAction") in allowlist
    assert ("src.swarm.state.schema", "AuditFinding") in allowlist
    assert ("src.swarm.evidence", "ToolEvidence") in allowlist
