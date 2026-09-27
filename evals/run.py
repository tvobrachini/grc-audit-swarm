"""Evaluate the LLM layer: run scenarios through the real pipeline and score them.

Real run (makes paid model calls; never in CI):

    uv run python -m evals.run --runs 3 --scenarios all --out evals/results/

Offline replay of canned crew outputs (tests the harness, not a model):

    uv run python -m evals.run --replay evals/fixtures/replay --out /tmp/eval-replay

See docs/EVALUATION.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

import evals
from evals import metrics
from evals.answer_key import (
    DEFAULT_ANSWER_KEY,
    AnswerKey,
    Scenario,
    load_answer_key,
    select_scenarios,
)
from evals.pipeline import RealCrews, ReplayCrews, run_scenario, score_record
from evals.qa_seeding import SEEDS, real_reviewer, replay_reviewer, run_qa_seeding
from evals.report import aggregate, notable, render_markdown

logger = logging.getLogger("evals")

EXIT_REFUSED = 2

_TRUTHY = {"1", "true", "yes", "on"}
_CI_VARS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "JENKINS_URL")

NO_PROVIDER_MESSAGE = (
    "No LLM provider is configured, so the evaluation cannot run.\n"
    "This harness makes real (paid) model calls through the app's LLM factory. "
    "Set one of OLLAMA_MODEL, NVIDIA_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY or "
    "GROQ_API_KEY (optionally QA_LLM_MODEL for a separate QA model), in the "
    "environment or in .env, and run again.\n"
    "To exercise the harness offline instead: "
    "uv run python -m evals.run --replay evals/fixtures/replay --out <dir>"
)


def _refuse(message: str) -> int:
    print(f"evals.run: refused. {message}", file=sys.stderr)
    return EXIT_REFUSED


def in_ci() -> bool:
    """True when a CI system is detected (its marker variable is set)."""
    if os.environ.get("JENKINS_URL", "").strip():
        return True
    return any(os.environ.get(v, "").strip().lower() in _TRUTHY for v in _CI_VARS)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-").lower() or "unknown"


def describe_models() -> tuple[str, str, str]:
    """(provider, model, qa model) from the app's LLM factory."""
    from swarm.llm_factory import get_crew_llm, qa_llm_configured

    model = str(get_crew_llm().model)
    provider, _, name = model.partition("/")
    qa = os.environ.get("QA_LLM_MODEL", "").strip() if qa_llm_configured() else ""
    return provider or "unknown", name or model, qa or f"same as crew ({model})"


def check_real_run_allowed(env_file: Optional[str]) -> Optional[str]:
    """None if a real run may start, else the reason it may not."""
    if in_ci():
        return (
            "A CI environment was detected. The LLM evaluation makes paid model "
            "calls and must never run in CI; use --replay for offline checks."
        )
    if env_file and Path(env_file).is_file():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)
    if os.environ.get("DEMO_MODE", "0").strip().lower() in _TRUTHY:
        return (
            "DEMO_MODE is on. Demo crews return fixed artifacts, so there is "
            "nothing to evaluate; unset DEMO_MODE."
        )
    from swarm.llm_factory import LLMConfigurationError, get_crew_llm

    try:
        get_crew_llm()
    except LLMConfigurationError:
        return NO_PROVIDER_MESSAGE
    except Exception as exc:  # e.g. the provider's client package is missing
        return (
            "The configured LLM provider failed to initialise: "
            f"{type(exc).__name__}: {exc}"
        )
    return None


def load_replay(
    fixture_dir: Path,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Scenario id -> canned runs, and the QA-seed decisions."""
    if not fixture_dir.is_dir():
        raise FileNotFoundError(f"replay fixture directory not found: {fixture_dir}")
    runs: dict[str, list[dict[str, Any]]] = {}
    qa: dict[str, Any] = {}
    for path in sorted(fixture_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "qa_seeds.json":
            qa = data.get("decisions", {})
            continue
        runs[data["scenario"]] = list(data["runs"])
    return runs, qa


def _output_paths(
    out: Path, provider: str, model: str, started: datetime
) -> tuple[Path, Path, Path]:
    stem = f"{started:%Y-%m-%d}-{_slug(provider)}-{_slug(model)}"
    if (out / f"{stem}.md").exists() or (out / stem).exists():
        stem = f"{stem}-{started:%H%M%S}"
    return out / f"{stem}.md", out / f"{stem}.json", out / stem


def evaluate(
    key: AnswerKey,
    scenarios: list[Scenario],
    *,
    runs: int,
    out: Path,
    replay: Optional[Path] = None,
    on_qa_reject: str = "override",
    qa_seeding: bool = True,
    prices: tuple[Optional[float], Optional[float]] = (None, None),
    models: Optional[tuple[str, str, str]] = None,
) -> dict[str, Any]:
    """Run, score and write the report. Returns the results dict."""
    started = datetime.now(UTC)
    replay_runs: dict[str, list[dict[str, Any]]] = {}
    qa_decisions: dict[str, Any] = {}
    if replay is not None:
        replay_runs, qa_decisions = load_replay(replay)
        missing = [s.id for s in scenarios if s.id not in replay_runs]
        if missing:
            raise KeyError(f"replay fixture has no runs for: {', '.join(missing)}")
        provider, model, qa_model = (
            "replay",
            _slug(replay.name),
            "replay (canned decisions)",
        )
    else:
        provider, model, qa_model = models or describe_models()

    out.mkdir(parents=True, exist_ok=True)
    md_path, json_path, raw_dir = _output_paths(out, provider, model, started)
    raw_dir.mkdir(parents=True, exist_ok=True)

    scenario_results: list[dict[str, Any]] = []
    all_kickoffs: list[dict[str, Any]] = []
    for scenario in scenarios:
        canned = replay_runs.get(scenario.id)
        n_runs = len(canned) if canned is not None else runs
        run_results: list[dict[str, Any]] = []
        for i in range(1, n_runs + 1):
            logger.info("Scenario %s run %d/%d", scenario.id, i, n_runs)
            crews = ReplayCrews(canned[i - 1]) if canned is not None else RealCrews()
            run_dir = raw_dir / scenario.id / f"run-{i}"
            record = run_scenario(
                scenario, i, crews, run_dir, on_qa_reject=on_qa_reject
            )
            scored = score_record(record, key, scenario)
            record["scoring"] = scored
            raw_file = run_dir / "run.json"
            raw_file.write_text(
                json.dumps(record, indent=2, default=str), encoding="utf-8"
            )
            all_kickoffs.extend(record.get("kickoffs") or [])
            run_results.append(
                {
                    "run": i,
                    "raw_file": str(raw_file.relative_to(out)),
                    "pipeline": record["pipeline"],
                    **scored,
                }
            )
        pooled: Counter = Counter()
        for r in run_results:
            pooled.update(r["counts"])
        scenario_results.append(
            {
                "id": scenario.id,
                "title": scenario.title,
                "expected_outcomes": {
                    a: str(e.expected_result.value) for a, e in scenario.areas.items()
                },
                "runs": run_results,
                "pooled": metrics.rates(pooled),
                "consistency": metrics.consistency(
                    [r["finding_scores"] for r in run_results], scenario
                ),
            }
        )

    qa_result: Optional[dict[str, Any]] = None
    if qa_seeding:
        reviewer = (
            replay_reviewer(qa_decisions) if replay is not None else real_reviewer()
        )
        qa_runs = 1 if replay is not None else runs
        qa_result = run_qa_seeding(reviewer, qa_runs, raw_dir)
        (raw_dir / "qa_seeding.json").write_text(
            json.dumps(qa_result, indent=2, default=str), encoding="utf-8"
        )
        all_kickoffs.extend(
            {"phase": "qa_seed", "token_usage": d.get("token_usage")}
            for d in qa_result["decisions"]
        )

    usage = _usage(all_kickoffs, prices, "replay" if replay is not None else "real")
    results = {
        "meta": {
            "harness_version": evals.HARNESS_VERSION,
            "mode": "replay" if replay is not None else "real",
            "replay_fixture": str(replay) if replay is not None else None,
            "provider": provider,
            "model": model,
            "qa_model": qa_model,
            "runs": runs if replay is None else "per fixture",
            "scenario_count": len(scenarios),
            "scenarios": [s.id for s in scenarios],
            "on_qa_reject": on_qa_reject,
            "started": started.strftime("%Y-%m-%d %H:%M"),
            "raw_dir": str(raw_dir.relative_to(out)),
        },
        "answer_key": {
            "version": key.version,
            "status": key.status,
            "reviewed_by": key.reviewed_by,
            "reviewed_on": key.reviewed_on,
            "review_status": key.review_status(),
        },
        "aggregate": aggregate(scenario_results),
        "scenarios": scenario_results,
        "notable": notable(scenario_results),
        "qa_seeding": (
            {"scores": qa_result["scores"], "seeds": len(SEEDS)} if qa_result else None
        ),
        "usage": usage,
        "outputs": {"markdown": str(md_path), "json": str(json_path)},
    }
    json_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(results), encoding="utf-8")
    return results


def _usage(
    kickoffs: list[dict[str, Any]],
    prices: tuple[Optional[float], Optional[float]],
    mode: str,
) -> dict[str, Any]:
    total: dict[str, int] = {}
    for k in kickoffs:
        for name, value in (k.get("token_usage") or {}).items():
            if isinstance(value, int):
                total[name] = total.get(name, 0) + value
    cost = None
    price_in, price_out = prices
    if total and price_in is not None and price_out is not None:
        cost = (
            total.get("prompt_tokens", 0) * price_in
            + total.get("completion_tokens", 0) * price_out
        ) / 1_000_000
    return {"mode": mode, "total": total or None, "estimated_cost_usd": cost}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m evals.run",
        description="Evaluate the audit conclusions of the LLM layer against a versioned answer key.",
    )
    p.add_argument(
        "--runs", type=int, default=3, help="runs per scenario (real mode; default 3)"
    )
    p.add_argument(
        "--scenarios",
        default="all",
        help="'all' or comma-separated scenario ids / prefixes (e.g. s01,s05)",
    )
    p.add_argument(
        "--out",
        default="evals/results/",
        help="output directory (default evals/results/)",
    )
    p.add_argument(
        "--replay",
        default=None,
        help="replay canned crew outputs from this fixture directory (no model calls)",
    )
    p.add_argument(
        "--answer-key", default=str(DEFAULT_ANSWER_KEY), help="answer key YAML"
    )
    p.add_argument(
        "--on-qa-reject",
        choices=["override", "stop"],
        default="override",
        help="after a QA rejection that survives the automatic retry: override it with the synthetic reviewer (recorded) so later phases are measured, or stop the run",
    )
    p.add_argument(
        "--skip-qa-seeding",
        action="store_true",
        help="do not run the QA catch-rate seeds",
    )
    p.add_argument(
        "--env-file",
        default=".env",
        help="dotenv file with provider keys (real mode; default .env)",
    )
    p.add_argument(
        "--usd-per-mtok-input",
        type=float,
        default=None,
        help="price per million prompt tokens, for a cost estimate",
    )
    p.add_argument(
        "--usd-per-mtok-output",
        type=float,
        default=None,
        help="price per million completion tokens, for a cost estimate",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.runs < 1:
        return _refuse("--runs must be at least 1.")
    key = load_answer_key(args.answer_key)
    replay = Path(args.replay) if args.replay else None

    if replay is None:
        reason = check_real_run_allowed(args.env_file)
        if reason:
            return _refuse(reason)
    try:
        if replay is not None and args.scenarios.strip().lower() == "all":
            available, _ = load_replay(replay)
            scenarios = [s for s in key.scenarios if s.id in available]
        else:
            scenarios = select_scenarios(key, args.scenarios)
    except (KeyError, FileNotFoundError) as exc:
        return _refuse(str(exc))

    print(key.review_status())
    if replay is None:
        seeds = 0 if args.skip_qa_seeding else len(SEEDS) * args.runs
        print(
            f"COST WARNING: real model calls. {len(scenarios)} scenario(s) x "
            f"{args.runs} run(s) x 3 phase crews (up to 2 attempts each), plus "
            f"{seeds} QA-seed reviews. Each crew run makes several LLM calls; "
            "watch your provider's usage dashboard."
        )
    try:
        results = evaluate(
            key,
            scenarios,
            runs=args.runs,
            out=Path(args.out),
            replay=replay,
            on_qa_reject=args.on_qa_reject,
            qa_seeding=not args.skip_qa_seeding,
            prices=(args.usd_per_mtok_input, args.usd_per_mtok_output),
        )
    except (KeyError, FileNotFoundError) as exc:
        return _refuse(str(exc))
    agg = results["aggregate"]["metrics"]
    print(
        f"False-pass rate: {agg['false_pass_rate']['num']}/{agg['false_pass_rate']['den']}. "
        f"Report: {results['outputs']['markdown']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
