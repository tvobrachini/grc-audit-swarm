"""Runtime tests for direct httpx ZeroFrameworkLLMGateway, _compat Crew, and PlanningCrew context chaining."""

from __future__ import annotations

import json
import os
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from flow_builders import make_racm  # type: ignore[import-not-found]

from swarm.crews._compat import LLM, Agent, Crew, Process, Task
from swarm.crews.planning_crew import PlanningCrew
from swarm.llm_gateway import ZeroFrameworkLLMGateway
from swarm.model_routing import EffortLevel, ModelFamily, ModelSpec
from swarm.schema import QA_PushbackSchema, RiskControlMatrixSchema


def _mock_httpx_response(payload: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


class TestDirectHttpxGateway:
    def test_openai_compatible_completion_strips_prefix_and_records_usage(
        self, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai")
        captured: dict[str, Any] = {}

        def fake_post(
            url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float
        ):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            captured["timeout"] = timeout
            return _mock_httpx_response(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '```json\n{"approved": true, "rejection_reason": null}\n```'
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 35},
                }
            )

        monkeypatch.setattr("swarm.llm_gateway.httpx.post", fake_post)
        gateway = ZeroFrameworkLLMGateway()
        spec = ModelSpec("openai/gpt-4o-mini", ModelFamily.OPENAI, EffortLevel.LOW)

        out = gateway.invoke_structured(
            phase=1,
            role="qa_reviewer",
            spec=spec,
            system_prompt="Review carefully.",
            user_prompt="Check artifact.",
            response_model=QA_PushbackSchema,
            timeout=30.0,
        )

        assert out.approved is True
        assert captured["url"] == "https://api.openai.com/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer sk-env-openai"
        assert captured["json"]["model"] == "gpt-4o-mini"
        assert captured["json"]["response_format"] == {"type": "json_object"}
        assert captured["timeout"] == 30.0
        assert len(gateway.receipts) == 1
        assert gateway.receipts[0].input_tokens == 120
        assert gateway.receipts[0].output_tokens == 35

    @pytest.mark.parametrize(
        ("model_id", "env_var", "expected_url", "expected_model"),
        [
            (
                "gemini/gemini-2.5-flash",
                "GEMINI_API_KEY",
                "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                "gemini-2.5-flash",
            ),
            (
                "openrouter/meta-llama/llama-3.1-8b-instruct",
                "OPENROUTER_API_KEY",
                "https://openrouter.ai/api/v1/chat/completions",
                "meta-llama/llama-3.1-8b-instruct",
            ),
            (
                "nvidia_nim/meta/llama-3.3-70b-instruct",
                "NVIDIA_NIM_API_KEY",
                "https://integrate.api.nvidia.com/v1/chat/completions",
                "meta/llama-3.3-70b-instruct",
            ),
            (
                "ollama/qwen2.5",
                None,
                "http://localhost:11434/v1/chat/completions",
                "qwen2.5",
            ),
        ],
    )
    def test_provider_routing_and_prefix_stripping(
        self,
        monkeypatch,
        model_id: str,
        env_var: str | None,
        expected_url: str,
        expected_model: str,
    ):
        if env_var:
            monkeypatch.setenv(env_var, f"key-for-{env_var}")
        captured: dict[str, Any] = {}

        def fake_post(
            url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float
        ):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _mock_httpx_response(
                {
                    "choices": [{"message": {"content": '{"approved": true}'}}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 10},
                }
            )

        monkeypatch.setattr("swarm.llm_gateway.httpx.post", fake_post)
        gateway = ZeroFrameworkLLMGateway()
        spec = ModelSpec(model_id, ModelFamily.OTHER, EffortLevel.LOW)

        out = gateway.invoke_structured(
            phase=2,
            role="critic",
            spec=spec,
            system_prompt="Sys",
            user_prompt="Usr",
            response_model=QA_PushbackSchema,
        )

        assert out.approved is True
        assert captured["url"] == expected_url
        assert captured["json"]["model"] == expected_model
        if env_var:
            assert captured["headers"]["Authorization"] == f"Bearer key-for-{env_var}"
        else:
            assert "Authorization" not in captured["headers"]

    def test_anthropic_messages_endpoint_and_usage_extraction(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        captured: dict[str, Any] = {}

        def fake_post(
            url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float
        ):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _mock_httpx_response(
                {
                    "content": [
                        {
                            "type": "text",
                            "text": '{"approved": false, "rejection_reason": "Missing IPE."}',
                        }
                    ],
                    "usage": {"input_tokens": 210, "output_tokens": 45},
                }
            )

        monkeypatch.setattr("swarm.llm_gateway.httpx.post", fake_post)
        gateway = ZeroFrameworkLLMGateway()
        spec = ModelSpec(
            "anthropic/claude-3-5-haiku-latest", ModelFamily.ANTHROPIC, EffortLevel.LOW
        )

        out = gateway.invoke_structured(
            phase=1,
            role="qa",
            spec=spec,
            system_prompt="System prompt",
            user_prompt="User prompt",
            response_model=QA_PushbackSchema,
        )

        assert out.approved is False
        assert out.rejection_reason == "Missing IPE."
        assert captured["url"] == "https://api.anthropic.com/v1/messages"
        assert captured["headers"]["x-api-key"] == "sk-ant-test"
        assert captured["headers"]["anthropic-version"] == "2023-06-01"
        assert captured["json"]["model"] == "claude-3-5-haiku-latest"
        assert gateway.receipts[0].input_tokens == 210
        assert gateway.receipts[0].output_tokens == 45


class TestCompatCredentialForwardingAndTokenUsage:
    def test_crew_kickoff_forwards_llm_credentials_and_aggregates_token_usage(
        self, monkeypatch
    ):
        calls: list[dict[str, Any]] = []
        responses = [
            _mock_httpx_response(
                {
                    "choices": [
                        {"message": {"content": json.dumps({"content": "Memo body"})}}
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 40},
                }
            ),
            _mock_httpx_response(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"approved": True, "rejection_reason": None}
                                )
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 250, "completion_tokens": 60},
                }
            ),
        ]

        def fake_post(
            url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float
        ):
            calls.append(
                {"url": url, "headers": headers, "json": json, "timeout": timeout}
            )
            return responses[len(calls) - 1]

        monkeypatch.setattr("swarm.llm_gateway.httpx.post", fake_post)

        llm = LLM(
            model="nvidia_nim/meta/llama-3.3-70b-instruct",
            temperature=0.05,
            api_key="explicit-nim-key",  # pragma: allowlist secret
            base_url="https://custom-nim.example/v1",
            timeout=45.0,
        )
        agent = Agent(role="Auditor", goal="Test", backstory="Experienced", llm=llm)
        t1 = Task(
            description="Draft memo", expected_output="Text", name="memo", agent=agent
        )
        t2 = Task(
            description="Review memo",
            expected_output="Verdict",
            name="review",
            agent=agent,
            output_pydantic=QA_PushbackSchema,
        )

        crew = Crew(agents=[agent], tasks=[t1, t2], process=Process.sequential)
        out = crew.kickoff()

        assert len(calls) == 2
        for call in calls:
            assert call["url"] == "https://custom-nim.example/v1/chat/completions"
            assert call["headers"]["Authorization"] == "Bearer explicit-nim-key"
            assert call["json"]["model"] == "meta/llama-3.3-70b-instruct"
            assert call["timeout"] == 45.0

        assert out.token_usage == {
            "prompt_tokens": 350,
            "completion_tokens": 100,
            "total_tokens": 450,
        }
        # Also verify eval harness compatibility (.model_dump())
        assert getattr(out.token_usage, "model_dump")() == {
            "prompt_tokens": 350,
            "completion_tokens": 100,
            "total_tokens": 450,
        }


class TestTaskContextChainingAndPlanningCrew:
    def test_context_none_chains_previous_task_while_empty_list_omits_context(self):
        seen_prompts: dict[str, str] = {}

        def fake_completion(*, spec, system_prompt, user_prompt, response_model):
            if user_prompt.startswith("STEP_1"):
                seen_prompts["t1"] = user_prompt
                return {"content": "OUTPUT_FROM_STEP_1"}
            if user_prompt.startswith("STEP_2"):
                seen_prompts["t2"] = user_prompt
                return {"content": "OUTPUT_FROM_STEP_2"}
            seen_prompts["t3"] = user_prompt
            return {"content": "OUTPUT_FROM_STEP_3"}

        llm = LLM(model="openai/gpt-4o-mini")
        agent = Agent(role="Worker", goal="Work", backstory="None", llm=llm)
        t1 = Task(description="STEP_1", expected_output="1", name="t1", agent=agent)
        t2 = Task(
            description="STEP_2",
            expected_output="2",
            name="t2",
            agent=agent,
            context=None,
        )
        t3 = Task(
            description="STEP_3",
            expected_output="3",
            name="t3",
            agent=agent,
            context=[],
        )

        assert t1.context is None
        assert t2.context is None
        assert t3.context == []

        crew = Crew(
            agents=[agent],
            tasks=[t1, t2, t3],
            gateway=ZeroFrameworkLLMGateway(completion_fn=fake_completion),
        )
        crew.kickoff()

        assert "=== Context from" not in seen_prompts["t1"]
        assert "=== Context from t1 ===" in seen_prompts["t2"]
        assert "OUTPUT_FROM_STEP_1" in seen_prompts["t2"]
        assert "=== Context from" not in seen_prompts["t3"]
        assert "OUTPUT_FROM_STEP_2" not in seen_prompts["t3"]

    def test_planning_crew_chains_context_and_crosswalk_into_weighting(
        self, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        crew = PlanningCrew("test-session").crew()
        by_name = {t.name: t for t in crew.tasks}

        assert by_name["crosswalk_task"].context == [by_name["context_task"]]
        assert by_name["weighting_task"].context == [
            by_name["context_task"],
            by_name["crosswalk_task"],
        ]

        prompts_by_role: dict[str, str] = {}

        def fake_completion(*, spec, system_prompt, user_prompt, response_model):
            if response_model is RiskControlMatrixSchema:
                prompts_by_role["racm"] = user_prompt
                return make_racm()
            if response_model is QA_PushbackSchema:
                prompts_by_role["qa"] = user_prompt
                return QA_PushbackSchema(approved=True)
            if "context_task" not in prompts_by_role:
                prompts_by_role["context_task"] = user_prompt
                return {"content": "SCOPE_MEMO_PAYLOAD"}
            if "crosswalk_task" not in prompts_by_role:
                prompts_by_role["crosswalk_task"] = user_prompt
                return {"content": "CROSSWALK_TABLE_PAYLOAD"}
            prompts_by_role["weighting_task"] = user_prompt
            return {"content": "RANKED_RISKS_PAYLOAD"}

        crew.gateway = ZeroFrameworkLLMGateway(completion_fn=fake_completion)
        crew.kickoff(
            inputs={
                "theme": "AWS IAM",
                "business_context": "Fintech",
                "frameworks": "CIS AWS",
                "qa_feedback": "",
            }
        )

        assert "SCOPE_MEMO_PAYLOAD" in prompts_by_role["crosswalk_task"]
        assert "SCOPE_MEMO_PAYLOAD" in prompts_by_role["weighting_task"]
        assert "CROSSWALK_TABLE_PAYLOAD" in prompts_by_role["weighting_task"]
        assert "RANKED_RISKS_PAYLOAD" in prompts_by_role["racm"]
