"""Scripted DEMO_MODE walk-through through the real API, in process.

Creates an audit, returns Gate 1 for rework once, records the example
reviewer decisions at Gates 2 and 3 (``swarm.demo.demo_review_decisions``;
the application never records decisions itself: this script acts as the
reviewer), approves each gate with separate declared identities, records a
management response after the report is issued, and writes the four exports
to an output directory. Used to regenerate ``docs/sample-run/``:

    uv run python scripts/demo_walkthrough.py docs/sample-run

Everything it writes is fixed demo content labelled DEMO DATA. State (the
sessions file, trail anchors and evidence vault) goes to a temporary
directory unless SESSIONS_PATH / TRAIL_ANCHORS_PATH / EVIDENCE_VAULT_PATH are
set.
"""

from __future__ import annotations

import os
import secrets
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

PREPARER = "Pat Preparer (demo)"
IN_CHARGE = "Ivan In-Charge (demo)"
MANAGER = "Mona Manager (demo)"
REWORK_NOTES = (
    "[DEMO DATA] Please confirm each control's key-control flag and the "
    "population source before fieldwork starts."
)


def _setup_environment() -> None:
    state_dir = Path(tempfile.mkdtemp(prefix="grc-demo-"))
    os.environ["DEMO_MODE"] = "1"
    os.environ.setdefault("DEMO_STEP_DELAY", "0")
    os.environ.setdefault("ENVIRONMENT", "local")
    os.environ.setdefault("API_AUTH_TOKEN", secrets.token_urlsafe(24))
    os.environ.setdefault("SESSIONS_PATH", str(state_dir / "sessions.json"))
    os.environ.setdefault("TRAIL_ANCHORS_PATH", str(state_dir / "anchors.json"))
    os.environ.setdefault("EVIDENCE_VAULT_PATH", str(state_dir / "vault"))
    sys.path.insert(0, str(ROOT / "src"))


def _wait_for(client: Any, sid: str, headers: dict, status: str) -> dict:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        detail = client.get(f"/api/sessions/{sid}", headers=headers).json()
        if detail["status"] == status:
            return detail
        if detail["status"].startswith(("ERROR", "QA_REJECTED")):
            raise RuntimeError(f"demo run stopped at {detail['status']}")
        time.sleep(0.05)
    raise TimeoutError(f"session {sid} did not reach {status}")


def _ok(response: Any, expected: int = 200) -> Any:
    if response.status_code != expected:
        raise RuntimeError(f"{response.status_code}: {response.text}")
    return response.json()


def _record(client: Any, sid: str, headers: dict, stage: str, who: str) -> None:
    from swarm.demo import demo_review_decisions

    detail = _ok(client.get(f"/api/sessions/{sid}", headers=headers))
    for body in demo_review_decisions(stage, detail["effective"]):
        _ok(
            client.post(
                f"/api/sessions/{sid}/decisions",
                headers=headers,
                json={**body, "decided_by": who},
            ),
            201,
        )


def run(out_dir: Path) -> str:
    """Run the walk-through; write the exports to ``out_dir``; return the id."""
    _setup_environment()
    from fastapi.testclient import TestClient

    from api.main import app

    headers = {"Authorization": f"Bearer {os.environ['API_AUTH_TOKEN']}"}
    with TestClient(app) as client:
        created = _ok(
            client.post(
                "/api/sessions",
                headers=headers,
                json={
                    "theme": "AWS S3 and IAM security",
                    "business_context": (
                        "[DEMO DATA] Fintech storing customer exports in S3."
                    ),
                    "prepared_by": PREPARER,
                },
            ),
            201,
        )
        sid = created["session_id"]
        _wait_for(client, sid, headers, "WAITING_HUMAN_GATE_1")
        _ok(
            client.post(
                f"/api/sessions/{sid}/return",
                headers=headers,
                json={"phase": 1, "human_id": IN_CHARGE, "notes": REWORK_NOTES},
            )
        )
        _wait_for(client, sid, headers, "WAITING_HUMAN_GATE_1")
        _ok(
            client.patch(
                f"/api/sessions/{sid}/approve",
                headers=headers,
                json={"gate_number": 1, "human_id": IN_CHARGE},
            )
        )
        _wait_for(client, sid, headers, "WAITING_HUMAN_GATE_2")
        _record(client, sid, headers, "gate2", IN_CHARGE)
        _ok(
            client.patch(
                f"/api/sessions/{sid}/approve",
                headers=headers,
                json={"gate_number": 2, "human_id": IN_CHARGE},
            )
        )
        _wait_for(client, sid, headers, "WAITING_HUMAN_GATE_3")
        _record(client, sid, headers, "gate3", MANAGER)
        _ok(
            client.patch(
                f"/api/sessions/{sid}/approve",
                headers=headers,
                json={"gate_number": 3, "human_id": MANAGER},
            )
        )
        _wait_for(client, sid, headers, "COMPLETED")
        # Management's response arrives after the report is issued; the
        # preparer transcribes it.
        _record(client, sid, headers, "after_issue", PREPARER)

        out_dir.mkdir(parents=True, exist_ok=True)
        for name in ("racm.xlsx", "working-papers.xlsx", "report.md", "oscal.json"):
            r = client.get(f"/api/sessions/{sid}/export/{name}", headers=headers)
            if r.status_code != 200:
                raise RuntimeError(f"export {name}: {r.status_code} {r.text}")
            (out_dir / name).write_bytes(r.content)
        verify = _ok(client.get(f"/api/sessions/{sid}/trail/verify", headers=headers))
        if not verify["ok"]:
            raise RuntimeError(f"trail does not verify: {verify}")
    return sid


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    sid = run(Path(argv[1]))
    print(f"Demo walk-through {sid} complete; exports written to {argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
