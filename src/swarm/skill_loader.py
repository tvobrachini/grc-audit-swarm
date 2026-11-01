"""
Skill Loader
------------
Loads YAML skill definitions from the `skills/` directory and provides
auto-detection logic to match skills against an audit scope narrative.

A "Skill" is the GRC Audit Swarm equivalent of a Claude Skill:
  - A structured instruction set that agents load at runtime
  - Provides domain-specific system prompts, focus areas, and research hints
  - Swappable without touching agent code
"""

import logging
import os
import re
import yaml
import copy
from typing import List, Dict, Optional, Any, Sequence

logger = logging.getLogger(__name__)

SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../skills")

_SKILLS_CACHE: Optional[List[Dict[str, Any]]] = None


def _load_skill_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_available_skills() -> List[Dict[str, Any]]:
    """Return all skills found in the skills/ directory."""
    global _SKILLS_CACHE
    if _SKILLS_CACHE is not None:
        return copy.deepcopy(_SKILLS_CACHE)

    skills = []
    if not os.path.exists(SKILLS_DIR):
        return skills
    for fname in sorted(os.listdir(SKILLS_DIR)):
        if fname.endswith(".yaml") or fname.endswith(".yml"):
            try:
                skill = _load_skill_file(os.path.join(SKILLS_DIR, fname))
            except (yaml.YAMLError, OSError, UnicodeDecodeError):
                logger.exception("Failed to load skill file %s — skipping it", fname)
                continue
            if not isinstance(skill, dict):
                logger.warning(
                    "Skill file %s did not parse to a mapping — skipping it", fname
                )
                continue
            skills.append(skill)

    _SKILLS_CACHE = skills
    return copy.deepcopy(_SKILLS_CACHE)


def _keyword_matches(keyword: str, text: str) -> bool:
    """Return True if ``keyword`` appears in ``text`` bounded by non-word characters.

    Prevents short acronyms such as ``"EU"`` from matching inside words like
    ``"reuse"`` or ``"queue"``.
    """
    kw = keyword.strip()
    if not kw:
        return False
    pattern = rf"(?<!\w){re.escape(kw)}(?!\w)"
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def detect_skills_from_scope(
    scope_text: str,
    frameworks: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Auto-detect which skills apply based on word-boundary keyword matching
    against the scope text (and optional explicit framework list).
    Returns a list of matched skill dicts, ordered by match score descending.
    Falls back to 'itgc_general' if nothing matches.
    """
    combined_scope = scope_text or ""
    if frameworks:
        combined_scope = f"{combined_scope} {' '.join(str(f) for f in frameworks)}"
    skills = list_available_skills()
    scored = []

    for skill in skills:
        keywords = skill.get("scope_keywords", [])
        score = sum(
            1
            for kw in keywords
            if isinstance(kw, str) and _keyword_matches(kw, combined_scope)
        )
        if score > 0:
            scored.append((score, skill))

    scored.sort(key=lambda x: x[0], reverse=True)
    matched = [s for _, s in scored]

    if not matched:
        # fallback to ITGC general controls
        for skill in skills:
            if skill.get("id") == "itgc_general":
                matched = [skill]
                break

    return matched


def get_skill_by_id(skill_id: str) -> Optional[Dict[str, Any]]:
    """Load a specific skill by its id field."""
    for skill in list_available_skills():
        if skill.get("id") == skill_id:
            return skill
    return None


def get_specialist_prompt(matched_skills: List[Dict[str, Any]]) -> str:
    """
    Combine specialist system prompts from all matched skills.
    Used by the Specialist agent.
    """
    if not matched_skills:
        return (
            "You are a general IT Audit Specialist. Apply standard audit methodology."
        )

    parts = []
    for skill in matched_skills:
        name = skill.get("name", "Unknown Skill")
        prompt = skill.get("specialist_system_prompt", "")
        if prompt:
            parts.append(f"=== {name} ===\n{prompt.strip()}")

    return "\n\n".join(parts)


def get_researcher_context_hints(matched_skills: List[Dict[str, Any]]) -> str:
    """
    Combine researcher context hints from all matched skills.
    Used by the Researcher agent to guide web searches.
    """
    if not matched_skills:
        return ""
    hints = [
        skill.get("researcher_context_hint", "").strip()
        for skill in matched_skills
        if skill.get("researcher_context_hint")
    ]
    return "\n\n".join(hints)


def get_focus_domains(matched_skills: List[Dict[str, Any]]) -> List[str]:
    """Return a deduplicated list of priority control domains across all matched skills."""
    seen = set()
    domains = []
    for skill in matched_skills:
        for d in skill.get("focus_domains", []):
            if d not in seen:
                seen.add(d)
                domains.append(d)
    return domains
