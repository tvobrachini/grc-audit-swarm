"""
LLM Factory for the Swarm Agents.

Priority order (fully open-source / free-tier compatible):
  1. Groq  (llama-3.3-70b-versatile) — free tier, very fast
  2. OpenAI (gpt-4o-mini)             — fallback if GROQ_API_KEY absent
  3. None  → agents fall back to mock data

Set either GROQ_API_KEY or OPENAI_API_KEY in your .env file.
"""

import logging
import os

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


def get_llm(temperature: float = 0.1, prefer_fast: bool = False):
    """
    Return a chat model instance based on available API keys.

    prefer_fast=True selects a smaller/faster model variant when
    latency matters more than quality (e.g. query generation).
    """
    groq_key = os.environ.get("GROQ_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if groq_key:
        try:
            from langchain_groq import ChatGroq

            model = "llama-3.1-8b-instant" if prefer_fast else "llama-3.3-70b-versatile"
            logger.info("[LLM Factory] Using Groq → %s", model)
            return ChatGroq(model=model, temperature=temperature, api_key=groq_key)
        except ImportError:
            logger.warning(
                "[LLM Factory] langchain-groq not installed, falling back to OpenAI."
            )

    if openai_key:
        from langchain_openai import ChatOpenAI

        model = "gpt-4o-mini" if prefer_fast else "gpt-4o"
        logger.info("[LLM Factory] Using OpenAI → %s", model)
        return ChatOpenAI(model=model, temperature=temperature, api_key=openai_key)

    logger.warning(
        "[LLM Factory] No API key found. Returning None → agents will use mock data."
    )
    return None


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def invoke_with_retry(chain, inputs: dict):
    """
    Invoke a LangChain chain with exponential backoff retry on transient failures.

    Retries up to 3 times with 2-60 second exponential backoff — designed to
    handle Groq TPM rate limit errors (429) and transient network timeouts.
    """
    return chain.invoke(inputs)
