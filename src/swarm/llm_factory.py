import os
import logging
from crewai import LLM

logger = logging.getLogger(__name__)

def get_crew_llm(temperature: float = 0.1) -> LLM:
    """
    Dynamically instantiate a native CrewAI LLM based on environment variables.
    Priority:
      1. Gemini (Most generous free-tier TPM)
      2. OpenAI (Enterprise standard)
      3. Groq (Fastest, but harsh TPM limits)
    """
    
    if os.environ.get("GEMINI_API_KEY"):
        logger.info("[LLM Factory] Binding to Gemini 1.5 Flash.")
        return LLM(model="gemini/gemini-1.5-flash", temperature=temperature)
        
    if os.environ.get("OPENAI_API_KEY"):
        logger.info("[LLM Factory] Binding to OpenAI GPT-4o-mini.")
        return LLM(model="openai/gpt-4o-mini", temperature=temperature)
        
    if os.environ.get("GROQ_API_KEY"):
        logger.info("[LLM Factory] Binding to Groq Llama 3.3 70B.")
        return LLM(model="groq/llama-3.3-70b-versatile", temperature=temperature)
        
    logger.warning("[LLM Factory] No API keys found! Defaulting to mocked/fallback LLM.")
    return LLM(model="openai/gpt-4o-mini", temperature=temperature)
