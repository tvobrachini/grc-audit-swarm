"""
Zero-Framework Typed LLM Gateway, Powerpipe 4-State Probes & SWE-Agent ACI Helpers.

Replaces heavy multi-agent frameworks with:
  1. **Powerpipe / Lula Deterministic 4-State Probe Runner** (`ok | alarm | info | skip`):
     Executes read-only collectors in Python (0 tokens) and deterministically resolves
     unmapped (`skip` -> `Not tested`) and unambiguous (`ok` / `alarm`) controls.
  2. **Princeton SWE-Agent ACI Line-Indexed Evidence Formatter**:
     Numbers raw evidence lines (`L001: ...`) so models can reference line spans
     `(vault_id, start_line, end_line)` and Python extracts the byte-exact quote.
  3. **Cryptographic LLMCallReceipt Provenance**:
     Records SHA-256 digests of prompt, JSON schema, and response alongside token
     counts, latency, and USD cost.
  4. **Surgical Single-Control Retry**:
     Re-prompts only the specific `control_id` that failed a linter or cascade check
     instead of re-running the entire phase crew.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from swarm.model_routing import (
    ModelSpec,
)
from swarm.schema import (
    AuditFindingSchema,
    DesignConclusion,
    FindingResult,
    OperatingConclusion,
    RiskControlMatrixSchema,
    WorkingPaperSchema,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ── Powerpipe 4-State Probe Tuple (Turbot Powerpipe / Defense Unicorns Lula) ─


class ProbeStatus(StrEnum):
    """Turbot Powerpipe 4-state compliance probe status."""

    OK = "ok"  # Control criterion deterministically satisfied
    ALARM = "alarm"  # Control criterion deterministically violated (exception)
    INFO = "info"  # Evidence collected; requires System-1/System-2 judgment
    SKIP = "skip"  # No collector tool covers this control -> auto 'Not tested'


@dataclass(frozen=True)
class ControlProbeResult:
    """Deterministic probe output for a single RACM control (0 LLM tokens)."""

    control_id: str
    resource: str
    status: ProbeStatus
    reason: str
    vault_id: str = ""
    exact_quote: str = ""
    items_tested: int | None = None
    exceptions_noted: int | None = None
    tod_conclusion: DesignConclusion = DesignConclusion.NOT_TESTED
    toe_conclusion: OperatingConclusion = OperatingConclusion.NOT_TESTED
    toe_basis: str | None = None

    def to_finding(self) -> AuditFindingSchema:
        """Convert a deterministic probe result into a schema-valid AuditFindingSchema."""
        if self.status == ProbeStatus.SKIP:
            return AuditFindingSchema(
                control_id=self.control_id,
                vault_id_reference="",
                exact_quote_from_evidence="",
                tod_conclusion=DesignConclusion.NOT_TESTED,
                toe_conclusion=OperatingConclusion.NOT_TESTED,
                result=FindingResult.NOT_TESTED,
                preliminary_deficiency=False,
                test_conclusion=self.reason
                or f"No automated evidence collector covers {self.control_id}; recorded as Not tested.",
            )
        return AuditFindingSchema(
            control_id=self.control_id,
            vault_id_reference=self.vault_id,
            exact_quote_from_evidence=self.exact_quote,
            tod_conclusion=self.tod_conclusion,
            toe_conclusion=self.toe_conclusion,
            toe_basis=self.toe_basis,
            items_tested=self.items_tested,
            exceptions_noted=self.exceptions_noted,
            test_conclusion=self.reason,
        )


def resolve_unmapped_controls_deterministically(
    racm: RiskControlMatrixSchema,
    mapped_control_ids: set[str],
) -> dict[str, AuditFindingSchema]:
    """Deterministically generate 'Not tested' findings for controls without a collector (0 tokens)."""
    resolved: dict[str, AuditFindingSchema] = {}
    for risk in racm.risks:
        for ctrl in risk.controls:
            if ctrl.control_id not in mapped_control_ids:
                probe = ControlProbeResult(
                    control_id=ctrl.control_id,
                    resource=ctrl.control_id,
                    status=ProbeStatus.SKIP,
                    reason=(
                        f"No automated collector covers control {ctrl.control_id} "
                        f"({ctrl.nature or 'unspecified nature'}); concluded Not tested."
                    ),
                )
                resolved[ctrl.control_id] = probe.to_finding()
    return resolved


# ── Princeton SWE-Agent ACI Line-Indexed Evidence View ──────────────────────


def format_line_indexed_evidence(vault_id: str, raw_payload: str) -> str:
    """Format raw vault evidence with 1-based line numbers (`L001: ...`) for span-bound citation."""
    lines = (raw_payload or "").splitlines()
    indexed = "\n".join(
        f"L{idx:03d}: {line}" for idx, line in enumerate(lines, start=1)
    )
    return f"=== Vault ID: {vault_id} ({len(lines)} lines) ===\n{indexed}"


def extract_quote_by_lines(
    raw_payload: str, start_line: int, end_line: int | None = None
) -> str:
    """Extract the byte-exact substring from ``raw_payload`` for 1-based line range ``[start_line, end_line]``.

    Guarantees ``EvidenceAssuranceProtocol.verify_exact_quote`` passes (`I_span = 1.0`)
    without the model ever having to re-type JSON punctuation.
    """
    lines = (raw_payload or "").splitlines()
    if not lines:
        return ""
    end = end_line if end_line is not None else start_line
    s_idx = max(1, min(start_line, len(lines))) - 1
    e_idx = max(s_idx + 1, min(end, len(lines)))
    return "\n".join(lines[s_idx:e_idx])


# ── Cryptographic LLMCallReceipt Provenance ──────────────────────────────────


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LLMCallReceipt:
    """Immutable cryptographic receipt for a single structured LLM call."""

    call_id: str
    phase: int
    role: str
    model_id: str
    model_family: str
    effort: str
    temperature: float
    prompt_sha256: str
    schema_sha256: str
    response_sha256: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    timestamp: str


_PROVIDER_CONFIG: dict[str, tuple[str, tuple[str, ...]]] = {
    "openai": ("https://api.openai.com/v1/chat/completions", ("OPENAI_API_KEY",)),
    "anthropic": ("https://api.anthropic.com/v1/messages", ("ANTHROPIC_API_KEY",)),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        ("GEMINI_API_KEY",),
    ),
    "nvidia_nim": (
        "https://integrate.api.nvidia.com/v1/chat/completions",
        ("NVIDIA_NIM_API_KEY", "NVIDIA_API_KEY"),
    ),
    "openrouter": (
        "https://openrouter.ai/api/v1/chat/completions",
        ("OPENROUTER_API_KEY",),
    ),
    "ollama": ("http://localhost:11434/v1/chat/completions", ()),
    "groq": ("https://api.groq.com/openai/v1/chat/completions", ("GROQ_API_KEY",)),
}


def _resolve_provider_and_model(model_id: str) -> tuple[str, str]:
    """Split a prefixed model string into ``(provider, stripped_model_name)``."""
    raw = (model_id or "").strip()
    for prefix in _PROVIDER_CONFIG:
        token = f"{prefix}/"
        if raw.startswith(token):
            return prefix, raw[len(token) :]
    return "openai", raw


def _resolve_endpoint_url(provider: str, base_url: str | None) -> str:
    if base_url:
        base = base_url.rstrip("/")
        if provider == "anthropic":
            if base.endswith("/messages"):
                return base
            if base.endswith("/v1"):
                return f"{base}/messages"
            return f"{base}/v1/messages"
        if base.endswith("/chat/completions"):
            return base
        if provider == "ollama" and not base.endswith("/v1"):
            return f"{base}/v1/chat/completions"
        return f"{base}/chat/completions"
    return _PROVIDER_CONFIG[provider][0]


def _resolve_api_key(provider: str, api_key: str | None) -> str | None:
    if api_key:
        return api_key
    for env_var in _PROVIDER_CONFIG[provider][1]:
        val = os.environ.get(env_var)
        if val:
            return val
    return None


def _extract_json_text(content: Any) -> str:
    if not isinstance(content, str):
        return json.dumps(content)
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


class ZeroFrameworkLLMGateway:
    """Lightweight typed JSON-Schema LLM gateway with direct httpx transport and receipt logging."""

    def __init__(
        self,
        *,
        completion_fn: Callable[..., Any] | None = None,
    ):
        self._completion_fn = completion_fn
        self.receipts: list[LLMCallReceipt] = []

    def invoke_structured(
        self,
        *,
        phase: int,
        role: str,
        spec: ModelSpec,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 120.0,
    ) -> T:
        """Call ``spec.model_id`` enforcing ``response_model`` JSON schema and record an LLMCallReceipt."""
        t0 = time.monotonic()
        schema_json = json.dumps(
            response_model.model_json_schema(), sort_keys=True, separators=(",", ":")
        )
        combined_prompt = f"{system_prompt}\n---\n{user_prompt}"
        prompt_hash = _sha256_text(combined_prompt)
        schema_hash = _sha256_text(schema_json)

        raw_json_str: str
        in_tok = max(1, len(combined_prompt) // 4)
        out_tok = 0

        if self._completion_fn is not None:
            result = self._completion_fn(
                spec=spec,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
            )
            if isinstance(result, response_model):
                parsed = result
                raw_json_str = parsed.model_dump_json()
            elif isinstance(result, dict):
                parsed = response_model.model_validate(result)
                raw_json_str = json.dumps(result, sort_keys=True)
            else:
                raw_json_str = str(result)
                parsed = response_model.model_validate_json(raw_json_str)
        else:
            raw_json_str, in_tok, out_tok = self._invoke_httpx(
                spec=spec,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema_json=schema_json,
                default_in_tok=in_tok,
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
            )
            parsed = response_model.model_validate_json(raw_json_str)

        if out_tok == 0:
            out_tok = max(1, len(raw_json_str) // 4)

        latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
        cost_usd = round(
            (in_tok / 1000.0) * spec.cost_per_1k_input_usd
            + (out_tok / 1000.0) * spec.cost_per_1k_output_usd,
            6,
        )
        receipt = LLMCallReceipt(
            call_id=str(uuid.uuid4()),
            phase=phase,
            role=role,
            model_id=spec.model_id,
            model_family=spec.family.value,
            effort=spec.effort.value,
            temperature=spec.temperature,
            prompt_sha256=prompt_hash,
            schema_sha256=schema_hash,
            response_sha256=_sha256_text(raw_json_str),
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
        )
        self.receipts.append(receipt)
        return parsed

    def _invoke_httpx(
        self,
        *,
        spec: ModelSpec,
        system_prompt: str,
        user_prompt: str,
        schema_json: str,
        default_in_tok: int,
        api_key: str | None,
        base_url: str | None,
        timeout: float,
    ) -> tuple[str, int, int]:
        provider, model_name = _resolve_provider_and_model(spec.model_id)
        url = _resolve_endpoint_url(provider, base_url)
        resolved_key = _resolve_api_key(provider, api_key)
        schema_system = (
            f"{system_prompt}\n\n"
            f"Respond ONLY with valid JSON matching this JSON Schema:\n{schema_json}"
        ).strip()

        if provider == "anthropic":
            headers: dict[str, str] = {
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            if resolved_key:
                headers["x-api-key"] = resolved_key
            payload: dict[str, Any] = {
                "model": model_name,
                "max_tokens": 8192,
                "temperature": spec.temperature,
                "system": schema_system,
                "messages": [{"role": "user", "content": user_prompt}],
            }
            resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            blocks = data.get("content") or []
            text_parts = [
                b.get("text", "")
                for b in blocks
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            raw_content: Any = "\n".join(text_parts) if text_parts else blocks
            raw_json_str = _extract_json_text(raw_content)
            usage = data.get("usage") or {}
            in_tok = int(
                usage.get("input_tokens")
                or usage.get("prompt_tokens")
                or default_in_tok
            )
            out_tok = int(
                usage.get("output_tokens") or usage.get("completion_tokens") or 0
            )
            return raw_json_str, in_tok, out_tok

        headers = {"content-type": "application/json"}
        if resolved_key:
            headers["Authorization"] = f"Bearer {resolved_key}"
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": schema_system},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": spec.temperature,
            "response_format": {"type": "json_object"},
        }
        resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        raw_json_str = _extract_json_text(message.get("content", ""))
        usage = data.get("usage") or {}
        in_tok = int(usage.get("prompt_tokens") or default_in_tok)
        out_tok = int(usage.get("completion_tokens") or 0)
        return raw_json_str, in_tok, out_tok


def merge_surgical_finding_retries(
    original_papers: WorkingPaperSchema,
    repaired_findings: Sequence[AuditFindingSchema],
) -> WorkingPaperSchema:
    """Replace only the repaired control findings in ``original_papers`` while preserving RACM order."""
    by_id = {f.control_id: f for f in repaired_findings}
    merged = [by_id.get(f.control_id, f) for f in original_papers.findings]
    return original_papers.model_copy(update={"findings": merged})
