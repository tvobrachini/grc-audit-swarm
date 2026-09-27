import os
import logging
from crewai import LLM

logger = logging.getLogger(__name__)

_NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

_PROVIDER_ENV_VARS = (
    "OLLAMA_MODEL",
    "NVIDIA_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
)


class LLMConfigurationError(RuntimeError):
    """Raised when no LLM provider is configured in the environment."""


def get_crew_llm(temperature: float = 0.1, prefer_fast: bool = False) -> LLM:
    """
    Dynamically instantiate a native CrewAI LLM based on environment variables.
    Priority:
      1. Ollama (Local - No limits, zero cost)
      2. NVIDIA NIM (DeepSeek / Llama 3.3 via H100s)
      3. Gemini (Most generous free-tier TPM)
      4. OpenAI (Enterprise standard)
      5. Groq (Fastest, but harsh TPM limits)

    Credentials are passed to the LLM object directly — this function never
    mutates ``os.environ``, so one provider's key cannot leak into another
    provider's client elsewhere in the process.

    Raises:
        LLMConfigurationError: if none of the supported providers is configured.
            There is deliberately no silent fallback: a crew run without a real
            model would only fail later with a confusing authentication error.
    """
    ollama_model = os.environ.get("OLLAMA_MODEL")
    if ollama_model:
        # Local Ollama integration
        logger.info(f"[LLM Factory] Binding to Local Ollama: {ollama_model}.")
        return LLM(
            model=f"ollama/{ollama_model}",
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=temperature,
            timeout=120,
        )

    nvidia_key = os.environ.get("NVIDIA_API_KEY")
    if nvidia_key:
        try:
            model_name = "meta/llama-3.3-70b-instruct"
            logger.info(f"[LLM Factory] Binding to NVIDIA NIM: {model_name}.")
            return LLM(
                model=f"nvidia_nim/{model_name}",
                api_key=nvidia_key,
                base_url=os.environ.get("NVIDIA_BASE_URL", _NVIDIA_DEFAULT_BASE_URL),
                temperature=temperature,
                timeout=120,
            )
        except Exception as e:
            logger.warning(
                f"[LLM Factory] NVIDIA NIM failed to initialize: {e}. "
                "Falling back to the next configured provider."
            )

    if os.environ.get("GEMINI_API_KEY"):
        # gemini-2.0-flash was shut down on 2026-06-01; override with GEMINI_MODEL.
        gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        logger.info(f"[LLM Factory] Binding to Gemini: {gemini_model}.")
        return LLM(model=f"gemini/{gemini_model}", temperature=temperature)

    if os.environ.get("OPENAI_API_KEY"):
        logger.info("[LLM Factory] Binding to OpenAI GPT-4o-mini.")
        return LLM(model="openai/gpt-4o-mini", temperature=temperature)

    if os.environ.get("GROQ_API_KEY"):
        logger.info("[LLM Factory] Binding to Groq Llama 3.3 70B Versatile.")
        return LLM(model="groq/llama-3.3-70b-versatile", temperature=temperature)

    raise LLMConfigurationError(
        "No LLM provider configured. Set one of: "
        + ", ".join(_PROVIDER_ENV_VARS)
        + " (or run with DEMO_MODE=1 to bypass the crews)."
    )


def qa_llm_configured() -> bool:
    """True when a separate QA reviewer model is configured (QA_LLM_MODEL)."""
    return bool(os.environ.get("QA_LLM_MODEL", "").strip())


def get_qa_llm(temperature: float = 0.0) -> LLM:
    """The LLM for the QA reviewer agents.

    A reviewer running on the same model as the preparer shares its blind
    spots and training biases, so its approval is not an independent check.
    Set ``QA_LLM_MODEL`` (a LiteLLM model string such as
    ``openai/gpt-4o-mini`` or ``ollama/qwen2.5``) to give the QA agents a
    different provider/model; ``QA_LLM_API_KEY`` and ``QA_LLM_BASE_URL`` are
    passed to that client only (the process environment is not changed).

    Without ``QA_LLM_MODEL`` this is exactly :func:`get_crew_llm` (the
    default, unchanged behaviour). A different model is still not a human
    reviewer: the human gates remain the sign-off.
    """
    model = os.environ.get("QA_LLM_MODEL", "").strip()
    if not model:
        return get_crew_llm(temperature=temperature)
    kwargs: dict = {"model": model, "temperature": temperature, "timeout": 120}
    api_key = os.environ.get("QA_LLM_API_KEY")
    if api_key:
        kwargs["api_key"] = api_key
    base_url = os.environ.get("QA_LLM_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    logger.info(f"[LLM Factory] QA reviewers bound to separate model: {model}.")
    return LLM(**kwargs)
