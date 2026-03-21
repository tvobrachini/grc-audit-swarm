import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from swarm.runtime_adapters import (
    LiveLLMAdapter,
    LiveSearchAdapter,
    MockLLMAdapter,
    MockSearchAdapter,
    build_llm_adapter,
    build_search_adapter,
)


def test_build_llm_adapter_returns_mock_when_llm_missing():
    with patch("swarm.runtime_adapters.get_llm", return_value=None):
        adapter = build_llm_adapter(temperature=0.1)

    assert isinstance(adapter, MockLLMAdapter)
    assert adapter.is_live is False


def test_build_llm_adapter_returns_live_when_llm_available():
    with patch("swarm.runtime_adapters.get_llm", return_value=object()):
        adapter = build_llm_adapter(temperature=0.1)

    assert isinstance(adapter, LiveLLMAdapter)
    assert adapter.is_live is True


def test_build_search_adapter_returns_mock_when_search_setup_fails():
    with (
        patch("swarm.runtime_adapters.get_llm", return_value=object()),
        patch(
            "swarm.runtime_adapters.DuckDuckGoSearchRun",
            side_effect=RuntimeError("offline"),
        ),
    ):
        adapter = build_search_adapter(temperature=0.1)

    assert isinstance(adapter, MockSearchAdapter)
    assert adapter.is_live is False


def test_build_search_adapter_returns_live_when_dependencies_available():
    with (
        patch("swarm.runtime_adapters.get_llm", return_value=object()),
        patch(
            "swarm.runtime_adapters.DuckDuckGoSearchRun",
            return_value=object(),
        ),
    ):
        adapter = build_search_adapter(temperature=0.1)

    assert isinstance(adapter, LiveSearchAdapter)
    assert adapter.is_live is True
