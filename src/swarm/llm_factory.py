import os
import logging
from crewai import LLM

logger = logging.getLogger(__name__)


def get_crew_llm(temperature: float = 0.1, prefer_fast: bool = False) -> LLM:
    """
    Dynamically instantiate a native CrewAI LLM based on environment variables.
    Priority:
      1. Ollama (Local - No limits, zero cost)
      2. NVIDIA NIM (DeepSeek / Llama 3.3 via H100s)
      3. Gemini (Most generous free-tier TPM)
      4. OpenAI (Enterprise standard)
      5. Groq (Fastest, but harsh TPM limits)
    """
    if os.environ.get("OLLAMA_MODEL"):
        # Local Ollama integration
        model_name = os.environ.get("OLLAMA_MODEL")
        logger.info(f"[LLM Factory] Binding to Local Ollama: {model_name}.")
        return LLM(
            model=f"ollama/{model_name}",
            base_url="http://localhost:11434",
            temperature=temperature,
            timeout=120
        )

    if os.environ.get("NVIDIA_API_KEY"):
        try:
            # Native NVIDIA NIM provider often expects this specific env var
            os.environ["NVIDIA_NIM_API_KEY"] = os.environ["NVIDIA_API_KEY"]
            # Also set OPENAI_API_KEY as a backup for compatibility layers
            os.environ["OPENAI_API_KEY"] = os.environ["NVIDIA_API_KEY"]
            
            model_name = "meta/llama-3.3-70b-instruct"
            logger.info(f"[LLM Factory] Binding to NVIDIA NIM: {model_name}.")
            return LLM(
                model=f"nvidia_nim/{model_name}",
                api_key=os.environ.get("NVIDIA_API_KEY"),
                temperature=temperature,
                timeout=120
            )
        except Exception as e:
            logger.warning(f"[LLM Factory] NVIDIA NIM failed to initialize: {e}. Falling back to Gemini.")

    if os.environ.get("GEMINI_API_KEY"):
        logger.info("[LLM Factory] Binding to Gemini 2.0 Flash.")
        return LLM(model="gemini/gemini-2.0-flash", temperature=temperature)

    if os.environ.get("OPENAI_API_KEY"):
        logger.info("[LLM Factory] Binding to OpenAI GPT-4o-mini.")
        return LLM(model="openai/gpt-4o-mini", temperature=temperature)

    if os.environ.get("GROQ_API_KEY"):
        logger.info("[LLM Factory] Binding to Groq Llama 3.3 70B Versatile.")
        return LLM(model="groq/llama-3.3-70b-versatile", temperature=temperature)

    logger.warning(
        "[LLM Factory] No API keys found! Defaulting to mocked/fallback LLM."
    )
    return LLM(model="openai/gpt-4o-mini", temperature=temperature)


# Alias for runtime_adapters compatibility
get_llm = get_crew_llm
