"""LLM-layer evaluation harness for GRC Audit Swarm.

The evidence layer (the deterministic AWS reads) is evaluated in
``tests/eval``. This package evaluates the layer above it: whether the agents
draw the right audit conclusions from that evidence. It runs the real
pipeline (Planning -> Fieldwork -> Reporting, with the real gates) against
AWS accounts simulated in moto, maps the findings to the control areas of a
versioned answer key and scores them.

Two modes:

* **real** (``python -m evals.run``): the configured LLM provider drives the
  real crews. Never runs in CI; refuses to start without a provider.
* **replay** (``python -m evals.run --replay <dir>``): canned crew outputs
  stand in for the model, so the seeding, mapping, scoring and report code is
  tested offline (see ``tests/test_llm_eval*.py``).

See docs/EVALUATION.md for the metric definitions and limitations.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The application lives in src/ and is imported as ``swarm`` (the same layout
# the tests and run_monitor.py use).
_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

HARNESS_VERSION = "1.0"
