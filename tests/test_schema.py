"""
Tests for src/swarm/schema.py — Pydantic schema structural contracts.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.schema import (
    AuditFindingSchema,
    Control,
    ControlFrequency,
    ControlNature,
    ControlTesting,
    DeficiencyClassification,
    DeficiencyEvaluationSchema,
    DeficiencyEvaluationSetSchema,
    DeficiencyScale,
    DesignConclusion,
    FinalReportSchema,
    OSCAL_SAR_ImportAP,
    OSCAL_SAR_Metadata,
    OSCAL_SAR_Schema,
    OperatingConclusion,
    Risk,
    RiskControlMatrixSchema,
    SamplingMethod,
    FindingResult,
    WorkingPaperSchema,
)

MOCK_DIR = os.path.join(os.path.dirname(__file__), "mock_data")


def _make_metadata():
    return OSCAL_SAR_Metadata(
        title="Test Report", last_modified="2026-01-01T00:00:00Z", version="1.0"
    )


class TestOscalSarSchema:
    def test_import_ap_is_required(self):
        with pytest.raises(Exception):
            OSCAL_SAR_Schema(metadata=_make_metadata(), results=[])

    def test_import_ap_populates_correctly(self):
        schema = OSCAL_SAR_Schema(
            metadata=_make_metadata(),
            import_ap=OSCAL_SAR_ImportAP(href="urn:audit:fintech-s3-2026"),
            results=[],
        )
        assert schema.import_ap.href == "urn:audit:fintech-s3-2026"
        assert schema.import_ap.remarks is None


# ── Fieldwork findings ───────────────────────────────────────────────────────


def _finding(**kw):
    data = {
        "control_id": "CTRL-01",
        "vault_id_reference": "vault-1",
        "exact_quote_from_evidence": "MinimumPasswordLength=14",
        "test_conclusion": "Checked.",
        "tod_conclusion": "Effective",
        "toe_conclusion": "Effective",
    }
    data.update(kw)
    return AuditFindingSchema.model_validate(data)


class TestFinding:
    def test_result_and_preliminary_deficiency_are_derived(self):
        f = _finding()
        assert f.result == FindingResult.NO_EXCEPTION
        assert f.preliminary_deficiency is False
        f = _finding(toe_conclusion="Exceptions noted", exceptions_noted=2)
        assert f.result == FindingResult.EXCEPTION
        assert f.preliminary_deficiency is True
        f = _finding(tod_conclusion="Ineffective", toe_conclusion="Not tested")
        assert f.result == FindingResult.EXCEPTION

    def test_design_effective_toe_not_tested_is_no_exception(self):
        f = _finding(toe_conclusion="Not tested", toe_basis="point in time")
        assert f.result == FindingResult.NO_EXCEPTION

    def test_not_tested_needs_no_evidence(self):
        f = _finding(
            tod_conclusion="Not tested",
            toe_conclusion="Not tested",
            vault_id_reference="",
            exact_quote_from_evidence="",
        )
        assert f.result == FindingResult.NOT_TESTED
        assert f.preliminary_deficiency is False

    @pytest.mark.parametrize(
        "missing", ["vault_id_reference", "exact_quote_from_evidence"]
    )
    def test_effective_without_evidence_is_rejected(self, missing):
        with pytest.raises(ValueError, match="verbatim evidence quote"):
            _finding(**{missing: "  "})

    def test_exception_without_evidence_is_rejected(self):
        with pytest.raises(ValueError, match="verbatim evidence quote"):
            _finding(toe_conclusion="Ineffective", exact_quote_from_evidence="")

    def test_contradictory_result_is_rejected(self):
        with pytest.raises(ValueError, match="contradicts"):
            _finding(result="No exception", toe_conclusion="Ineffective")
        with pytest.raises(ValueError, match="contradicts"):
            _finding(
                toe_conclusion="Not tested", exceptions_noted=1, result="No exception"
            )
        with pytest.raises(ValueError, match="Exceptions noted"):
            _finding(exceptions_noted=1)

    def test_preliminary_deficiency_needs_an_exception(self):
        with pytest.raises(ValueError, match="preliminary_deficiency"):
            _finding(preliminary_deficiency=True)

    def test_exception_may_be_judged_not_a_deficiency(self):
        f = _finding(
            toe_conclusion="Exceptions noted",
            exceptions_noted=1,
            preliminary_deficiency=False,
        )
        assert f.result == FindingResult.EXCEPTION
        assert f.preliminary_deficiency is False

    def test_enum_drift_is_normalised_but_unknown_values_fail(self):
        f = _finding(
            tod_conclusion="effective",
            toe_conclusion="EXCEPTIONS_NOTED",
            exceptions_noted="1 of 25",
            items_tested="25 items",
        )
        assert f.toe_conclusion == OperatingConclusion.EXCEPTIONS_NOTED
        assert (f.exceptions_noted, f.items_tested) == (1, 25)
        with pytest.raises(ValueError):
            _finding(toe_conclusion="Pass")
        with pytest.raises(ValueError):
            _finding(tod_conclusion="Material Weakness")

    def test_llm_schema_has_no_severity_or_legacy_field(self):
        text = json.dumps(WorkingPaperSchema.model_json_schema())
        assert "legacy_severity" not in text
        assert '"severity"' not in text
        assert "Material Weakness" not in text


# ── Backward compatibility ───────────────────────────────────────────────────


class TestLegacyMigration:
    @pytest.mark.parametrize(
        "severity, tod, toe, result, prelim",
        [
            ("Pass", "Effective", "Effective", "No exception", False),
            ("pass", "Effective", "Effective", "No exception", False),
            ("Control Deficiency", "Not tested", "Ineffective", "Exception", True),
            ("Significant Deficiency", "Not tested", "Ineffective", "Exception", True),
            ("Material Weakness", "Not tested", "Ineffective", "Exception", True),
            ("High", "Not tested", "Ineffective", "Exception", True),
            ("Not tested", "Not tested", "Not tested", "Not tested", False),
            (None, "Not tested", "Not tested", "Not tested", False),
        ],
    )
    def test_old_severity_maps_to_new_fields(self, severity, tod, toe, result, prelim):
        f = AuditFindingSchema.model_validate(
            {
                "control_id": "CTRL-01",
                "vault_id_reference": "v",
                "exact_quote_from_evidence": "q",
                "test_conclusion": "old",
                "severity": severity,
            }
        )
        assert (f.tod_conclusion, f.toe_conclusion, f.result) == (tod, toe, result)
        assert f.preliminary_deficiency is prelim
        assert f.legacy_severity == str(severity)

    def test_stray_severity_on_new_shape_is_ignored(self):
        f = _finding(severity="Material Weakness")
        assert f.result == FindingResult.NO_EXCEPTION
        assert f.legacy_severity is None

    def test_old_snapshot_loads_through_repository(self, tmp_path, monkeypatch):
        from swarm import session_manager
        from swarm.state.repository import FlowRepository

        monkeypatch.setattr(
            session_manager, "SESSIONS_PATH", str(tmp_path / "sessions.json")
        )
        with open(os.path.join(MOCK_DIR, "legacy_session_snapshot.json")) as fh:
            snapshot = json.load(fh)
        session_manager.save_session("legacy-1", "Old audit", "ctx")
        session_manager.update_session("legacy-1", state_snapshot=snapshot)

        loaded = FlowRepository().load("legacy-1")
        assert loaded is not None
        assert loaded.skipped_fields == []
        state = loaded.flow.state
        assert state.status == "WAITING_HUMAN_GATE_3"
        assert state.working_papers is not None
        assert state.racm_plan is not None
        assert state.final_report is not None
        results = [f.result for f in state.working_papers.findings]
        assert results == ["Exception", "No exception", "Exception"]
        assert state.working_papers.findings[0].legacy_severity == "Material Weakness"
        assert state.racm_plan.risks[0].controls[0].frequency is None
        assert state.final_report.deficiency_evaluations == []
        assert state.final_report.deficiency_scale is None
        # And it round-trips in the new shape.
        dumped = state.model_dump(mode="json")
        assert "severity" not in dumped["working_papers"]["findings"][0]
        WorkingPaperSchema.model_validate(dumped["working_papers"])


# ── RACM attributes ──────────────────────────────────────────────────────────

_NO_STEPS = {"test_of_design": [], "test_of_effectiveness": []}


class TestRacmAttributes:
    def test_attributes_are_optional_for_llm_output(self):
        c = Control.model_validate(
            {"control_id": "C", "description": "d", "testing_procedures": _NO_STEPS}
        )
        assert c.frequency is None and c.key_control is None
        assert c.assertions == [] and c.ipe == []

    def test_attributes_and_test_design_validate(self):
        c = Control.model_validate(
            {
                "control_id": "C",
                "description": "d",
                "control_owner": "IT Manager",
                "frequency": "multiple times per day",
                "nature": "it-dependent manual",
                "control_type": "detective",
                "key_control": True,
                "assertions": ["Completeness"],
                "ipe": ["User listing"],
                "testing_procedures": {
                    **_NO_STEPS,
                    "population": "Ticketing tool export",
                    "sample_size": "25",
                    "sampling_method": "random",
                    "period_of_reliance": "FY2026",
                },
            }
        )
        assert c.frequency == ControlFrequency.MULTIPLE_TIMES_PER_DAY
        assert c.nature == ControlNature.IT_DEPENDENT_MANUAL
        tp = c.testing_procedures
        assert tp.population is not None
        assert tp.population.source == "Ticketing tool export"
        assert tp.population.completeness_procedure == "Not specified"
        assert (tp.sample_size, tp.sampling_method) == (25, SamplingMethod.RANDOM)

    def test_invalid_enum_and_negative_sample_fail(self):
        with pytest.raises(ValueError):
            ControlTesting.model_validate({**_NO_STEPS, "sample_size": -1})
        with pytest.raises(ValueError):
            Control.model_validate(
                {
                    "control_id": "C",
                    "description": "d",
                    "nature": "Semi-automated",
                    "testing_procedures": _NO_STEPS,
                }
            )

    def test_mapping_guidance_names_control_frameworks(self):
        desc = Risk.model_fields["regulatory_mapping"].description or ""
        assert "COSO 2013" in desc and "NIST SP 800-53" in desc
        assert "not mappings" in desc
        text = json.dumps(RiskControlMatrixSchema.model_json_schema())
        assert "e.g. COSO, PCAOB AS 2201" not in text


# ── Deficiency evaluation ────────────────────────────────────────────────────


def _evaluation(**kw):
    data = {
        "deficiency_id": "DEF-01",
        "title": "t",
        "related_findings": ["CTRL-01"],
        "compensating_controls": "None identified",
        "likelihood": "Medium",
        "magnitude": "High",
        "classification": "High",
        "rationale": "r",
    }
    data.update(kw)
    return DeficiencyEvaluationSchema.model_validate(data)


_REPORT = {
    "executive_summary": "s",
    "detailed_report": "d",
    "compliance_tone_approved": True,
}


class TestDeficiencyEvaluation:
    def test_needs_related_findings(self):
        with pytest.raises(ValueError):
            _evaluation(related_findings=[])

    def test_material_weakness_needs_reasonable_possibility_of_material_impact(self):
        _evaluation(classification="Material Weakness")
        with pytest.raises(ValueError, match="Material Weakness"):
            _evaluation(classification="Material Weakness", likelihood="Low")
        with pytest.raises(ValueError, match="Material Weakness"):
            _evaluation(classification="material weakness", magnitude="Medium")

    def test_set_rejects_classifications_off_scale(self):
        with pytest.raises(ValueError, match="not on the"):
            DeficiencyEvaluationSetSchema.model_validate(
                {
                    "deficiency_scale": "Risk rating",
                    "evaluations": [
                        _evaluation(classification="Significant Deficiency")
                    ],
                }
            )
        ok = DeficiencyEvaluationSetSchema.model_validate(
            {
                "deficiency_scale": "icfr deficiency scale",
                "evaluations": [_evaluation(classification="Control Deficiency")],
            }
        )
        assert ok.deficiency_scale == DeficiencyScale.ICFR

    def test_report_infers_scale(self):
        report = FinalReportSchema.model_validate(
            {**_REPORT, "deficiency_evaluations": [_evaluation().model_dump()]}
        )
        assert report.deficiency_scale == DeficiencyScale.RISK_RATING
        report = FinalReportSchema.model_validate(_REPORT)
        assert report.deficiency_scale is None
        # "Not a deficiency" fits either scale: not inferred.
        only_nd = _evaluation(classification="Not a deficiency").model_dump()
        report = FinalReportSchema.model_validate(
            {**_REPORT, "deficiency_evaluations": [only_nd]}
        )
        assert report.deficiency_scale is None

    def test_report_rejects_mixed_or_off_scale_classifications(self):
        with pytest.raises(ValueError, match="mix"):
            FinalReportSchema.model_validate(
                {
                    **_REPORT,
                    "deficiency_evaluations": [
                        _evaluation().model_dump(),
                        _evaluation(
                            deficiency_id="DEF-02", classification="Control Deficiency"
                        ).model_dump(),
                    ],
                }
            )
        with pytest.raises(ValueError, match="not on the"):
            FinalReportSchema.model_validate(
                {
                    **_REPORT,
                    "deficiency_scale": "ICFR deficiency scale",
                    "deficiency_evaluations": [_evaluation().model_dump()],
                }
            )

    def test_descriptions_say_draft_for_gate_3(self):
        assert "Gate 3" in (
            DeficiencyEvaluationSchema.model_fields["rationale"].description or ""
        )
        assert "Gate 3" in (
            FinalReportSchema.model_fields["deficiency_evaluations"].description or ""
        )
        assert DeficiencyClassification.MATERIAL_WEAKNESS.value == "Material Weakness"
        assert DesignConclusion.NOT_TESTED.value == "Not tested"
