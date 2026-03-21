from dataclasses import dataclass
from typing import Any, TypeAlias

from langchain_community.tools import DuckDuckGoSearchRun

from swarm.llm_factory import get_llm


@dataclass(frozen=True)
class MockLLMAdapter:
    reason: str
    llm: Any | None = None
    is_live: bool = False


@dataclass(frozen=True)
class LiveLLMAdapter:
    llm: Any
    is_live: bool = True


@dataclass(frozen=True)
class MockSearchAdapter:
    reason: str
    llm: Any | None = None
    search_tool: Any | None = None
    is_live: bool = False


@dataclass(frozen=True)
class LiveSearchAdapter:
    llm: Any
    search_tool: Any
    is_live: bool = True


LLMAdapter: TypeAlias = MockLLMAdapter | LiveLLMAdapter
SearchAdapter: TypeAlias = MockSearchAdapter | LiveSearchAdapter


def build_llm_adapter(
    temperature: float,
    prefer_fast: bool = False,
) -> LLMAdapter:
    llm = get_llm(temperature=temperature, prefer_fast=prefer_fast)
    if llm is None:
        return MockLLMAdapter("No LLM available.")
    return LiveLLMAdapter(llm=llm)


def build_search_adapter(
    temperature: float,
    prefer_fast: bool = False,
) -> SearchAdapter:
    llm_adapter = build_llm_adapter(temperature=temperature, prefer_fast=prefer_fast)
    if not llm_adapter.is_live:
        return MockSearchAdapter(reason="No LLM available.")

    try:
        search_tool = DuckDuckGoSearchRun()
    except Exception as exc:
        return MockSearchAdapter(reason=f"Search tool setup failed: {exc}")

    return LiveSearchAdapter(llm=llm_adapter.llm, search_tool=search_tool)
