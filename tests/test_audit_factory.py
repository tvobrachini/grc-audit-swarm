"""
Comprehensive tests for the Zero-Framework Audit Factory AI architecture:
  1. Word-boundary skill detection & new ISO 27001:2022 / SOC 2 TSC skills
  2. Gate 2 -> Phase 3 effective-view handoff (Rule D5)
  3. Deterministic Audit Methodology Linters (Rules D1-D14 & 5 Distortion Traps)
  4. System-1 Decision Engine (Jev / Kev / Noul / Choice / Score) & FrugalGPT Cascade
  5. Cross-Family Model Routing (Panickssery et al.) & PoLL Micro-Jury (Verga et al.)
  6. Zero-Framework LLM Gateway, Powerpipe 4-State Probes & SWE-Agent Line Citations
  7. Root Account / Access-Key Age AWS Collectors & Seeded AICPA Sampling
"""

from __future__ import annotations

import datetime
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flow_builders import make_papers, make_racm, make_report  # type: ignore[import-not-found]
from swarm.audit_flow import AuditFlow
from swarm.decision_engine import (
    CascadeAction,
    HeuristicLogprobDecisionEngine,
    JevDecisionEngine,
    compute_control_complexity,
    compute_delta_ibc,
    evaluate_finding_cascade,
)
from swarm.evidence import EvidenceAssuranceProtocol
from swarm.linters import (
    lint_distortion_traps,
    lint_racm,
    lint_report,
    lint_working_papers,
)
from swarm.llm_gateway import (
    ZeroFrameworkLLMGateway,
    extract_quote_by_lines,
    format_line_indexed_evidence,
    merge_surgical_finding_retries,
    resolve_unmapped_controls_deterministically,
)
from swarm.model_routing import (
    EffortLevel,
    ModelFamily,
    ModelSpec,
    PoLLJuryVote,
    SameFamilyBiasError,
    aggregate_poll_jury,
    assert_disjoint_family,
    build_blind_falsification_prompt,
    build_poll_jury_specs,
    detect_model_family,
    select_cross_family_critic,
)
from swarm.sampling import (
    deterministic_sample_selection,
    recommended_sample_size,
)
from swarm.schema import (
    AuditFindingSchema,
    Control,
    ControlFrequency,
    ControlNature,
    ControlTestStep,
    ControlTesting,
    DeficiencyClassification,
    DeficiencyEvaluationSchema,
    DeficiencyScale,
    FindingResult,
    QA_PushbackSchema,
    RiskRating,
    SamplingMethod,
)
from swarm.skill_loader import detect_skills_from_scope
from swarm.tools.aws_checks import (
    collect_access_key_age,
    collect_root_account_summary,
)


# ── 1. Skill Loader Word-Boundary & New Framework Skills ─────────────────────


class TestSkillWordBoundaryAndFrameworks:
    def test_eu_does_not_match_password_reuse(self):
        skills = detect_skills_from_scope(
            "AWS IAM password reuse and rotation policy audit"
        )
        ids = [s["id"] for s in skills]
        assert "aws_cloud_security" in ids
        assert "gdpr_privacy" not in ids

    def test_eu_matches_standalone_eu_word(self):
        skills = detect_skills_from_scope("Data privacy audit for EU residents")
        ids = [s["id"] for s in skills]
        assert "gdpr_privacy" in ids

    def test_detects_iso_27001_and_soc2_skills(self):
        iso_skills = detect_skills_from_scope("ISMS review", frameworks=["ISO 27001"])
        assert any(s["id"] == "iso_27001" for s in iso_skills)

        soc2_skills = detect_skills_from_scope(
            "Service organization controls", frameworks=["SOC 2"]
        )
        assert any(s["id"] == "soc2_tsc" for s in soc2_skills)

    def test_audit_flow_includes_frameworks_in_skill_detection(self):
        flow = AuditFlow()
        flow.state.theme = "Annual security review"
        flow.state.business_context = "B2B SaaS platform"
        flow.state.frameworks = ["SOC 2", "ISO 27001"]
        flow._detect_skills()
        assert "soc2_tsc" in flow.state.active_skill_ids
        assert "iso_27001" in flow.state.active_skill_ids


# ── 2. Gate 2 -> Phase 3 Effective View Handoff (Rule D5) ────────────────────


class TestEffectiveViewPhase3Handoff:
    def test_challenged_finding_propagates_to_reporting_inputs(self):
        flow = AuditFlow(initial_status="WAITING_HUMAN_GATE_2")
        flow.state.theme = "AWS S3"
        flow.state.business_context = "Fintech"
        flow.state.frameworks = ["CIS AWS"]
        flow.state.racm_plan = make_racm()
        flow.state.working_papers = make_papers()

        flow.record_decision(
            decision_type="challenge",
            subject_id="CTRL-01",
            decided_by="bob",
            values={
                "tod_conclusion": "Not tested",
                "toe_conclusion": "Not tested",
            },
            rationale="Evidence predates the audit period.",
        )

        # AI draft remains untouched (ADR-011 immutability)
        assert flow.state.working_papers.findings[0].tod_conclusion == "Effective"

        # Effective working papers and Phase 3 reporting inputs reflect the challenge
        eff_papers = flow.effective_working_papers()
        assert eff_papers is not None
        assert eff_papers.findings[0].tod_conclusion == "Not tested"
        assert eff_papers.findings[0].toe_conclusion == "Not tested"
        assert eff_papers.findings[0].result == "Not tested"

        rep_inputs = flow._reporting_inputs()
        assert (
            "Not tested" in rep_inputs[" findings_index" if False else "findings_index"]
        )
        assert '"tod_conclusion":"Not tested"' in rep_inputs["working_papers_string"]


# ── 3. Deterministic Audit Linters (D1-D14 & 5 Distortion Traps) ─────────────


class TestDeterministicAuditLinters:
    def test_clean_racm_passes(self):
        res = lint_racm(make_racm())
        assert res.passed, res.to_feedback()

    def test_racm_catches_auditing_standard_in_mapping_d13(self):
        racm = make_racm()
        racm.risks[0].regulatory_mapping = ["PCAOB AS 2201", "CIS AWS 2.1.5"]
        res = lint_racm(racm)
        assert not res.passed
        assert any(v.rule_id == "D13" for v in res.errors)

    def test_racm_catches_invalid_sample_size_and_test_of_one_d7(self):
        racm = make_racm()
        ctrl = racm.risks[0].controls[0]
        ctrl.nature = ControlNature.MANUAL
        ctrl.frequency = ControlFrequency.DAILY
        ctrl.testing_procedures.sample_size = 5  # Daily manual requires >= 20
        ctrl.testing_procedures.sampling_method = SamplingMethod.TEST_OF_ONE
        res = lint_racm(racm)
        assert not res.passed
        d7_msgs = [v.message for v in res.errors if v.rule_id == "D7"]
        assert len(d7_msgs) == 2

    def test_racm_catches_missing_ipe_on_itdm_control_d10(self):
        racm = make_racm()
        ctrl = racm.risks[0].controls[0]
        ctrl.nature = ControlNature.IT_DEPENDENT_MANUAL
        ctrl.ipe = []
        res = lint_racm(racm)
        assert not res.passed
        assert any(v.rule_id == "D10" for v in res.errors)

    def test_distortion_traps_1_through_4(self):
        racm = make_racm()
        ctrl = racm.risks[0].controls[0]
        ctrl.testing_procedures.test_of_design[
            0
        ].expected_result = "Minimum length of at least 14 characters"

        # Trap 1: Polarity inversion (quote says MFA_Enabled: No, finding says Effective)
        f_trap1 = AuditFindingSchema(
            control_id="CTRL-01",
            vault_id_reference="v1",
            exact_quote_from_evidence='"MFA_Enabled": "No"',
            tod_conclusion="Effective",
            toe_conclusion="Not tested",
            test_conclusion="MFA is enabled for all users.",
        )
        v1 = lint_distortion_traps(f_trap1, control=ctrl)
        assert any(v.rule_id == "TRAP-1" for v in v1)

        # Trap 3: Design-vs-Operating confusion (ToE Effective with no toe_basis on snapshot)
        f_trap3 = AuditFindingSchema(
            control_id="CTRL-01",
            vault_id_reference="v1",
            exact_quote_from_evidence="BlockPublicAcls: true",
            tod_conclusion="Effective",
            toe_conclusion="Effective",
            toe_basis=None,
            items_tested=1,
            test_conclusion="Operating effectively.",
        )
        v3 = lint_distortion_traps(f_trap3, control=ctrl)
        assert any(v.rule_id == "TRAP-3" for v in v3)

        # Trap 4: Quantitative threshold mismatch (MinimumPasswordLength=8 < 14)
        f_trap4 = AuditFindingSchema(
            control_id="CTRL-01",
            vault_id_reference="v1",
            exact_quote_from_evidence='"MinimumPasswordLength": 8',
            tod_conclusion="Effective",
            toe_conclusion="Not tested",
            test_conclusion="Password length meets standard.",
        )
        v4 = lint_distortion_traps(f_trap4, control=ctrl)
        assert any(v.rule_id == "TRAP-4" for v in v4)

    def test_cross_control_itgc_reliance_chain_d8(self):
        step = ControlTestStep(step_description="Check", expected_result="OK")
        racm = make_racm()
        racm.risks[0].controls.append(
            Control(
                control_id="CTRL-CHG-01",
                description="Change management ITGC over infrastructure changes",
                control_owner="DevOps Lead",
                frequency=ControlFrequency.CONTINUOUS,
                nature=ControlNature.IT_DEPENDENT_MANUAL,
                control_type="Preventive",
                key_control=True,
                assertions=["Program changes"],
                ipe=["Change log"],
                testing_procedures=ControlTesting(
                    test_of_design=[step],
                    test_of_effectiveness=[step],
                    substantive_testing=[step],
                    sampling_method=SamplingMethod.RANDOM,
                    sample_size=25,
                    period_of_reliance="FY2026",
                ),
            )
        )
        papers = make_papers()
        # CTRL-01 relies on change-management ITGCs, but CTRL-CHG-01 is Ineffective!
        papers.findings.append(
            AuditFindingSchema(
                control_id="CTRL-CHG-01",
                vault_id_reference="v2",
                exact_quote_from_evidence="Unapproved changes: 3",
                tod_conclusion="Effective",
                toe_conclusion="Ineffective",
                items_tested=25,
                exceptions_noted=3,
                test_conclusion="Change management failed.",
            )
        )
        res = lint_working_papers(papers, racm=racm, verify_vault_quotes=False)
        assert any(v.rule_id == "D8" and v.subject_id == "CTRL-01" for v in res.errors)

    def test_compensating_control_validation_d9_and_anti_slop_d14(self):
        papers = make_papers()
        # Make CTRL-01 an exception
        papers.findings[0] = AuditFindingSchema(
            control_id="CTRL-01",
            vault_id_reference="v1",
            exact_quote_from_evidence="BlockPublicAcls: false",
            tod_conclusion="Ineffective",
            toe_conclusion="Not tested",
            test_conclusion="Failed.",
        )
        report = make_report()
        report.executive_summary = "We delve into the tapestry of controls and confirm the system is 100% compliant."
        report.deficiency_evaluations = [
            DeficiencyEvaluationSchema(
                deficiency_id="DEF-01",
                title="Public S3",
                related_findings=["CTRL-01"],
                related_risks=["RISK-01"],
                compensating_controls="Mitigated by CTRL-99 which was not tested",
                likelihood=RiskRating.LOW,
                magnitude=RiskRating.LOW,
                classification=DeficiencyClassification.NOT_A_DEFICIENCY,
                rationale="Compensated by CTRL-99.",
            )
        ]
        res = lint_report(
            report,
            papers=papers,
            racm=make_racm(),
            expected_scale=DeficiencyScale.RISK_RATING,
        )
        assert not res.passed
        rule_ids = {v.rule_id for v in res.errors}
        assert "D9" in rule_ids
        assert "D14" in rule_ids


# ── 4. System-1 Decision Engine (Jev / Kev / FrugalGPT Cascade) ──────────────


class TestSystem1DecisionEngineAndCascade:
    def test_noul_choice_and_score_primitives(self):
        eng = HeuristicLogprobDecisionEngine()
        noul_res = eng.noul(
            "Evidence shows BlockPublicAcls: true and all checks are effective and verified."
        )
        assert noul_res.value is True
        assert noul_res.probability > 0.5

        choice_res = eng.choice(
            "Select the control nature for an automated IAM password policy configuration check",
            ["Automated", "Manual", "IT-dependent manual"],
        )
        assert choice_res.value == "Automated"
        assert abs(sum(choice_res.probabilities.values()) - 1.0) < 1e-3

        score_res = eng.score("Verified effective control with zero exceptions.")
        assert 0.5 <= score_res.score <= 1.0

    def test_jev_engine_graceful_fallback_without_sdk(self):
        jev = JevDecisionEngine(api_key="test-key")
        res = jev.noul("Control is verified and effective.")
        assert res.backend.startswith("typesafe-jev")
        assert res.value is True

    def test_pre_router_complexity_and_post_generation_cascade(self):
        racm = make_racm()
        ctrl = racm.risks[0].controls[0]
        c_low = compute_control_complexity(
            ctrl, has_exception=False, regulatory_mappings=["CIS 2.1.5"]
        )
        ctrl.nature = ControlNature.IT_DEPENDENT_MANUAL
        c_high = compute_control_complexity(
            ctrl,
            has_exception=True,
            regulatory_mappings=["SOX", "COSO", "PCAOB"],
        )
        assert c_high > c_low

        # Clean verified finding -> FAST_ACCEPT
        clean_finding = make_papers().findings[0]
        decision_ok = evaluate_finding_cascade(
            clean_finding,
            control=make_racm().risks[0].controls[0],
            quote_verified_in_vault=True,
            p_seq=0.95,
            is_sox_or_key=False,
        )
        assert decision_ok.action == CascadeAction.FAST_ACCEPT
        assert decision_ok.s_hat >= 0.80

        # Unverified quote (I_span = 0) -> s_hat = 0.0 -> ABSTAIN_TO_HUMAN
        decision_bad_span = evaluate_finding_cascade(
            clean_finding,
            control=ctrl,
            quote_verified_in_vault=False,
            p_seq=0.95,
        )
        assert decision_bad_span.i_span == 0
        assert decision_bad_span.s_hat == 0.0
        assert decision_bad_span.action == CascadeAction.ABSTAIN_TO_HUMAN

        ibc = compute_delta_ibc(0.70, 0.95, 0.0005, 0.0055)
        assert ibc.delta_ibc == 50.0


# ── 5. Cross-Family Model Routing & PoLL Micro-Jury ─────────────────────────


class TestCrossFamilyModelRoutingAndPoLL:
    def test_disjoint_family_invariant_blocks_same_family(self):
        assert detect_model_family("gemini/gemini-2.5-flash") == ModelFamily.GOOGLE
        assert (
            detect_model_family("anthropic/claude-3-7-sonnet") == ModelFamily.ANTHROPIC
        )
        assert detect_model_family("openai/gpt-4o") == ModelFamily.OPENAI

        with pytest.raises(SameFamilyBiasError):
            assert_disjoint_family("gemini/gemini-2.5-flash", "gemini/gemini-2.5-pro")

        critic = select_cross_family_critic(
            "gemini/gemini-2.5-flash", effort=EffortLevel.HIGH
        )
        assert critic.family != ModelFamily.GOOGLE
        assert critic.thinking_budget_tokens == 8192

    def test_poll_jury_diversity_and_voting(self):
        specs = build_poll_jury_specs("gemini/gemini-2.5-flash", panel_size=3)
        assert len(specs) == 3
        assert ModelFamily.GOOGLE not in {s.family for s in specs}
        assert len({s.family for s in specs}) == 3

        # 2 of 3 approve -> passes standard supermajority, fails unanimity
        votes = [
            PoLLJuryVote("openai/gpt-4o-mini", ModelFamily.OPENAI, True),
            PoLLJuryVote("anthropic/claude-3-5-haiku", ModelFamily.ANTHROPIC, True),
            PoLLJuryVote(
                "groq/llama-3.3-70b",
                ModelFamily.META,
                False,
                rejection_reason="CTRL-02 missing IPE procedure.",
            ),
        ]
        res_super = aggregate_poll_jury(votes, require_unanimity=False)
        assert res_super.approved is True

        res_unanim = aggregate_poll_jury(votes, require_unanimity=True)
        assert res_unanim.approved is False
        assert "CTRL-02 missing IPE procedure" in (res_unanim.rejection_reason or "")

        # Duplicate family in jury raises SameFamilyBiasError
        with pytest.raises(SameFamilyBiasError):
            aggregate_poll_jury(
                [
                    PoLLJuryVote("openai/gpt-4o-mini", ModelFamily.OPENAI, True),
                    PoLLJuryVote("openai/gpt-4o", ModelFamily.OPENAI, True),
                ]
            )

    def test_blind_falsification_prompt_has_no_peer_anchor(self):
        prompt = build_blind_falsification_prompt(
            '{"findings": []}', "Vault evidence...", phase=2
        )
        assert "FALSIFY" in prompt
        assert "QA_PushbackSchema" in prompt


# ── 6. Zero-Framework Gateway, Powerpipe Probes & Line-Indexed Quotes ────────


class TestZeroFrameworkGatewayAndProbes:
    def test_line_indexed_quote_extraction_guarantees_exact_match(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path))
        payload = '{\n  "MinimumPasswordLength": 14,\n  "RequireSymbols": true\n}'
        rec = EvidenceAssuranceProtocol.register_evidence(payload, "iam:GetPolicy")
        vid = rec["vault_id"]

        indexed_view = format_line_indexed_evidence(vid, payload)
        assert 'L002:   "MinimumPasswordLength": 14,' in indexed_view

        extracted = extract_quote_by_lines(payload, 2, 3)
        assert EvidenceAssuranceProtocol.verify_exact_quote(vid, extracted) is True

    def test_powerpipe_probes_and_deterministic_not_tested_resolution(self):
        racm = make_racm()
        resolved = resolve_unmapped_controls_deterministically(
            racm, mapped_control_ids=set()
        )
        assert "CTRL-01" in resolved
        assert resolved["CTRL-01"].result == FindingResult.NOT_TESTED

    def test_gateway_records_cryptographic_receipt(self):
        gateway = ZeroFrameworkLLMGateway(
            completion_fn=lambda **kw: QA_PushbackSchema(approved=True)
        )
        spec = ModelSpec("openai/gpt-4o-mini", ModelFamily.OPENAI, EffortLevel.LOW)
        out = gateway.invoke_structured(
            phase=2,
            role="critic",
            spec=spec,
            system_prompt="System",
            user_prompt="User",
            response_model=QA_PushbackSchema,
        )
        assert out.approved is True
        assert len(gateway.receipts) == 1
        r = gateway.receipts[0]
        assert len(r.prompt_sha256) == 64
        assert len(r.schema_sha256) == 64
        assert len(r.response_sha256) == 64

    def test_surgical_single_control_retry_merge(self):
        papers = make_papers()
        repaired = AuditFindingSchema(
            control_id="CTRL-01",
            vault_id_reference="v-new",
            exact_quote_from_evidence="BlockPublicAcls: true",
            tod_conclusion="Effective",
            toe_conclusion="Not tested",
            test_conclusion="Repaired single control.",
        )
        merged = merge_surgical_finding_retries(papers, [repaired])
        assert merged.findings[0].vault_id_reference == "v-new"
        assert merged.findings[0].toe_conclusion == "Not tested"


# ── 7. New AWS Collectors & Deterministic Seeded Sampling ────────────────────


class TestNewAwsChecksAndSeededSampling:
    def test_collect_root_account_summary(self):
        iam = MagicMock()
        iam.get_account_summary.return_value = {
            "SummaryMap": {
                "AccountMFAEnabled": 0,
                "AccountAccessKeysPresent": 1,
            }
        }
        res = collect_root_account_summary(iam)
        assert res["Verdict"] == "FAIL"
        assert res["AccountMFAEnabled"] == 0
        assert res["AccountAccessKeysPresent"] == 1
        assert len(res["Reasons"]) == 2

    def test_collect_access_key_age(self):
        iam = MagicMock()
        iam.get_paginator.return_value.paginate.return_value = [
            {"Users": [{"UserName": "alice"}]}
        ]
        now = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
        iam.list_access_keys.return_value = {
            "AccessKeyMetadata": [
                {
                    "AccessKeyId": "AKIAIOSFODNN7EXAMPLE",  # pragma: allowlist secret
                    "Status": "Active",
                    "CreateDate": datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
                }
            ]
        }
        res = collect_access_key_age(iam, max_age_days=90, reference_time=now)
        assert res["Verdict"] == "FAIL"
        assert res["Summary"]["exceeding_max_age"] == 1
        assert res["AccessKeys"][0]["AgeDays"] == 151
        assert res["AccessKeys"][0]["AccessKeyIdSuffix"] == "MPLE"

    def test_recommended_sample_size_and_deterministic_selection(self):
        assert (
            recommended_sample_size(
                ControlFrequency.DAILY,
                ControlNature.MANUAL,
                risk_rating=RiskRating.LOW,
            )
            == 20
        )
        assert (
            recommended_sample_size(
                ControlFrequency.DAILY,
                ControlNature.MANUAL,
                risk_rating=RiskRating.HIGH,
            )
            == 40
        )
        assert (
            recommended_sample_size(
                ControlFrequency.CONTINUOUS, ControlNature.AUTOMATED
            )
            == 1
        )

        pop = [f"ITEM-{i:03d}" for i in range(100)]
        r1 = deterministic_sample_selection(
            pop, 5, session_id="sess-1", control_id="CTRL-01"
        )
        r2 = deterministic_sample_selection(
            pop, 5, session_id="sess-1", control_id="CTRL-01"
        )
        r3 = deterministic_sample_selection(
            pop, 5, session_id="sess-1", control_id="CTRL-02"
        )
        assert r1 == r2
        assert len(r1.selected_items) == 5
        assert r1.selected_items != r3.selected_items


# ── 8. Full End-to-End Audit Factory Pipeline (Phase 1 -> Gate 3 -> Exports) ─


class TestEndToEndAuditFactoryPipeline:
    def test_end_to_end_lifecycle_with_gates_linters_cascade_and_oscal_export(
        self, tmp_path, monkeypatch
    ):
        import json
        from api.exports import (
            ExportContext,
            oscal_json,
            racm_xlsx,
            report_markdown,
            working_papers_xlsx,
        )
        from review_helpers import record_flow_decisions  # type: ignore[import-not-found]
        from swarm.trail import verify_trail

        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path / "vault"))
        monkeypatch.setenv("DEMO_MODE", "1")
        monkeypatch.setenv("DEMO_STEP_DELAY", "0")
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("DEMO_QA_REJECT_PHASE", raising=False)

        flow = AuditFlow()
        flow.session_id = "e2e-session-0001"
        flow.state.theme = "AWS Cloud Security & SOC 2 Review"
        flow.state.business_context = (
            "B2B SaaS platform storing customer data in AWS S3"
        )
        flow.state.frameworks = ["CIS AWS", "SOC 2", "ISO 27001"]
        flow.record_preparer("Pat", require_review_decisions=True)

        # Phase 1: Planning
        flow.begin_phase_1()
        flow.generate_planning()
        assert flow.state.status == "WAITING_HUMAN_GATE_1"
        assert flow.state.racm_plan is not None
        assert "aws_cloud_security" in flow.state.active_skill_ids
        assert "soc2_tsc" in flow.state.active_skill_ids
        assert "iso_27001" in flow.state.active_skill_ids

        # Lint Phase 1 RACM deterministically
        racm_lint = lint_racm(flow.state.racm_plan)
        assert racm_lint.passed, racm_lint.to_feedback()

        # Gate 1 Approval (SoD enforced: alice != Pat)
        flow.begin_phase_2("alice")
        assert flow.state.status == "RUNNING_PHASE_2"

        # Phase 2: Fieldwork
        flow.generate_fieldwork()
        assert flow.state.status == "WAITING_HUMAN_GATE_2"
        assert flow.state.working_papers is not None

        # Lint Phase 2 Working Papers + run System-1 FrugalGPT Cascade on each finding
        wp_lint = lint_working_papers(
            flow.state.working_papers,
            racm=flow.state.racm_plan,
            session_id=flow.session_id,
            verify_vault_quotes=True,
        )
        assert wp_lint.passed, wp_lint.to_feedback()
        for f in flow.state.working_papers.findings:
            cascade = evaluate_finding_cascade(f, quote_verified_in_vault=True)
            assert cascade.s_hat >= 0.80

        # Gate 2 Reviewer Decisions & Approval (bob)
        record_flow_decisions(flow, "gate2", "bob")
        flow.begin_phase_3("bob")
        assert flow.state.status == "RUNNING_PHASE_3"

        # Phase 3: Reporting (receives effective view)
        flow.generate_reporting()
        assert flow.state.status == "WAITING_HUMAN_GATE_3"
        assert flow.state.final_report is not None

        rep_lint = lint_report(
            flow.state.final_report,
            papers=flow.effective_working_papers(),
            racm=flow.state.racm_plan,
            expected_scale=DeficiencyScale.RISK_RATING,
        )
        assert rep_lint.passed, rep_lint.to_feedback()

        # Gate 3 Reviewer Decisions & Final Sign-off (carol)
        record_flow_decisions(flow, "gate3", "carol")
        flow.finalize_audit("carol")
        assert flow.state.status == "COMPLETED"

        # Verify hash-chained approval trail integrity
        trail_check = verify_trail(
            flow.state.approval_trail,
            artifacts={
                "racm_plan": flow.state.racm_plan,
                "working_papers": flow.state.working_papers,
                "final_report": flow.state.final_report,
            },
            decisions=flow.state.review_decisions,
        )
        assert trail_check["ok"], trail_check["detail"]

        # Export all 4 deliverables (RACM .xlsx, Working Papers .xlsx, Report .md, OSCAL 1.2.1 .json)
        ctx = ExportContext(
            session_id=flow.session_id,
            session_name=flow.state.theme,
            status=flow.state.status,
        )
        racm_bytes = racm_xlsx(flow.state.racm_plan, ctx)
        wp_bytes = working_papers_xlsx(
            flow.state.working_papers,
            ctx,
            view=flow.effective_view(),
        )
        md_text = report_markdown(
            flow.state.final_report,
            flow.state.approval_trail,
            ctx,
            view=flow.effective_view(),
        )
        oscal_str = oscal_json(
            flow.state.final_report,
            flow.state.working_papers,
            flow.state.racm_plan,
            flow.state.approval_trail,
            ctx,
            theme=flow.state.theme,
            prepared_by=flow.state.prepared_by,
            view=flow.effective_view(),
            decisions=flow.state.review_decisions,
        )
        assert len(racm_bytes) > 1000
        assert len(wp_bytes) > 1000
        assert "## Approval Trail" in md_text
        oscal_doc = json.loads(oscal_str)
        assert oscal_doc["assessment-results"]["metadata"]["oscal-version"] == "1.2.1"

    def test_end_to_end_native_crew_zero_framework_runtime(self, tmp_path, monkeypatch):
        """Run PlanningCrew, FieldworkCrew, and ReportingCrew through NativeCrew (DEMO_MODE=0)."""
        from swarm.crews.planning_crew import PlanningCrew
        from swarm.crews.fieldwork_crew import FieldworkCrew
        from swarm.crews.reporting_crew import ReportingCrew
        from swarm.schema import (
            DeficiencyEvaluationSetSchema,
            FinalReportSchema,
            OSCAL_SAR_ImportAP,
            OSCAL_SAR_Metadata,
            OSCAL_SAR_Schema,
            RiskControlMatrixSchema,
            WorkingPaperSchema,
        )

        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path / "vault"))
        monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setenv("QA_LLM_MODEL", "openai/gpt-4o-mini")
        monkeypatch.delenv("DEMO_MODE", raising=False)

        rec = EvidenceAssuranceProtocol.register_evidence(
            '{\n  "MinimumPasswordLength": 14,\n  "RequireSymbols": true\n}',
            "aws.iam.get_account_password_policy",
            session_id="native-e2e-session",
        )
        verified_papers = WorkingPaperSchema(
            theme="AWS IAM",
            findings=[
                AuditFindingSchema(
                    control_id="CTRL-01",
                    vault_id_reference=rec["vault_id"],
                    exact_quote_from_evidence='"MinimumPasswordLength": 14',
                    tod_conclusion="Effective",
                    toe_conclusion="Effective",
                    toe_basis="Test of one; relies on change-management ITGCs.",
                    items_tested=1,
                    exceptions_noted=0,
                    test_conclusion="Password policy meets 14-char minimum.",
                )
            ],
        )

        def fake_completion(*, spec, system_prompt, user_prompt, response_model):
            if response_model is RiskControlMatrixSchema:
                return make_racm()
            if response_model is WorkingPaperSchema:
                return verified_papers
            if response_model is QA_PushbackSchema:
                return QA_PushbackSchema(approved=True)
            if response_model is DeficiencyEvaluationSetSchema:
                return DeficiencyEvaluationSetSchema(
                    deficiency_scale=DeficiencyScale.RISK_RATING, evaluations=[]
                )
            if response_model is OSCAL_SAR_Schema:
                return OSCAL_SAR_Schema(
                    metadata=OSCAL_SAR_Metadata(
                        title="Native Zero-Framework SAR",
                        last_modified="2026-10-24",
                        version="1.0",
                    ),
                    import_ap=OSCAL_SAR_ImportAP(href="RACM"),
                    results=[],
                )
            if response_model is FinalReportSchema:
                return make_report()
            return {"content": "Intermediate analysis memo."}

        gateway = ZeroFrameworkLLMGateway(completion_fn=fake_completion)

        for crew_cls, inputs in (
            (
                PlanningCrew,
                {
                    "theme": "AWS IAM",
                    "business_context": "Fintech",
                    "frameworks": "CIS AWS",
                    "qa_feedback": "",
                },
            ),
            (
                FieldworkCrew,
                {
                    "theme": "AWS IAM",
                    "test_plan": "Control CTRL-01: Inspect policy",
                    "qa_feedback": "",
                },
            ),
            (
                ReportingCrew,
                {
                    "theme": "AWS IAM",
                    "scope_string": "Theme: AWS IAM",
                    "racm_summary": "RISK-01 -> CTRL-01",
                    "working_papers_string": verified_papers.model_dump_json(),
                    "findings_index": f"CTRL-01 | No exception | {rec['vault_id']}",
                    "deficiency_scale_guidance": "Risk rating",
                    "tone_qa_feedback": "",
                },
            ),
        ):
            c = crew_cls("native-e2e-session").crew()
            c.gateway = gateway
            # Avoid live AWS calls during the collector step in FieldworkCrew
            for agent in c.agents:
                agent.tools = []
            out = c.kickoff(inputs=inputs)
            assert out.tasks_output
            assert out.pydantic is not None

        # Verify cryptographic LLMCallReceipts were recorded across both model families
        assert len(gateway.receipts) >= 8
        families_used = {r.model_family for r in gateway.receipts}
        assert "google" in families_used
        assert "openai" in families_used

    def test_post_issuance_remediation_verification_crosswalk_and_oscal_drift(
        self, tmp_path, monkeypatch
    ):
        """Test IIA GIAS 15.2 remediation_verification, RACM Crosswalk xlsx, and OSCAL drift."""
        import io
        import json
        from openpyxl import load_workbook
        from api.exports import (
            RACM_CROSSWALK_HEADERS,
            ExportContext,
            oscal_json,
            report_markdown,
            working_papers_xlsx,
        )
        from review_helpers import record_flow_decisions  # type: ignore[import-not-found]
        from swarm.oscal_ar import evaluate_oscal_drift

        monkeypatch.setenv("EVIDENCE_VAULT_PATH", str(tmp_path / "vault"))
        monkeypatch.setenv("DEMO_MODE", "1")
        monkeypatch.setenv("DEMO_STEP_DELAY", "0")
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("DEMO_QA_REJECT_PHASE", raising=False)

        flow = AuditFlow()
        flow.session_id = "remediation-session-001"
        flow.state.theme = "AWS S3 Exposure"
        flow.record_preparer("Pat", require_review_decisions=True)
        flow.begin_phase_1()
        flow.generate_planning()
        flow.begin_phase_2("alice")
        flow.generate_fieldwork()
        record_flow_decisions(flow, "gate2", "bob")
        flow.begin_phase_3("bob")
        flow.generate_reporting()
        record_flow_decisions(flow, "gate3", "carol")
        flow.finalize_audit("carol")
        assert flow.state.status == "COMPLETED"

        # Verify generation_runs captured linter_passed and provenance
        assert len(flow.state.generation_runs) == 3
        for r in flow.state.generation_runs:
            assert r.linter_passed is True
            assert r.linter_violations == []

        ctx = ExportContext(
            session_id=flow.session_id,
            session_name=flow.state.theme,
            status=flow.state.status,
        )
        assert flow.state.final_report is not None
        assert flow.state.working_papers is not None
        assert flow.state.racm_plan is not None

        # Baseline OSCAL before remediation (DEF-01 is open)
        baseline_oscal = json.loads(
            oscal_json(
                flow.state.final_report,
                flow.state.working_papers,
                flow.state.racm_plan,
                flow.state.approval_trail,
                ctx,
                theme=flow.state.theme,
                prepared_by=flow.state.prepared_by,
                view=flow.effective_view(),
                decisions=flow.state.review_decisions,
            )
        )
        assert (
            baseline_oscal["assessment-results"]["results"][0]["risks"][0]["status"]
            == "open"
        )
        assert (
            "objectives-and-methods"
            in baseline_oscal["assessment-results"]["local-definitions"]
        )

        # Record post-issuance remediation verification (IIA GIAS 15.2)
        rem_decision = flow.record_decision(
            decision_type="remediation_verification",
            subject_id="DEF-01",
            decided_by="carol",
            values={
                "status": "remediated",
                "verified_on": "2026-10-25",
            },
            rationale="Retested S3 BlockPublicAccess via read-only collector; all 4 flags enabled.",
        )
        assert rem_decision.decision_type == "remediation_verification"
        view = flow.effective_view()
        assert view is not None
        dv = view.deficiency("DEF-01")
        assert dv is not None and dv.remediation_verification is not None
        assert dv.remediation_verification.values["status"] == "remediated"

        # Target OSCAL after remediation (DEF-01 is closed!)
        target_oscal = json.loads(
            oscal_json(
                flow.state.final_report,
                flow.state.working_papers,
                flow.state.racm_plan,
                flow.state.approval_trail,
                ctx,
                theme=flow.state.theme,
                prepared_by=flow.state.prepared_by,
                view=view,
                decisions=flow.state.review_decisions,
            )
        )
        risk0 = target_oscal["assessment-results"]["results"][0]["risks"][0]
        assert risk0["status"] == "closed"

        # Longitudinal OSCAL drift check (Lula pattern)
        drift = evaluate_oscal_drift(baseline_oscal, target_oscal)
        assert drift["passed"] is True
        assert drift["remediated_risks"] == ["DEF-01"]
        assert drift["regressed_controls"] == []

        # Report Markdown includes Remediation verification
        md = report_markdown(
            flow.state.final_report,
            flow.state.approval_trail,
            ctx,
            view=view,
        )
        assert "**Remediation verification** (remediated; verified on 2026-10-25" in md

        # Working Papers .xlsx with RACM Crosswalk worksheet
        wp_bytes = working_papers_xlsx(
            flow.state.working_papers,
            ctx,
            view=view,
            racm=flow.state.racm_plan,
        )
        wb = load_workbook(io.BytesIO(wp_bytes))
        assert wb.sheetnames == ["Findings", "RACM Crosswalk", "Export Info"]
        cw_rows = [list(r) for r in wb["RACM Crosswalk"].iter_rows(values_only=True)]
        assert cw_rows[0] == RACM_CROSSWALK_HEADERS
        assert cw_rows[1][2] == "CTRL-01"
