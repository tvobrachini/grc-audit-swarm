"""
Tests for LLM factory priority order.
Ensures Gemini > OpenAI > Groq selection when multiple keys are set.
"""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(autouse=True)
def clear_api_keys(monkeypatch):
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def _call_factory(monkeypatch_env: dict, temperature: float = 0.1):
    """Set env vars and call get_crew_llm; return the model string passed to LLM."""
    import swarm.llm_factory as factory

    captured = {}

    class FakeLLM:
        def __init__(self, model, temperature):
            captured["model"] = model
            captured["temperature"] = temperature

    with patch.object(factory, "LLM", FakeLLM):
        factory.get_crew_llm(temperature=temperature)

    return captured


class TestLlmFactoryPriority:
    def test_gemini_preferred_over_openai_and_groq(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        monkeypatch.setenv("GROQ_API_KEY", "groq-key")
        result = _call_factory({})
        assert result["model"].startswith("gemini/")

    def test_openai_preferred_over_groq_when_no_gemini(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        monkeypatch.setenv("GROQ_API_KEY", "groq-key")
        result = _call_factory({})
        assert result["model"].startswith("openai/")

    def test_groq_used_as_last_resort(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "groq-key")
        result = _call_factory({})
        assert result["model"].startswith("groq/")

    def test_no_keys_returns_a_model(self):
        result = _call_factory({})
        assert "model" in result

    def test_temperature_passed_through(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        result = _call_factory({}, temperature=0.0)
        assert result["temperature"] == 0.0

    def test_gemini_not_selected_without_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        result = _call_factory({})
        assert not result["model"].startswith("gemini/")
