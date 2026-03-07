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
import os
import yaml
from typing import List, Dict, Optional, Any

SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../skills")


def _load_skill_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_available_skills() -> List[Dict[str, Any]]:
    """Return all skills found in the skills/ directory."""
    skills = []
    if not os.path.exists(SKILLS_DIR):
        return skills
    for fname in sorted(os.listdir(SKILLS_DIR)):
        if fname.endswith(".yaml") or fname.endswith(".yml"):
            skill = _load_skill_file(os.path.join(SKILLS_DIR, fname))
            skills.append(skill)
    return skills


def detect_skills_from_scope(scope_text: str) -> List[Dict[str, Any]]:
    """
    Auto-detect which skills apply based on keyword matching against the scope text.
    Returns a list of matched skill dicts, ordered by match score descending.
    Falls back to 'itgc_general' if nothing matches.
    """
    scope_lower = scope_text.lower()
    skills = list_available_skills()
    scored = []

    for skill in skills:
        keywords = skill.get("scope_keywords", [])
        score = sum(1 for kw in keywords if kw.lower() in scope_lower)
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
        return "You are a general IT Audit Specialist. Apply standard audit methodology."
    
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


def get_mandatory_control_hints(matched_skills: List[Dict[str, Any]]) -> List[str]:
    """Return a flat list of control IDs that should always be included for matched skills."""
    seen = set()
    controls = []
    for skill in matched_skills:
        for c in skill.get("mandatory_control_hints", []):
            cid = c.split()[0]  # strip inline comments
            if cid not in seen:
                seen.add(cid)
                controls.append(cid)
    return controls
