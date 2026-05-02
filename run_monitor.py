"""
Monitored headless run of the GRC Audit Swarm.
Exercises AuditFlow phases 1→2→3 directly (no Streamlit UI).
All CrewAI, LLM, and tool output is captured and printed with phase labels.

Usage:
    python run_monitor.py [--phase1-only] [--skip-aws]

Flags:
    --phase1-only   Run only Planning crew, then stop
    --skip-aws      Inject mock working papers instead of running real Fieldwork crew
"""

import sys
import os
import logging
import argparse
import traceback
from datetime import datetime

# ── env bootstrap ──────────────────────────────────────────────────────────────
from dotenv import load_dotenv

load_dotenv(override=True)
# google-genai SDK reads GEMINI_API_KEY directly; no remapping needed
os.environ["DEMO_MODE"] = "0"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

# ── logging: print everything to stdout with timestamps ───────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("monitor")


# ── imports ────────────────────────────────────────────────────────────────────
def section(title: str):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")


def check_env():
    section("ENV CHECK")
    
    def is_set(k):
        if k == "AWS_REGION":
            return bool(os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"))
        return bool(os.environ.get(k))

    keys_to_check = [
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
    ]
    
    for k in keys_to_check:
        status = "✅ SET" if is_set(k) else "❌ MISSING"
        print(f"  {status}  {k}")
        
    if not any([is_set("GEMINI_API_KEY"), is_set("GROQ_API_KEY"), is_set("OPENAI_API_KEY")]):
        print("\n  FATAL: No LLM API key found. Aborting.")
        sys.exit(1)
    print()


def check_imports():
    section("IMPORT CHECK")
    modules = [
        ("swarm.audit_flow", "AuditFlow"),
        ("swarm.llm_factory", "get_crew_llm"),
        ("swarm.state.schema", "AuditState"),
        ("swarm.schema", "RiskControlMatrixSchema"),
        ("swarm.evidence", "EvidenceAssuranceProtocol"),
        ("swarm.crews.planning_crew", "PlanningCrew"),
        ("swarm.crews.fieldwork_crew", "FieldworkCrew"),
        ("swarm.crews.reporting_crew", "ReportingCrew"),
    ]
    all_ok = True
    for module, attr in modules:
        try:
            mod = __import__(module, fromlist=[attr])
            getattr(mod, attr)
            print(f"  ✅  {module}.{attr}")
        except Exception as e:
            print(f"  ❌  {module}.{attr}  →  {e}")
            all_ok = False
    if not all_ok:
        print("\n  FATAL: Import errors detected. Fix before running crews.")
        sys.exit(1)
    print()


def run_phase1(flow) -> bool:
    section("PHASE 1 — PLANNING CREW")
    print(f"  Theme:   {flow.state.theme}")
    print(f"  Context: {flow.state.business_context}")
    print(f"  Frameworks: {flow.state.frameworks}\n")
    t0 = datetime.utcnow()
    try:
        flow.generate_planning()
    except Exception:
        print("\n  ❌ UNHANDLED EXCEPTION in generate_planning():")
        traceback.print_exc()
        return False

    elapsed = (datetime.utcnow() - t0).total_seconds()
    status = flow.state.status
    print(f"\n  Status after Phase 1: {status}  ({elapsed:.1f}s)")

    if status == "ERROR":
        print(f"  ❌ ERROR: {flow.state.qa_rejection_reason}")
        return False
    if status == "QA_REJECTED_PHASE_1":
        print(f"  ❌ QA REJECTED (after retry): {flow.state.qa_rejection_reason}")
        return False

    racm = flow.state.racm_plan or {}
    risks = racm.get("risks", [])
    print(f"\n  ✅ RACM generated — theme: '{racm.get('theme')}', risks: {len(risks)}")
    for r in risks:
        ctrls = r.get("controls", [])
        print(
            f"     Risk {r.get('risk_id')}: {len(ctrls)} control(s)  — {r.get('description', '')[:60]}"
        )
    return True


def run_phase2(flow, skip_aws: bool) -> bool:
    section("PHASE 2 — FIELDWORK CREW")
    if skip_aws:
        print(
            "  [--skip-aws] Injecting mock working papers, skipping real AWS tools.\n"
        )
        from swarm.schema import WorkingPaperSchema, AuditFindingSchema
        from datetime import datetime as dt

        flow.state.working_papers = WorkingPaperSchema(
            theme=flow.state.theme,
            findings=[
                AuditFindingSchema(
                    control_id="CTRL-01",
                    vault_id_reference="mock-0000-0000-0000",
                    exact_quote_from_evidence="MinimumPasswordLength: 14",
                    test_conclusion="IAM password policy meets CIS baseline.",
                    severity="Pass",
                )
            ],
        ).model_dump()
        flow.state.approval_trail.append(
            {
                "gate": "Gate 1 (Planning)",
                "human": "MONITOR_RUNNER",
                "timestamp": dt.utcnow().isoformat(),
            }
        )
        flow.state.status = "WAITING_HUMAN_GATE_2"
        print("  ✅ Mock working papers injected.")
        return True

    t0 = datetime.utcnow()
    try:
        flow.generate_fieldwork(human_id="MONITOR_RUNNER")
    except Exception:
        print("\n  ❌ UNHANDLED EXCEPTION in generate_fieldwork():")
        traceback.print_exc()
        return False

    elapsed = (datetime.utcnow() - t0).total_seconds()
    status = flow.state.status
    print(f"\n  Status after Phase 2: {status}  ({elapsed:.1f}s)")

    if status == "ERROR":
        print(f"  ❌ ERROR: {flow.state.qa_rejection_reason}")
        return False
    if status == "QA_REJECTED_PHASE_2":
        print(f"  ❌ QA REJECTED (after retry): {flow.state.qa_rejection_reason}")
        return False

    papers = flow.state.working_papers or {}
    findings = papers.get("findings", [])
    print(f"\n  ✅ Working Papers generated — {len(findings)} finding(s)")
    for f in findings:
        print(
            f"     {f.get('control_id')}  [{f.get('severity')}]  vault={f.get('vault_id_reference', '')[:16]}…"
        )
        quote = f.get("exact_quote_from_evidence", "")
        print(f'       Quote: "{quote[:80]}{"…" if len(quote) > 80 else ""}"')

    # Vault verification
    from swarm.evidence import EvidenceAssuranceProtocol

    print("\n  VAULT VERIFICATION:")
    for f in findings:
        vid = f.get("vault_id_reference", "")
        quote = f.get("exact_quote_from_evidence", "")
        if vid and quote:
            verified = EvidenceAssuranceProtocol.verify_exact_quote(vid, quote)
            icon = "✅" if verified else "❌ HALLUCINATION DETECTED"
            print(f"     {icon}  {f.get('control_id')}  vault={vid[:16]}…")
        else:
            print(f"     ⚠️  {f.get('control_id')}  missing vault_id or quote")

    return True


def run_phase3(flow) -> bool:
    section("PHASE 3 — REPORTING CREW")
    t0 = datetime.utcnow()
    try:
        flow.generate_reporting(human_id="MONITOR_RUNNER")
    except Exception:
        print("\n  ❌ UNHANDLED EXCEPTION in generate_reporting():")
        traceback.print_exc()
        return False

    elapsed = (datetime.utcnow() - t0).total_seconds()
    status = flow.state.status
    print(f"\n  Status after Phase 3: {status}  ({elapsed:.1f}s)")

    if status == "ERROR":
        print(f"  ❌ ERROR: {flow.state.qa_rejection_reason}")
        return False
    if status == "QA_REJECTED_PHASE_3":
        print(f"  ❌ QA REJECTED: {flow.state.qa_rejection_reason}")
        return False

    rep = flow.state.final_report or {}
    print("\n  ✅ Final Report generated")
    print(f"\n  EXECUTIVE SUMMARY:\n  {rep.get('executive_summary', '(empty)')[:300]}")
    print(f"\n  TONE APPROVED: {rep.get('compliance_tone_approved')}")
    return True


def print_summary(flow, results: dict):
    section("RUN SUMMARY")
    for phase, ok in results.items():
        icon = "✅" if ok else "❌"
        print(f"  {icon}  {phase}")
    print(f"\n  Final status : {flow.state.status}")
    print("  Approval trail:")
    for entry in flow.state.approval_trail:
        print(
            f"    {entry.get('gate')}  —  {entry.get('human')}  @  {entry.get('timestamp')}"
        )
    evidence_dir = os.path.join(os.path.dirname(__file__), "evidence_vault")
    vault_files = len(os.listdir(evidence_dir)) if os.path.exists(evidence_dir) else 0
    print(f"\n  Evidence vault files: {vault_files}")
    print()


# ── main ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase1-only", action="store_true", help="Stop after Phase 1")
    parser.add_argument(
        "--skip-aws", action="store_true", help="Mock Phase 2 AWS tools"
    )
    args = parser.parse_args()

    check_env()
    check_imports()

    from swarm.audit_flow import AuditFlow

    flow = AuditFlow()
    flow.state.theme = "Public S3 Buckets Exposure"
    flow.state.business_context = (
        "We are a fintech handling sensitive customer financial data. "
        "Ensure no S3 buckets are publicly accessible and IAM password policy is enforced."
    )
    flow.state.frameworks = ["CIS AWS Foundations Benchmark"]

    results = {}

    ok1 = run_phase1(flow)
    results["Phase 1 — Planning"] = ok1
    if not ok1 or args.phase1_only:
        print_summary(flow, results)
        sys.exit(0 if ok1 else 1)

    ok2 = run_phase2(flow, skip_aws=args.skip_aws)
    results["Phase 2 — Fieldwork"] = ok2
    if not ok2:
        print_summary(flow, results)
        sys.exit(1)

    ok3 = run_phase3(flow)
    results["Phase 3 — Reporting"] = ok3

    print_summary(flow, results)
    sys.exit(0 if all(results.values()) else 1)
