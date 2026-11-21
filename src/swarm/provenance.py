"""Generation provenance for audit phase runs (see DECISIONS.md, ADR-010).

Records deterministic prompt fingerprints, runtime/app versions, model
provider/family metadata (never secrets), and approval-trail linkage fields.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from swarm.demo import demo_mode_requested
from swarm.evidence import app_version as evidence_app_version
from swarm.state.schema import GenerationRun

__all__ = [
    "latest_run_for_phase",
    "prompt_fingerprint",
    "run_provenance_extra",
    "runtime_version",
    "start_generation_run",
]

_CONFIG_DIR = Path(__file__).parent / "config"
# Per phase: the agent/task YAML files whose content is fingerprinted.
_PHASE_CONFIG_FILES: dict[int, tuple[str, str]] = {
    1: ("planning_agents.yaml", "planning_tasks.yaml"),
    2: ("fieldwork_agents.yaml", "fieldwork_tasks.yaml"),
    3: ("reporting_agents.yaml", "reporting_tasks.yaml"),
}

# Crew / QA temperatures, matching the crews' Agent construction
# (swarm.crews.*_crew): base agents at 0.1, QA reviewers at 0.0. Recorded
# here so a generation run states the temperature it used without having to
# re-derive it from the crew classes.
_CREW_TEMPERATURE = 0.1
_QA_TEMPERATURE = 0.0


def _now() -> str:
    return datetime.now(UTC).isoformat()


def prompt_fingerprint(phase: int, skill_context: list[Any]) -> str:
    """Deterministic SHA-256 fingerprint of the prompts a phase run used.

    Covers the phase's agent and task YAML config files (raw bytes, as
    loaded by the crew) and the specialist prompt text injected into an
    agent's backstory for any active domain skill (``skill_loader
    .get_specialist_prompt``), in that order. The same config files plus the
    same active skills always hash to the same value; a change to either
    changes it. Each part is length-prefixed before hashing so that
    concatenating differently split content can never collide.

    Only the *prompt* fingerprint changes on a skill toggle or a config edit;
    a difference does not by itself mean the model call itself was
    different — see ADR-010 for what this can and cannot prove.
    """
    parts: list[bytes] = [
        (_CONFIG_DIR / name).read_bytes() for name in _PHASE_CONFIG_FILES[phase]
    ]
    if skill_context:
        from swarm.skill_loader import get_specialist_prompt

        parts.append(get_specialist_prompt(skill_context).encode("utf-8"))
    digest = hashlib.sha256()
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def runtime_version() -> str:
    """Version label of the agent runtime recorded with each generation run.

    The runtime is the in-repo zero-framework layer (ADR-013), so it is
    labelled with the app version. The run field is still named
    ``crewai_version`` so stored runs and API clients keep working.
    """
    return f"zero-framework-{evidence_app_version()}"


# Fixed at import time: it cannot change while the process is running.
_RUNTIME_VERSION = runtime_version()
_APP_VERSION = evidence_app_version()


def start_generation_run(phase: int, skill_context: list[Any]) -> GenerationRun:
    """Build a fresh ``GenerationRun`` record for one phase run.

    Uses ``demo_mode_requested`` (never raises) rather than
    ``demo_mode_enabled`` (raises when DEMO_MODE is set in a blocked
    environment): that guard's raise must surface from inside the crew
    build/kickoff try/except in ``AuditFlow._run_crew_with_qa``, not here,
    before a run record even exists.
    """
    demo = demo_mode_requested()
    if demo:
        crew_info = {"provider": "demo", "model": "fixed-content"}
        qa_info = {"provider": "demo", "model": "fixed-content"}
        drafter_family = "demo"
        qa_family = "demo"
        cross_family = False
        cascade_tier = "tier_0_deterministic"
    else:
        from swarm.llm_factory import describe_crew_llm, describe_qa_llm
        from swarm.model_routing import (
            ModelFamily,
            assert_disjoint_family,
            detect_model_family,
        )

        try:
            crew_info = describe_crew_llm()
        except Exception:
            # No provider configured: the crew build that follows raises
            # the same error and is handled there (_fail_phase). This
            # run record still gets stamped so the failed attempt has one.
            crew_info = {"provider": "unknown", "model": "unknown"}
        try:
            qa_info = describe_qa_llm()
        except Exception:
            qa_info = {"provider": "unknown", "model": "unknown"}

        dfam = detect_model_family(
            f"{crew_info['provider']}/{crew_info['model']}"
            if crew_info["provider"] not in ("unknown", "custom")
            else crew_info["model"]
        )
        qfam = detect_model_family(
            f"{qa_info['provider']}/{qa_info['model']}"
            if qa_info["provider"] not in ("unknown", "custom")
            else qa_info["model"]
        )
        drafter_family = dfam.value
        qa_family = qfam.value
        cross_family = (
            dfam != ModelFamily.OTHER and qfam != ModelFamily.OTHER and dfam != qfam
        )
        cascade_tier = "tier_2_cross_family" if cross_family else "tier_1_system1"

        if os.environ.get("AUDIT_ENFORCE_CROSS_FAMILY", "").strip() == "1":
            assert_disjoint_family(
                f"{crew_info['provider']}/{crew_info['model']}",
                f"{qa_info['provider']}/{qa_info['model']}",
            )
    return GenerationRun(
        run_id=str(uuid.uuid4()),
        phase=phase,
        provider=crew_info["provider"],
        model=crew_info["model"],
        qa_provider=qa_info["provider"],
        qa_model=qa_info["model"],
        temperature=0.0 if demo else _CREW_TEMPERATURE,
        qa_temperature=0.0 if demo else _QA_TEMPERATURE,
        crewai_version=_RUNTIME_VERSION,
        app_version=_APP_VERSION,
        prompt_fingerprint=prompt_fingerprint(phase, skill_context),
        started_at=_now(),
        demo_mode=demo,
        drafter_family=drafter_family,
        qa_family=qa_family,
        cross_family_verified=cross_family,
        cascade_tier=cascade_tier,
    )


def latest_run_for_phase(
    generation_runs: Sequence[GenerationRun], phase: int
) -> GenerationRun | None:
    for run in reversed(generation_runs):
        if run.phase == phase:
            return run
    return None


def run_provenance_extra(
    generation_runs: Sequence[GenerationRun], phase: int
) -> dict[str, str]:
    """Trail-entry fields tying an action to the generation run it acted
    on: the run id, its prompt fingerprint, and its provider/model —
    enough to say exactly what was reviewed (see ADR-010). Empty when no
    run has been recorded yet for this phase (e.g. a snapshot from
    before this field existed)."""
    run = latest_run_for_phase(generation_runs, phase)
    if run is None:
        return {}
    return {
        "generation_run_id": run.run_id,
        "generation_prompt_fingerprint": run.prompt_fingerprint,
        "generation_model": f"{run.provider}/{run.model}",
    }
