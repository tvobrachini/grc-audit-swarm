"""Answer-key model and loader (evals/scenarios/answer_key.v*.yaml)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

import evals  # noqa: F401  (puts src/ on sys.path)
from swarm.schema import (
    DeficiencyClassification,
    DeficiencyScale,
    DesignConclusion,
    FindingResult,
    OperatingConclusion,
    SCALE_CLASSIFICATIONS,
)

DEFAULT_ANSWER_KEY = Path(__file__).parent / "scenarios" / "answer_key.v1.yaml"

UNREVIEWED_BANNER = (
    "ANSWER KEY NOT YET REVIEWED BY THE OWNER: every score below is "
    "provisional. The key is an AI-assisted draft pending sign-off "
    "(reviewed_by is not set)."
)

EvidenceKind = Literal["point_in_time_config", "none"]


class Keyword(BaseModel):
    pattern: str
    weight: float = 1.0

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        re.compile(value)
        return value


class Expectation(BaseModel):
    """Expected fieldwork outcome for one control area in one scenario."""

    in_scope: bool = False
    expected_result: FindingResult
    acceptable_tod: Optional[list[DesignConclusion]] = None
    acceptable_toe: Optional[list[OperatingConclusion]] = None
    expected_exceptions: Optional[int] = None
    deficiency_acceptable: list[DeficiencyClassification] = Field(default_factory=list)
    rationale: str = ""


class Area(BaseModel):
    label: str
    evidence_kind: EvidenceKind
    evidence_sources: list[str] = Field(default_factory=list)
    keywords: list[Keyword]
    default_expectation: Optional[Expectation] = None

    @model_validator(mode="after")
    def _no_tool_means_not_tested(self) -> "Area":
        if self.evidence_kind == "none":
            if (
                self.default_expectation is None
                or self.default_expectation.expected_result != FindingResult.NOT_TESTED
            ):
                raise ValueError(
                    "an area with no evidence tool needs default_expectation "
                    "'Not tested'"
                )
        return self


class Scenario(BaseModel):
    id: str
    title: str
    theme: str
    business_context: str
    frameworks: list[str]
    deficiency_scale: DeficiencyScale
    aws: dict[str, Any] = Field(default_factory=dict)
    areas: dict[str, Expectation]


class AnswerKey(BaseModel):
    version: str
    status: str
    reviewed_by: Optional[str] = None
    reviewed_on: Optional[str] = None
    authored_with: str = ""
    areas: dict[str, Area]
    scenarios: list[Scenario]

    @model_validator(mode="after")
    def _consistent(self) -> "AnswerKey":
        ids = [s.id for s in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate scenario id")
        for s in self.scenarios:
            allowed = SCALE_CLASSIFICATIONS[s.deficiency_scale]
            for name, exp in s.areas.items():
                if name not in self.areas:
                    raise ValueError(f"{s.id}: unknown area {name!r}")
                stray = set(exp.deficiency_acceptable) - allowed
                if stray:
                    raise ValueError(
                        f"{s.id}/{name}: {sorted(stray)} not on the "
                        f"'{s.deficiency_scale}' scale"
                    )
                if exp.deficiency_acceptable and (
                    exp.expected_result != FindingResult.EXCEPTION
                ):
                    raise ValueError(
                        f"{s.id}/{name}: deficiency_acceptable needs an "
                        "expected Exception"
                    )
                if (
                    self.areas[name].evidence_kind == "none"
                    and exp.expected_result != FindingResult.NOT_TESTED
                ):
                    raise ValueError(
                        f"{s.id}/{name}: no evidence tool covers this area, so "
                        "the only supported outcome is 'Not tested'"
                    )
        return self

    @property
    def reviewed(self) -> bool:
        return bool((self.reviewed_by or "").strip())

    def review_status(self) -> str:
        if self.reviewed:
            return (
                f"Answer key v{self.version} reviewed by {self.reviewed_by}"
                + (f" on {self.reviewed_on}" if self.reviewed_on else "")
                + "."
            )
        return UNREVIEWED_BANNER

    def scenario(self, scenario_id: str) -> Scenario:
        for s in self.scenarios:
            if s.id == scenario_id:
                return s
        raise KeyError(f"unknown scenario {scenario_id!r}")

    def expectation(self, scenario: Scenario, area: str) -> Optional[Expectation]:
        """The scenario's expectation for ``area``, else the area's default
        (no-tool areas are 'Not tested' in every scenario), else None."""
        if area in scenario.areas:
            return scenario.areas[area]
        known = self.areas.get(area)
        return known.default_expectation if known else None


def load_answer_key(path: Path | str = DEFAULT_ANSWER_KEY) -> AnswerKey:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return AnswerKey.model_validate(data)


def select_scenarios(key: AnswerKey, selector: str) -> list[Scenario]:
    """``all`` or a comma-separated list of scenario ids (or id prefixes such
    as ``s01``)."""
    if selector.strip().lower() == "all":
        return list(key.scenarios)
    chosen: list[Scenario] = []
    for token in (t.strip() for t in selector.split(",")):
        if not token:
            continue
        matches = [s for s in key.scenarios if s.id == token or s.id.startswith(token)]
        if len(matches) != 1:
            raise KeyError(
                f"scenario selector {token!r} matches {len(matches)} scenarios"
            )
        if matches[0] not in chosen:
            chosen.append(matches[0])
    if not chosen:
        raise KeyError("no scenarios selected")
    return chosen
