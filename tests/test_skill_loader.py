"""
test_skill_loader.py
--------------------
Guardrails for the skill loading system.
Verifies keyword detection, YAML structure, and prompt assembly.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.skill_loader import (
    list_available_skills,
    detect_skills_from_scope,
    get_skill_by_id,
    get_specialist_prompt,
    get_researcher_context_hints,
    get_focus_domains,
    get_mandatory_control_hints,
)


class TestSkillDirectory:
    def test_skills_directory_not_empty(self):
        skills = list_available_skills()
        assert len(skills) >= 1, "At least one skill YAML must exist in skills/"

    def test_all_skills_have_required_fields(self):
        for skill in list_available_skills():
            assert "name" in skill, f"Skill missing 'name': {skill}"
            assert "id" in skill, f"Skill missing 'id': {skill}"
            assert "scope_keywords" in skill, f"Skill missing 'scope_keywords': {skill}"
            assert isinstance(skill["scope_keywords"], list), (
                f"scope_keywords must be a list: {skill['id']}"
            )
            assert len(skill["scope_keywords"]) > 0, (
                f"scope_keywords cannot be empty: {skill['id']}"
            )

    def test_all_skills_have_specialist_prompt(self):
        for skill in list_available_skills():
            prompt = skill.get("specialist_system_prompt", "")
            assert len(prompt) > 50, (
                f"Specialist prompt too short for skill: {skill['id']}"
            )

    def test_all_skills_have_researcher_hint(self):
        for skill in list_available_skills():
            hint = skill.get("researcher_context_hint", "")
            assert len(hint) > 20, (
                f"researcher_context_hint missing or too short: {skill['id']}"
            )

    def test_all_skills_have_focus_domains(self):
        for skill in list_available_skills():
            domains = skill.get("focus_domains", [])
            assert len(domains) >= 1, f"focus_domains missing: {skill['id']}"

    def test_no_duplicate_skill_ids(self):
        skills = list_available_skills()
        ids = [s["id"] for s in skills]
        assert len(ids) == len(set(ids)), f"Duplicate skill IDs found: {ids}"


class TestSkillDetection:
    def test_aws_scope_detects_aws_skill(self):
        skills = detect_skills_from_scope("AWS EKS IAM CloudTrail S3 audit")
        ids = [s["id"] for s in skills]
        assert "aws_cloud_security" in ids

    def test_pci_scope_detects_pci_skill(self):
        skills = detect_skills_from_scope(
            "PCI-DSS cardholder data environment payment processing"
        )
        ids = [s["id"] for s in skills]
        assert "pci_dss" in ids

    def test_hipaa_scope_detects_hipaa_skill(self):
        skills = detect_skills_from_scope(
            "HIPAA ePHI EHR healthcare patient data audit"
        )
        ids = [s["id"] for s in skills]
        assert "hipaa_privacy" in ids

    def test_gdpr_scope_detects_gdpr_skill(self):
        skills = detect_skills_from_scope("GDPR personal data controller processor DPO")
        ids = [s["id"] for s in skills]
        assert "gdpr_privacy" in ids

    def test_no_match_falls_back_to_itgc(self):
        """A scope with no keywords should fall back to ITGC general skill."""
        skills = detect_skills_from_scope("quarterly review of internal processes")
        assert len(skills) >= 1
        assert skills[0]["id"] == "itgc_general"

    def test_multi_keyword_scope_can_match_multiple_skills(self):
        """A scope mentioning both AWS and PCI should match both skills."""
        skills = detect_skills_from_scope(
            "AWS S3 PCI cardholder payment cloud infrastructure"
        )
        ids = [s["id"] for s in skills]
        assert "aws_cloud_security" in ids
        assert "pci_dss" in ids

    def test_get_skill_by_id_returns_correct_skill(self):
        skill = get_skill_by_id("aws_cloud_security")
        assert skill is not None
        assert skill["id"] == "aws_cloud_security"

    def test_get_skill_by_id_returns_none_for_unknown(self):
        skill = get_skill_by_id("nonexistent_skill_xyz")
        assert skill is None


class TestSkillHelpers:
    def test_specialist_prompt_non_empty_for_aws(self):
        skills = detect_skills_from_scope("AWS EKS audit")
        prompt = get_specialist_prompt(skills)
        assert len(prompt) > 100
        assert "AWS" in prompt or "cloud" in prompt.lower()

    def test_researcher_hints_non_empty_for_pci(self):
        skills = detect_skills_from_scope("PCI payment cardholder")
        hints = get_researcher_context_hints(skills)
        assert len(hints) > 20

    def test_focus_domains_non_empty(self):
        skills = detect_skills_from_scope("AWS EKS IAM")
        domains = get_focus_domains(skills)
        assert len(domains) >= 1

    def test_mandatory_control_hints_non_empty(self):
        skills = detect_skills_from_scope("AWS")
        controls = get_mandatory_control_hints(skills)
        assert len(controls) >= 1

    def test_empty_skills_returns_generic_prompt(self):
        prompt = get_specialist_prompt([])
        assert "general" in prompt.lower()

    def test_empty_skills_returns_empty_hints(self):
        hints = get_researcher_context_hints([])
        assert hints == ""
