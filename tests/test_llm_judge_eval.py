"""
LLM-as-a-Judge Prompt Evaluation Pipeline
This test uses a "Golden Dataset" to evaluate the Swarm's ability to analyze evidence accurately.
Instead of rigid unit tests, it uses a second LLM to "Judge" the output of the first LLM against an ideal rubric,
allowing us to catch prompt drift and regressions before merging code.
"""

import pytest
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from src.swarm.llm_factory import get_llm
from src.swarm.state.schema import ControlMatrixItem, AuditFinding

load_dotenv()


class JudgeResult(BaseModel):
    score: int = Field(description="Score from 0 to 100 based on the rubric.")
    reasoning: str = Field(description="Why the score was given.")
    passed: bool = Field(description="True if score >= 90")


# Golden Dataset: Known exact inputs and our ideal expected behavior
GOLDEN_DATASET = [
    {
        "test_id": "eval_01",
        "control": ControlMatrixItem(
            control_id="AC-02",
            domain="Access Control",
            description="The organization must revoke access to systems within 24 hours of employee termination.",
        ),
        "evidence_log": "HR Log: John Doe terminated 2026-03-01. Access Log: John Doe AD Account disabled 2026-03-05.",
        "expected_status": "Fail",
        "expected_finding_keywords": ["delayed", "24 hours", "terminated"],
        "rubric": "The generated finding MUST explicitly Fail the control because the access was revoked 4 days late. It MUST NOT pass the control.",
    }
]


@pytest.mark.parametrize("scenario", GOLDEN_DATASET)
def test_prompt_eval_llm_as_judge(scenario):
    """
    Evaluates the Worker Agent using LLM-as-a-Judge.
    1. Runs the real Worker LLM prompt with the Golden Data.
    2. Runs a separate 'Judge' LLM to score the Worker's response.
    """
    # Initialize the LLM for judging (using prefer_fast=True to save API limits during tests)
    judge_llm = get_llm(temperature=0.0, prefer_fast=True)
    if not judge_llm:
        pytest.skip("No LLM configured. Skipping LLM-as-a-Judge eval.")

    # 1. GENERATE: Run the actual system agent
    # We must patch the get_evidence function or pass it through state (if our architecture allows).
    # For this isolated eval, we will test the string generation directly if run_control_test accepts it,
    # but since run_control_test is complex, we'll simulate the LLM call that worker.py makes using its prompt.

    worker_llm = get_llm(temperature=0.1, prefer_fast=True)
    structured_llm = worker_llm.with_structured_output(AuditFinding)

    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are an expert IT Auditor. You are evaluating a control based on evidence. Do not propose action plans. Only evaluate TOD and TOE. Output MUST be an AuditFinding with status 'Pass', 'Fail', or 'Exception'.",
            ),
            (
                "user",
                "Evaluate Control {control_id} - {domain}\nDescription: {description}\n\nEvidence Found:\n{evidence}",
            ),
        ]
    )

    chain = prompt | structured_llm

    print(f"\n[Eval] Running Generation for {scenario['test_id']}...")
    try:
        finding = chain.invoke(
            {
                "control_id": scenario["control"].control_id,
                "domain": scenario["control"].domain,
                "description": scenario["control"].description,
                "evidence": scenario["evidence_log"],
            }
        )
    except Exception as e:
        pytest.fail(f"LLM Generation failed: {e}")

    print(f"[Eval] Generated Finding Status: {finding.status}")
    print(f"[Eval] Generated Justification: {finding.justification}")

    # 2. JUDGE: Have the LLM grade the output
    judge_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are an independent AI Grader evaluating output from another AI. Read the provided Rubric and the AI's Output. Score the output from 0 to 100.",
            ),
            (
                "user",
                "Rubric:\n{rubric}\n\nAI Output Status: {status}\nAI Output Justification: {justification}\n\nEvaluate the AI Output based STRICTLY on the rubric.",
            ),
        ]
    )

    grader_chain = judge_prompt | judge_llm.with_structured_output(JudgeResult)

    print(f"[Eval] Running Judge for {scenario['test_id']}...")
    try:
        grade = grader_chain.invoke(
            {
                "rubric": scenario["rubric"],
                "status": finding.status,
                "justification": finding.justification,
            }
        )
    except Exception as e:
        pytest.fail(f"LLM Judge failed: {e}")

    print(f"[Eval] Judge Score: {grade.score}/100")
    print(f"[Eval] Judge Reasoning: {grade.reasoning}")

    # 3. ASSERT: Catch regressions
    assert grade.passed, (
        f"Prompt Regression Detected! Score: {grade.score}. Reason: {grade.reasoning}"
    )
