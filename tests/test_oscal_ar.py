"""OSCAL Assessment Results export: validated against NIST's official schema.

The schema is vendored (tests/fixtures/oscal/, see its README for source,
version and license), so this runs offline in CI's unit-test job.

OSCAL's JSON schema uses Unicode property escapes (``\\p{L}``, ``\\p{N}``) in
its ``pattern`` keywords, which Python's ``re`` cannot compile; the validator
below evaluates ``pattern`` with the ``regex`` module instead, so every OSCAL
pattern (tokens, UUIDs, date-times, URIs, strings) is enforced.
"""

import copy
import json
import os
import re
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
import regex
from fastapi.testclient import TestClient
from jsonschema import Draft7Validator, FormatChecker, ValidationError, validators

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flow_builders import make_racm  # type: ignore[import-not-found]
from review_helpers import record_demo_decisions  # type: ignore[import-not-found]
from api.exports import ExportContext, oscal_json
from api.job_store import remove_flow, set_flow
from swarm import session_manager
from swarm.audit_flow import AuditFlow
from swarm.demo import DEMO_LABEL
from swarm.evidence import EvidenceAssuranceProtocol
from swarm.oscal_ar import (
    OSCAL_VERSION,
    PROJECT_NS,
    build_assessment_results,
    element_uuid,
    oscal_datetime,
    token,
)
from swarm.schema import (
    AuditFindingSchema,
    DeficiencyEvaluationSchema,
    FinalReportSchema,
    OSCAL_SAR_ImportAP,
    OSCAL_SAR_Metadata,
    OSCAL_SAR_Observation,
    OSCAL_SAR_Result,
    OSCAL_SAR_Schema,
    WorkingPaperSchema,
)
from swarm.state.repository import FlowRepository

AUTH = {"Authorization": "Bearer test-token"}
SCHEMA_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "oscal"
    / "oscal_assessment-results_schema-1.2.1.json"
)
SID = "11111111-2222-4333-8444-555555555555"


def _pattern(validator, pattern, instance, schema):
    if validator.is_type(instance, "string") and not regex.search(pattern, instance):
        yield ValidationError(f"{instance!r} does not match {pattern!r}")


_OscalValidator = validators.extend(Draft7Validator, {"pattern": _pattern})

# Same Unicode-aware engine for the metaschema's "regex" format check.
_FORMATS = FormatChecker()
_FORMATS.checks("regex", raises=regex.error)(lambda s: bool(regex.compile(s)))


@pytest.fixture(scope="module")
def schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def validator(schema):
    Draft7Validator(Draft7Validator.META_SCHEMA, format_checker=_FORMATS).validate(
        schema
    )
    return _OscalValidator(schema, format_checker=_FORMATS)


def assert_valid(validator, document):
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.path))
    assert not errors, "\n".join(
        f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors[:20]
    )


# ── Fixtures: a finding-rich synthetic session ──────────────────────────────


def _rich_papers(vault_id: str) -> WorkingPaperSchema:
    return WorkingPaperSchema(
        theme="AWS S3",
        findings=[
            AuditFindingSchema(
                control_id="CTRL-01",
                vault_id_reference=vault_id,
                exact_quote_from_evidence="BlockPublicAcls: true",
                tod_conclusion="Effective",
                toe_conclusion="Effective",
                toe_basis="Test of one;\nrelies on change-management ITGCs.",
                items_tested=1,
                exceptions_noted=0,
                test_conclusion="Control operating effectively.",
            ),
            AuditFindingSchema(
                control_id="CTRL-02",
                vault_id_reference=vault_id,
                exact_quote_from_evidence="MinimumPasswordLength: 8",
                tod_conclusion="Ineffective",
                toe_conclusion="Not tested",
                test_conclusion="Password length below policy.",
            ),
            AuditFindingSchema(
                control_id="CTRL-03",
                tod_conclusion="Not tested",
                toe_conclusion="Not tested",
                test_conclusion="No evidence tool covers this control.",
            ),
            AuditFindingSchema(
                control_id="CTRL-04",
                vault_id_reference="not-a-uuid",
                exact_quote_from_evidence="Enabled: true",
                tod_conclusion="Effective",
                toe_conclusion="Exceptions noted",
                items_tested=25,
                exceptions_noted=2,
                test_conclusion="Two of 25 sampled changes lacked approval.",
            ),
            # Model-written IDs are not always OSCAL tokens.
            AuditFindingSchema(
                control_id="1.2 IAM: root MFA",
                vault_id_reference=vault_id,
                exact_quote_from_evidence="BlockPublicAcls: true",
                tod_conclusion="Effective",
                toe_conclusion="Not tested",
                test_conclusion="Root MFA enabled at read time.",
            ),
        ],
    )


def _rich_report(with_sar: bool = True) -> FinalReportSchema:
    sar = OSCAL_SAR_Schema(
        metadata=OSCAL_SAR_Metadata(
            title="S3 audit\nresults", last_modified="yesterday", version="v1"
        ),
        import_ap=OSCAL_SAR_ImportAP(href="S3 audit theme"),
        results=[
            OSCAL_SAR_Result(
                assessment_result_id="r1",
                start_date="2026-01-01",
                end_date="2026-01-02",
                observations=[
                    OSCAL_SAR_Observation(
                        observation_id="o1",
                        description="Narrative for CTRL-02 ![x](http://evil/?d=1)",
                        methods=["examine"],
                        subjects=["CTRL-02", "INVENTED-99"],
                        relevant_evidence=["invented-vault-id"],
                    )
                ],
            )
        ],
    )
    return FinalReportSchema(
        executive_summary="Two exceptions.\n\n![leak](http://evil.example/x)",
        detailed_report="Details.",
        compliance_tone_approved=True,
        deficiency_scale="Risk rating",
        deficiency_evaluations=[
            DeficiencyEvaluationSchema(
                deficiency_id="DEF-01",
                title="Weak password policy",
                related_findings=["CTRL-02"],
                related_risks=["RISK-01"],
                compensating_controls="None identified",
                likelihood="Medium",
                magnitude="Medium",
                classification="Medium",
                rationale="Single account-wide setting.",
            ),
            DeficiencyEvaluationSchema(
                deficiency_id="DEF-02",
                title="Change approval gaps",
                related_findings=["CTRL-04", "CTRL-02"],
                compensating_controls="Post-deployment review (not tested)",
                likelihood="Low",
                magnitude="Low",
                classification="Not a deficiency",
                rationale="Isolated exceptions.",
            ),
        ],
        oscal_sar=sar if with_sar else None,
    )


def _trail() -> list[dict]:
    return [
        {
            "gate": "Audit created",
            "human": "Pat Preparer",
            "timestamp": "2026-03-01T09:00:00+00:00",
            "action": "audit_created",
            "hash_alg": "sha256",
            "prev_hash": "0" * 64,
            "entry_hash": "a" * 64,
        },
        {
            "gate": "Return for rework (Planning)",
            "human": "alice",
            "timestamp": "2026-03-02T09:00:00.123456+00:00",
            "action": "return_for_rework",
            "notes": "Add IPE\nprocedures.",
            "artifact": "racm_plan",
        },
        {
            "gate": "Gate 1 (Planning)",
            "human": "alice",
            "timestamp": "2026-03-03T09:00:00",  # naive: treated as UTC
            "action": "gate_approval",
            "artifact": "racm_plan",
            "artifact_digest": "b" * 64,
        },
        {
            "gate": "Gate 2 (Fieldwork)",
            "human": "  bob  ",
            "timestamp": "2026-03-05T09:00:00Z",
            "action": "gate_approval",
            "artifact": "working_papers",
        },
        {
            "gate": "Gate 3 (Reporting)",
            "human": "carol",
            "timestamp": "not a date",
            "action": "gate_approval",
            "artifact": "final_report",
        },
    ]


def _build(status="COMPLETED", *, report=None, papers=None, trail=None, **kw):
    if papers is None:
        rec = EvidenceAssuranceProtocol.register_evidence(
            "BlockPublicAcls: true\nMinimumPasswordLength: 8", "get_public_access_block"
        )
        papers = _rich_papers(rec["vault_id"])
    ctx = ExportContext(session_id=SID, session_name="S3 audit", status=status)
    body = oscal_json(
        report or _rich_report(),
        papers,
        kw.pop("racm", make_racm()),
        _trail() if trail is None else trail,
        ctx,
        theme="AWS S3",
        prepared_by=kw.pop("prepared_by", "Pat Preparer"),
    )
    return json.loads(body)


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _ar(doc):
    return doc["assessment-results"]


# ── The validator itself is live ────────────────────────────────────────────


class TestValidatorIsLive:
    def test_schema_is_the_official_release(self, schema):
        assert schema["$id"] == (
            f"http://csrc.nist.gov/ns/oscal/{OSCAL_VERSION}/oscal-ar-schema.json"
        )

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda ar: ar.update(uuid="not-a-uuid"),
            lambda ar: ar.update(uuid=str(uuid.uuid1())),  # v1 not allowed
            lambda ar: ar["metadata"].pop("oscal-version"),
            lambda ar: ar["metadata"].update(title="two\nlines"),
            lambda ar: ar["results"][0]["reviewed-controls"]["control-selections"][0][
                "include-controls"
            ][0].update({"control-id": "1bad"}),  # \\p{L} token rule
            lambda ar: ar["results"][0]["observations"][0]["props"][0].update(
                ns="not a uri"
            ),
            lambda ar: ar["results"][0]["observations"][0]["props"][0].update(
                value=" padded"
            ),
            lambda ar: ar["results"][0]["observations"][0].update(
                collected="2026-01-01T00:00:00"  # no timezone
            ),
            lambda ar: ar["results"][0]["findings"][0]["target"]["status"].update(
                state="not-tested"
            ),
            lambda ar: ar["results"][0].update(extra="field"),
        ],
    )
    def test_rejects_broken_documents(self, validator, mutate):
        doc = _build()
        assert_valid(validator, doc)
        broken = copy.deepcopy(doc)
        mutate(_ar(broken))
        assert list(validator.iter_errors(broken))


# ── Conformance ─────────────────────────────────────────────────────────────


class TestConformance:
    def test_rich_report_validates(self, validator):
        assert_valid(validator, _build())

    @pytest.mark.parametrize("status", ["WAITING_HUMAN_GATE_3", "QA_REJECTED_PHASE_3"])
    def test_draft_report_validates(self, validator, status):
        assert_valid(validator, _build(status))

    def test_without_llm_sar_racm_trail_or_preparer_validates(self, validator):
        doc = _build(
            report=_rich_report(with_sar=False), racm=None, trail=[], prepared_by=""
        )
        assert_valid(validator, doc)
        assert "roles" not in _ar(doc)["metadata"]

    def test_legacy_severity_finding_validates(self, validator):
        papers = WorkingPaperSchema(
            theme="t",
            findings=[
                AuditFindingSchema.model_validate(
                    {
                        "control_id": "C-1",
                        "severity": "Not Tested",
                        "test_conclusion": "x",
                    }
                )
            ],
        )
        doc = _build(papers=papers, report=_rich_report(with_sar=False))
        assert_valid(validator, doc)
        props = {p["name"]: p["value"] for p in _obs(doc)[0]["props"]}
        assert props["legacy-severity"] == "Not Tested"

    def test_empty_papers_and_racm_are_refused(self):
        with pytest.raises(ValueError):
            _build(papers=WorkingPaperSchema(theme="t", findings=[]), racm=None)


def _obs(doc):
    return _ar(doc)["results"][0]["observations"]


# ── Mapping ─────────────────────────────────────────────────────────────────


class TestMapping:
    def test_metadata(self):
        md = _ar(_build())["metadata"]
        assert md["oscal-version"] == OSCAL_VERSION == "1.2.1"
        assert md["title"] == "S3 audit results"  # model title, one line
        # latest parseable trail entry, not the export time or the LLM's date
        assert md["last-modified"] == "2026-03-05T09:00:00+00:00"
        roles = {r["id"] for r in md["roles"]}
        assert roles == {"prepared-by", "gate-approver", "content-approver"}
        names = {p["uuid"]: p["name"] for p in md["parties"]}
        rp = {
            r["role-id"]: [names[u] for u in r["party-uuids"]]
            for r in md["responsible-parties"]
        }
        assert rp == {
            "prepared-by": ["Pat Preparer"],
            "gate-approver": ["alice", "bob", "carol"],
            "content-approver": ["carol"],
        }

    def test_import_ap_resolves_to_back_matter(self):
        ar = _ar(_build())
        href = ar["import-ap"]["href"]
        assert href.startswith("#")
        plan = next(r for r in ar["back-matter"]["resources"] if r["uuid"] == href[1:])
        assert "No OSCAL assessment plan" in plan["description"]

    def test_reviewed_controls_are_racm_ids_as_tokens(self):
        result = _ar(_build())["results"][0]
        ids = [
            c["control-id"]
            for c in result["reviewed-controls"]["control-selections"][0][
                "include-controls"
            ]
        ]
        assert ids == ["CTRL-01", "CTRL-02", "CTRL-03", "CTRL-04", "_1.2_IAM__root_MFA"]

    def test_observations_one_per_finding(self):
        obs = _obs(_build())
        assert len(obs) == 5
        props = [{p["name"]: p["value"] for p in o["props"]} for o in obs]
        assert props[0]["tod-conclusion"] == "Effective"
        assert props[0]["exceptions-noted"] == "0"  # zero is kept
        assert props[3]["toe-conclusion"] == "Exceptions noted"
        assert props[4]["control-id"] == "1.2 IAM: root MFA"  # original kept
        assert [o["methods"] for o in obs] == [
            ["EXAMINE", "TEST"],
            ["EXAMINE"],
            ["EXAMINE"],
            ["EXAMINE", "TEST"],
            ["EXAMINE"],
        ]
        assert "relevant-evidence" not in obs[2]
        assert "Not tested" in obs[2]["remarks"]

    def test_evidence_links_to_vault_records(self):
        ar = _ar(_build())
        resources = {r["uuid"]: r for r in ar["back-matter"]["resources"]}
        ev = _obs({"assessment-results": ar})[0]["relevant-evidence"][0]
        res = resources[ev["href"][1:]]
        rp = {p["name"]: p["value"] for p in res["props"]}
        assert rp["evidence-source"] == "get_public_access_block"
        assert rp["vault-record-found"] == "true"
        evp = {p["name"]: p["value"] for p in ev["props"]}
        assert evp["quote-verified-in-vault"] == "true"
        # CTRL-04 cites a non-UUID vault ID: not found, quote not verified.
        ev4 = _obs({"assessment-results": ar})[3]["relevant-evidence"][0]
        assert {p["name"]: p["value"] for p in ev4["props"]}[
            "quote-verified-in-vault"
        ] == "false"
        missing = resources[ev4["href"][1:]]
        assert "no such record was found" in missing["description"]
        # Payload never embedded.
        assert "MinimumPasswordLength: 8\n" not in json.dumps(ar)

    def test_observation_collected_is_the_vault_timestamp(self):
        ar = _ar(_build())
        res = {r["uuid"]: r for r in ar["back-matter"]["resources"]}
        obs = ar["results"][0]["observations"]
        ev_uuid = obs[0]["relevant-evidence"][0]["href"][1:]
        collected = {p["name"]: p["value"] for p in res[ev_uuid]["props"]}["collected"]
        assert obs[0]["collected"] == collected
        assert ar["results"][0]["start"] == collected
        # No evidence: falls back to the Gate 2 approval time.
        assert obs[2]["collected"] == "2026-03-05T09:00:00+00:00"

    def test_findings_only_for_tested_controls(self):
        result = _ar(_build())["results"][0]
        states = {
            f["target"]["target-id"]: (
                f["target"]["type"],
                f["target"]["status"]["state"],
                f["target"]["status"]["reason"],
            )
            for f in result["findings"]
        }
        assert states == {
            "CTRL-01": ("objective-id", "satisfied", "pass"),
            "CTRL-02": ("objective-id", "not-satisfied", "fail"),
            "CTRL-04": ("objective-id", "not-satisfied", "fail"),
            "_1.2_IAM__root_MFA": ("objective-id", "satisfied", "pass"),
        }
        mfa = result["findings"][3]["target"]["status"]
        assert "operating effectiveness" in mfa["remarks"]

    def test_risks_from_deficiency_evaluations(self):
        result = _ar(_build("WAITING_HUMAN_GATE_3"))["results"][0]
        risks = {r["uuid"]: r for r in result["risks"]}
        assert len(risks) == 2
        r1, r2 = result["risks"]
        p1 = {p["name"]: p["value"] for p in r1["props"]}
        assert p1["classification"] == "Medium"
        assert p1["classification-state"] == "draft"
        assert p1["racm-risk-id"] == "RISK-01"
        assert "DRAFT" in r1["description"]
        assert r1["status"] == "open"
        obs = {o["uuid"]: o for o in result["observations"]}
        # DEF-02 relates CTRL-04 and CTRL-02 observations
        assert [
            {p["name"]: p["value"] for p in obs[x["observation-uuid"]]["props"]}[
                "control-id"
            ]
            for x in r2["related-observations"]
        ] == ["CTRL-04", "CTRL-02"]
        ctrl2 = next(
            f for f in result["findings"] if f["target"]["target-id"] == "CTRL-02"
        )
        assert [x["risk-uuid"] for x in ctrl2["related-risks"]] == list(risks)

    def test_approved_report_marks_risks_approved(self):
        r1 = _ar(_build("COMPLETED"))["results"][0]["risks"][0]
        props = {p["name"]: p["value"] for p in r1["props"]}
        assert props["classification-state"] == "approved-with-report"

    def test_assessment_log_is_the_trail(self):
        entries = _ar(_build())["results"][0]["assessment-log"]["entries"]
        assert len(entries) == 5
        assert entries[1]["description"].startswith("return_for_rework by alice")
        assert "Add IPE" in entries[1]["description"]
        assert entries[2]["start"] == "2026-03-03T09:00:00+00:00"
        props = {p["name"]: p["value"] for p in entries[0]["props"]}
        assert props["trail-entry-hash"] == "a" * 64

    def test_llm_output_cannot_add_controls_or_evidence(self):
        text = json.dumps(_build())
        assert "INVENTED-99" not in text
        assert "invented-vault-id" not in text
        # its narrative is kept as a labelled remark, sanitised
        assert "Reporting-crew narrative (model-drafted): Narrative for CTRL-02" in text
        assert "evil" not in text


# ── Props, namespaces, UUIDs ────────────────────────────────────────────────

_TOKEN = regex.compile(r"^(\p{L}|_)(\p{L}|\p{N}|[.\-_])*$")
_UUID45 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[45][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class TestIdentifiers:
    def test_every_prop_is_namespaced_and_well_formed(self):
        doc = _build()
        props = [p for node in _walk(doc) for p in node.get("props", []) if "name" in p]
        assert props
        for p in props:
            assert p["ns"] == PROJECT_NS
            assert _TOKEN.match(p["name"]), p
            assert p["value"] == " ".join(p["value"].split()) and p["value"], p

    def test_all_uuids_are_v5_and_unique(self):
        doc = _build()
        uuids = [n["uuid"] for n in _walk(doc) if isinstance(n.get("uuid"), str)]
        assert all(_UUID45.match(u) and uuid.UUID(u).version == 5 for u in uuids)
        assert len(uuids) == len(set(uuids))

    def test_references_resolve(self):
        doc = _build()
        defined = {n["uuid"] for n in _walk(doc) if isinstance(n.get("uuid"), str)}
        refs = [
            v
            for n in _walk(doc)
            for k, v in n.items()
            if k.endswith("-uuid") and isinstance(v, str)
        ] + [u for n in _walk(doc) for u in n.get("party-uuids", [])]
        assert refs and set(refs) <= defined
        # "#<uuid>" hrefs point at back-matter resources.
        resources = {r["uuid"] for r in _ar(doc)["back-matter"]["resources"]}
        hrefs = [
            n["href"][1:] for n in _walk(doc) if str(n.get("href", "")).startswith("#")
        ]
        assert len(hrefs) >= 2 and set(hrefs) <= resources
        # roles used by responsible-parties are defined
        md = _ar(doc)["metadata"]
        assert {r["role-id"] for r in md["responsible-parties"]} <= {
            r["id"] for r in md["roles"]
        }

    def test_deterministic(self):
        # same vault record for both builds
        rec = EvidenceAssuranceProtocol.register_evidence(
            "BlockPublicAcls: true\nMinimumPasswordLength: 8", "op"
        )
        a = _build(papers=_rich_papers(rec["vault_id"]))
        b = _build(papers=_rich_papers(rec["vault_id"]))
        assert a == b

    def test_uuids_stable_per_element_across_revisions(self):
        rec = EvidenceAssuranceProtocol.register_evidence("BlockPublicAcls: true", "op")
        papers = _rich_papers(rec["vault_id"])
        before = _ar(_build(papers=papers))
        papers.findings[0].test_conclusion = "Revised wording."
        after = _ar(_build(papers=papers))
        assert before["uuid"] == after["uuid"]  # per-subject document UUID
        assert [o["uuid"] for o in _obs({"assessment-results": before})] == [
            o["uuid"] for o in _obs({"assessment-results": after})
        ]
        assert before["metadata"]["version"] != after["metadata"]["version"]

    def test_element_uuid_depends_on_session(self):
        assert element_uuid("a", "result") != element_uuid("b", "result")
        assert uuid.UUID(element_uuid("a", "result")).version == 5

    @pytest.mark.parametrize(
        "raw,expected",
        [("CTRL-01", "CTRL-01"), ("1.2", "_1.2"), ("a b:c", "a_b_c"), ("", "_")],
    )
    def test_token(self, raw, expected):
        assert token(raw) == expected
        assert _TOKEN.match(token(raw))

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00+00:00"),
            ("2026-01-01T00:00:00", "2026-01-01T00:00:00+00:00"),
            ("2026-01-01T05:37:00+05:37", "2026-01-01T00:00:00+00:00"),
            ("nope", None),
            (None, None),
        ],
    )
    def test_oscal_datetime(self, raw, expected):
        assert oscal_datetime(raw) == expected

    def test_direct_builder_without_sanitiser(self, validator):
        rec = EvidenceAssuranceProtocol.register_evidence("BlockPublicAcls: true", "op")
        doc = build_assessment_results(
            session_id=SID,
            session_name="n",
            session_status="COMPLETED",
            report_state="Approved at Gate 3",
            theme="",
            report=_rich_report(),
            papers=_rich_papers(rec["vault_id"]),
            racm=None,
            trail=[],
        )
        assert_valid(validator, doc)


# ── End to end: DEMO_MODE session through the API ───────────────────────────


class _InlineExecutor:
    def submit(self, session_id, fn, *args):
        fn(*args)


@pytest.fixture
def demo_client(monkeypatch, tmp_path):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DEMO_STEP_DELAY", "0")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEMO_QA_REJECT_PHASE", raising=False)
    monkeypatch.setenv("API_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("TRAIL_ANCHORS_PATH", str(tmp_path / "anchors.json"))
    monkeypatch.setattr(
        session_manager, "SESSIONS_PATH", str(tmp_path / "audit_sessions.json")
    )
    from api.main import app

    with patch("api.routers.sessions.get_executor", return_value=_InlineExecutor()):
        yield TestClient(app)


def test_demo_session_export_validates(demo_client, validator):
    c = demo_client
    r = c.post(
        "/api/sessions",
        headers=AUTH,
        json={
            "theme": "S3 exposure",
            "business_context": "Fintech",
            "prepared_by": "Pat",
        },
    )
    sid = r.json()["session_id"]
    try:
        path = f"/api/sessions/{sid}/export/oscal.json"
        assert c.get(path, headers=AUTH).status_code == 404  # no report yet
        for gate, who in ((1, "alice"), (2, "bob")):
            if gate == 2:
                record_demo_decisions(c, sid, "gate2", who, AUTH)
            r = c.patch(
                f"/api/sessions/{sid}/approve",
                headers=AUTH,
                json={"gate_number": gate, "human_id": who},
            )
            assert r.status_code == 200, r.text
        draft = c.get(path, headers=AUTH)
        assert draft.status_code == 200
        assert_valid(validator, draft.json())
        record_demo_decisions(c, sid, "gate3", "carol", AUTH)
        r = c.patch(
            f"/api/sessions/{sid}/approve",
            headers=AUTH,
            json={"gate_number": 3, "human_id": "carol"},
        )
        assert r.status_code == 200, r.text
        remove_flow(sid)  # served from disk
        r = c.get(path, headers=AUTH)
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/json"
        doc = r.json()
        assert_valid(validator, doc)
        ar = _ar(doc)
        assert DEMO_LABEL in ar["metadata"]["title"]
        assert ar["results"][0]["findings"]
        assert ar["results"][0]["risks"]
        assert all(
            DEMO_LABEL in o["description"] for o in ar["results"][0]["observations"]
        )
        assert c.get(path, headers=AUTH).content == r.content  # reproducible
    finally:
        remove_flow(sid)


def test_export_404_without_working_papers(demo_client):
    sid = str(uuid.uuid4())
    flow = AuditFlow(initial_status="COMPLETED")
    flow.state.final_report = _rich_report()
    session_manager.save_session(sid, "x", "ctx")
    FlowRepository().save(sid, flow)
    set_flow(sid, flow)
    try:
        r = demo_client.get(f"/api/sessions/{sid}/export/oscal.json", headers=AUTH)
        assert r.status_code == 404
        assert "Working papers" in r.json()["detail"]
    finally:
        remove_flow(sid)


def test_committed_sample_validates(validator):
    sample = Path(__file__).parent.parent / "docs" / "sample-run" / "oscal.json"
    doc = json.loads(sample.read_text(encoding="utf-8"))
    assert_valid(validator, doc)
    assert _ar(doc)["metadata"]["oscal-version"] == OSCAL_VERSION


# ── Reviewer decisions (ADR-011) ─────────────────────────────────────────────


def _props_of(element) -> dict:
    return {p["name"]: p["value"] for p in element.get("props", [])}


def test_reviewer_decisions_render_as_conclusion_of_record(demo_client, validator):
    c = demo_client
    r = c.post(
        "/api/sessions",
        headers=AUTH,
        json={
            "theme": "S3 exposure",
            "business_context": "Fintech",
            "prepared_by": "Pat",
        },
    )
    sid = r.json()["session_id"]
    try:
        for gate, who in ((1, "alice"), (2, "bob"), (3, "carol")):
            if gate > 1:
                record_demo_decisions(c, sid, f"gate{gate}", who, AUTH)
            r = c.patch(
                f"/api/sessions/{sid}/approve",
                headers=AUTH,
                json={"gate_number": gate, "human_id": who},
            )
            assert r.status_code == 200, r.text
        record_demo_decisions(c, sid, "after_issue", "Pat", AUTH)
        doc = c.get(f"/api/sessions/{sid}/export/oscal.json", headers=AUTH).json()
        assert_valid(validator, doc)
        ar = _ar(doc)
        result = ar["results"][0]

        [risk] = result["risks"]
        props = _props_of(risk)
        assert props["classification"] == "High"
        assert props["classification-source"] == "reviewer"
        assert props["classification-state"] == "reviewer-decision"
        assert props["ai-draft-classification"] == "Medium"
        assert props["ai-draft-magnitude"] == "Medium"
        assert props["decided-by"] == "carol"
        assert props["identity-source"] == "declared"
        assert "AI draft: Medium" in risk["description"]
        assert "Criteria:" in risk["description"]
        lifecycles = [x["lifecycle"] for x in risk["remediations"]]
        assert lifecycles == ["recommendation", "planned"]
        assert risk["deadline"].startswith("2026-12-31")
        log = risk["risk-log"]["entries"][0]
        assert "transcribed by Pat" in log["description"]
        assert any(a["type"] == "party" for o in risk["origins"] for a in o["actors"])

        by_control = {_props_of(o)["control-id"]: o for o in result["observations"]}
        assert _props_of(by_control["CTRL-02"])["reviewer-sign-off"] == "signed-off"
        assert _props_of(by_control["CTRL-02"])["reviewed-by"] == "bob"
        assert _props_of(by_control["CTRL-03"])["scope-limitation"] == "true"
        for finding in result["findings"]:
            assert _props_of(finding)["reviewer-sign-off"] == "signed-off"

        [attestation] = result["attestations"]
        assert attestation["parts"][0]["name"] == "engagement-conclusion"
        assert "Needs improvement" in attestation["parts"][0]["title"]
        assert _props_of(result)["engagement-conclusion"] == "Needs improvement"

        decision_logs = [
            e
            for e in result["assessment-log"]["entries"]
            if _props_of(e).get("trail-action") == "review_decision"
        ]
        assert decision_logs and all(
            "decision-digest" in _props_of(e) for e in decision_logs
        )
        roles = {r["id"] for r in ar["metadata"]["roles"]}
        assert "reviewer" in roles
    finally:
        remove_flow(sid)


def test_withdrawn_conclusion_and_disagreement(monkeypatch, validator):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DEMO_STEP_DELAY", "0")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEMO_QA_REJECT_PHASE", raising=False)
    flow = AuditFlow()
    flow.state.theme = "S3 exposure"
    flow.record_preparer("Pat", require_review_decisions=True)
    flow.begin_phase_1()
    flow.generate_planning()
    flow.begin_phase_2("alice")
    flow.generate_fieldwork()
    flow.record_decision(
        decision_type="challenge",
        subject_id="CTRL-01",
        decided_by="bob",
        values={"tod_conclusion": "Not tested"},
        rationale="The read predates the period.",
    )
    for control in ("CTRL-02", "CTRL-03"):
        flow.record_decision(
            decision_type="sign_off", subject_id=control, decided_by="bob"
        )
    flow.begin_phase_3("bob")
    flow.generate_reporting()
    flow.record_decision(
        decision_type="management_response",
        subject_id="DEF-01",
        decided_by="Pat",
        values={
            "text": "The bucket is meant to be public.",
            "agreement": "disagree",
            "received_from": "CISO",
            "received_on": "2026-10-01",
        },
        rationale="It holds customer exports.",
    )
    state = flow.state
    assert state.final_report is not None and state.working_papers is not None
    doc = json.loads(
        oscal_json(
            state.final_report,
            state.working_papers,
            state.racm_plan,
            state.approval_trail,
            ExportContext(SID, "Demo", state.status),
            theme=state.theme,
            prepared_by=state.prepared_by,
            view=flow.effective_view(),
            decisions=state.review_decisions,
        )
    )
    assert_valid(validator, doc)
    result = _ar(doc)["results"][0]
    obs = {_props_of(o)["control-id"]: o for o in result["observations"]}
    ctrl1 = _props_of(obs["CTRL-01"])
    assert ctrl1["tod-conclusion"] == "Not tested"
    assert ctrl1["ai-draft-tod-conclusion"] == "Effective"
    assert ctrl1["reviewer-sign-off"] == "challenged"
    assert "Reviewer challenge by bob" in obs["CTRL-01"]["remarks"]
    # Withdrawn to "Not tested": no satisfied finding is claimed for CTRL-01.
    assert [f["target"]["target-id"] for f in result["findings"]] == ["CTRL-02"]
    [risk] = result["risks"]
    assert _props_of(risk)["classification-source"] == "ai-draft"
    assert "remediations" not in risk  # no action plan when management disagrees
    assert (
        "Auditor's rebuttal: It holds customer exports."
        in (risk["risk-log"]["entries"][0]["description"])
    )
    assert "attestations" not in result
