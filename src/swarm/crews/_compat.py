"""
Zero-Framework Execution Runtime & Compatibility Layer (`_compat.py`).

Provides lightweight, deterministic Python implementations of ``LLM``, ``Agent``,
``Task``, ``Crew``, ``Process``, ``tool``, and ``interpolate_only`` so the entire
Audit Factory runs with **zero dependency on CrewAI** while preserving 100%
interface compatibility with existing unit tests and phase adapters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Optional, Sequence

from pydantic import BaseModel

from swarm.llm_gateway import ZeroFrameworkLLMGateway
from swarm.model_routing import (
    EffortLevel,
    ModelSpec,
    detect_model_family,
)

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_\-]*)\}")


def interpolate_only(input_string: Optional[str], inputs: dict[str, Any]) -> str:
    """Interpolate ``{key}`` placeholders from ``inputs`` without touching nested braces in values."""
    if input_string is None:
        return ""
    if not inputs:
        return input_string

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in inputs:
            raise KeyError(f"Template variable '{key}' not found in inputs dictionary")
        val = inputs[key]
        return "" if val is None else str(val)

    return _PLACEHOLDER_RE.sub(_replace, input_string)


class NativeTool:
    """Zero-framework tool wrapper preserving `.run()`, `.name`, and `.description`."""

    def __init__(self, name: str, fn: Callable[..., Any]):
        self.name = name
        self.description = (fn.__doc__ or "").strip()
        self.func = fn

    def run(self, *args: Any, **kwargs: Any) -> Any:
        return self.func(*args, **kwargs)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.func(*args, **kwargs)


def tool(name: str) -> Callable[[Callable[..., Any]], NativeTool]:
    def _decorator(fn: Callable[..., Any]) -> NativeTool:
        return NativeTool(name, fn)

    return _decorator


native_tool = tool


class LLM:
    """Zero-framework LLM configuration holder compatible with ``llm_factory.py``."""

    def __init__(
        self,
        model: str,
        temperature: float = 0.1,
        **kwargs: Any,
    ):
        self.model = model
        self.temperature = temperature
        self.kwargs = dict(kwargs)
        for k, v in kwargs.items():
            setattr(self, k, v)

    def to_model_spec(self, effort: EffortLevel = EffortLevel.MEDIUM) -> ModelSpec:
        return ModelSpec(
            model_id=self.model,
            family=detect_model_family(self.model),
            effort=effort,
            temperature=self.temperature,
        )


NativeLLM = LLM


class Agent:
    """Zero-framework Agent configuration holder."""

    def __init__(
        self,
        role: str = "",
        goal: str = "",
        backstory: str = "",
        *,
        llm: Any = None,
        tools: Optional[Sequence[Any]] = None,
        verbose: bool = False,
        max_iter: int = 5,
        **kwargs: Any,
    ):
        self.role = role.strip()
        self.goal = goal.strip()
        self.backstory = backstory.strip()
        self.llm = llm
        self.tools = list(tools or [])
        self.verbose = verbose
        self.max_iter = max_iter
        self.kwargs = kwargs


NativeAgent = Agent


class Task:
    """Zero-framework Task specification."""

    def __init__(
        self,
        description: str = "",
        expected_output: str = "",
        *,
        name: str = "",
        agent: Optional[Agent] = None,
        output_pydantic: Optional[type[BaseModel]] = None,
        context: Optional[Sequence["Task"]] = None,
        **kwargs: Any,
    ):
        self.description = description
        self.expected_output = expected_output
        self.name = name
        self.agent = agent
        self.output_pydantic = output_pydantic
        self.context = list(context) if context is not None else []
        self.kwargs = kwargs


NativeTask = Task


class Process:
    sequential = "sequential"
    hierarchical = "hierarchical"


NativeProcess = Process


@dataclass
class NativeTaskOutput:
    """Task output compatible with ``CrewResultAdapter``."""

    name: str
    raw: str = ""
    pydantic: Optional[BaseModel] = None
    description: str = ""


@dataclass
class NativeCrewOutput:
    """Crew execution output compatible with ``CrewResultAdapter``."""

    raw: str = ""
    pydantic: Optional[BaseModel] = None
    tasks_output: list[NativeTaskOutput] = field(default_factory=list)


class Crew:
    """Zero-framework sequential task runner."""

    def __init__(
        self,
        agents: Sequence[Any],
        tasks: Sequence[Any],
        *,
        process: str = Process.sequential,
        verbose: bool = False,
        max_rpm: int = 20,
        step_callback: Optional[Callable[[Any], None]] = None,
        gateway: Optional[ZeroFrameworkLLMGateway] = None,
        **kwargs: Any,
    ):
        self.agents = list(agents)
        self.tasks = list(tasks)
        self.process = process
        self.verbose = verbose
        self.max_rpm = max_rpm
        self.step_callback = step_callback
        self.gateway = gateway or ZeroFrameworkLLMGateway()
        self.kwargs = kwargs

    def kickoff(self, inputs: Optional[dict[str, Any]] = None) -> NativeCrewOutput:
        """Execute tasks sequentially using deterministic tools + ZeroFrameworkLLMGateway."""
        inp = dict(inputs or {})
        completed_by_name: dict[str, NativeTaskOutput] = {}
        outputs: list[NativeTaskOutput] = []

        for idx, task in enumerate(self.tasks, start=1):
            t_name = getattr(task, "name", None) or f"task_{idx}"
            agent = getattr(task, "agent", None)
            desc = interpolate_only(getattr(task, "description", ""), inp)
            expected = interpolate_only(getattr(task, "expected_output", ""), inp)

            # Gather upstream context from explicit task.context
            ctx_tasks = getattr(task, "context", None) or []
            ctx_blobs: list[str] = []
            for ct in ctx_tasks:
                ct_name = getattr(ct, "name", "")
                if ct_name in completed_by_name:
                    ctx_blobs.append(
                        f"=== Context from {ct_name} ===\n{completed_by_name[ct_name].raw}"
                    )
            context_str = "\n\n".join(ctx_blobs)

            tools = getattr(agent, "tools", None) or []
            out_model = getattr(task, "output_pydantic", None)

            # Deterministic tool-collector shortcut (0 LLM tokens when agent has tools and no Pydantic schema)
            if tools and out_model is None:
                tool_results: list[str] = []
                for t in tools:
                    t_label = getattr(t, "name", str(t))
                    try:
                        res = t.run("") if hasattr(t, "run") else t("")
                        tool_results.append(f"[{t_label}]\n{res}")
                    except Exception as exc:
                        tool_results.append(f"[{t_label}] Error: {exc}")
                raw_text = "\n\n".join(tool_results)
                task_out = NativeTaskOutput(
                    name=t_name, raw=raw_text, pydantic=None, description=desc
                )
            elif out_model is not None:
                llm_obj = getattr(agent, "llm", None)
                model_id = getattr(llm_obj, "model", "gemini/gemini-2.5-flash")
                temp = float(getattr(llm_obj, "temperature", 0.0))
                spec = ModelSpec(
                    model_id=model_id,
                    family=detect_model_family(model_id),
                    temperature=temp,
                )
                sys_prompt = (
                    f"Role: {getattr(agent, 'role', 'Auditor')}\n"
                    f"Goal: {getattr(agent, 'goal', '')}\n"
                    f"Backstory: {getattr(agent, 'backstory', '')}"
                ).strip()
                user_prompt = f"{desc}\n\nExpected Output:\n{expected}" + (
                    f"\n\n{context_str}" if context_str else ""
                )
                parsed = self.gateway.invoke_structured(
                    phase=0,
                    role=t_name,
                    spec=spec,
                    system_prompt=sys_prompt,
                    user_prompt=user_prompt,
                    response_model=out_model,
                )
                raw_text = parsed.model_dump_json(indent=2)
                task_out = NativeTaskOutput(
                    name=t_name, raw=raw_text, pydantic=parsed, description=desc
                )
            else:
                # Non-Pydantic intermediate memo/markdown task
                class _TextEnvelope(BaseModel):
                    content: str

                llm_obj = getattr(agent, "llm", None)
                model_id = getattr(llm_obj, "model", "gemini/gemini-2.5-flash")
                temp = float(getattr(llm_obj, "temperature", 0.1))
                spec = ModelSpec(
                    model_id=model_id,
                    family=detect_model_family(model_id),
                    temperature=temp,
                )
                sys_prompt = (
                    f"Role: {getattr(agent, 'role', 'Auditor')}\n"
                    f"Goal: {getattr(agent, 'goal', '')}\n"
                    f"Backstory: {getattr(agent, 'backstory', '')}"
                ).strip()
                user_prompt = f"{desc}\n\nExpected Output:\n{expected}" + (
                    f"\n\n{context_str}" if context_str else ""
                )
                parsed_env = self.gateway.invoke_structured(
                    phase=0,
                    role=t_name,
                    spec=spec,
                    system_prompt=sys_prompt,
                    user_prompt=user_prompt,
                    response_model=_TextEnvelope,
                )
                task_out = NativeTaskOutput(
                    name=t_name,
                    raw=parsed_env.content,
                    pydantic=None,
                    description=desc,
                )

            if self.step_callback is not None:
                self.step_callback(
                    SimpleNamespace(
                        thought=f"Completed {t_name}",
                        output=task_out.raw[:300],
                    )
                )

            completed_by_name[t_name] = task_out
            outputs.append(task_out)

        last = outputs[-1] if outputs else NativeTaskOutput(name="empty")
        return NativeCrewOutput(
            raw=last.raw,
            pydantic=last.pydantic,
            tasks_output=outputs,
        )


NativeCrew = Crew
