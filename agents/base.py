"""
Base agent class providing shared LLM access and MCP tool invocation.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/junit-generator-pipeline",
    "X-Title": "JUnit Generator Pipeline",
}


def get_llm(
    provider: str = "openrouter",
    model: str = "google/gemini-2.0-flash-exp:free",
    temperature: float = 0.2,
    base_url: str = "http://localhost:11434",
    api_key: str = "",
) -> BaseChatModel:
    """
    Factory — returns the configured LLM (JSON-mode where supported).

    Providers:
      openrouter  Free, fast cloud models. Get key: https://openrouter.ai/keys
                  Fast free models:
                    google/gemini-2.0-flash-exp:free      ← fastest, high limits
                    meta-llama/llama-3.3-70b-instruct:free
                    mistralai/mistral-7b-instruct:free
                    deepseek/deepseek-r1:free
      ollama      Local models, no API key needed
      grok        xAI Grok (requires GROK_API_KEY)
      openai      OpenAI (requires OPENAI_API_KEY)
      anthropic   Anthropic (requires ANTHROPIC_API_KEY)
    """
    if provider == "openrouter":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers=_OPENROUTER_HEADERS,
            max_retries=3,
        )
    elif provider == "grok":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=api_key,
            base_url="https://api.x.ai/v1",
            max_retries=3,
        )
    elif provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=model,
            temperature=temperature,
            base_url=base_url,
            format="json",
        )
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=temperature, api_key=api_key)
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=temperature, api_key=api_key)
    else:
        raise ValueError(
            f"Unknown LLM provider '{provider}'. "
            "Choose from: openrouter, grok, ollama, openai, anthropic"
        )


def get_llm_text(
    provider: str = "openrouter",
    model: str = "google/gemini-2.0-flash-exp:free",
    temperature: float = 0.2,
    base_url: str = "http://localhost:11434",
    api_key: str = "",
) -> BaseChatModel:
    """
    Same as get_llm but without format='json'.
    Use for agents that generate free-form Java code, not structured JSON.
    """
    if provider == "openrouter":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers=_OPENROUTER_HEADERS,
            max_retries=3,
        )
    elif provider == "grok":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=api_key,
            base_url="https://api.x.ai/v1",
            max_retries=3,
        )
    elif provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model, temperature=temperature, base_url=base_url)
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=temperature, api_key=api_key)
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=temperature, api_key=api_key)
    else:
        raise ValueError(f"Unknown LLM provider '{provider}'.")


class BaseAgent:
    """Shared base for all pipeline agents."""

    def __init__(self, llm: BaseChatModel, tools: dict[str, Any] | None = None):
        self.llm = llm
        self.tools = tools or {}

    def invoke_tool(self, tool_name: str, **kwargs) -> Any:
        """Call a registered MCP tool function directly."""
        if tool_name not in self.tools:
            raise ValueError(f"Tool '{tool_name}' not registered in agent.")
        return self.tools[tool_name](**kwargs)

    def call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """
        Send a system + user message to the LLM and return the text response.
        Retries with exponential backoff on 429 rate-limit errors.
        """
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        max_retries = 6
        delay = 5  # seconds, doubles each retry
        for attempt in range(max_retries):
            try:
                response = self.llm.invoke(messages)
                return response.content
            except Exception as e:
                err = str(e).lower()
                is_rate_limit = (
                    "429" in err
                    or "rate limit" in err
                    or "too many requests" in err
                    or "ratelimit" in err
                )
                if is_rate_limit and attempt < max_retries - 1:
                    wait = delay * (2 ** attempt)
                    logger.warning(
                        f"Rate limited by {self.llm.__class__.__name__}. "
                        f"Waiting {wait}s (attempt {attempt + 1}/{max_retries - 1})..."
                    )
                    time.sleep(wait)
                else:
                    raise

    def call_llm_json(
        self,
        system_prompt: str,
        user_prompt: str,
        default: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Call LLM and robustly parse the response as JSON.
        Falls back through multiple strategies before returning the default.
        Never raises — a bad LLM response never crashes the pipeline.
        """
        try:
            raw = self.call_llm(system_prompt, user_prompt)
        except Exception as e:
            return default if default is not None else {"error": str(e)}

        if not raw or not raw.strip():
            return default if default is not None else {}

        raw = raw.strip()

        # 1. Strip markdown code fences
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```\s*$", "", raw)
            raw = raw.strip()

        # 2. Direct parse
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # 3. Extract first {...} block (handles prose wrapping JSON)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            candidate = match.group()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                # 4. Fix common model JSON mistakes
                fixed = candidate
                fixed = re.sub(r"'([^']*)'(\s*:)", r'"\1"\2', fixed)
                fixed = re.sub(r":\s*'([^']*)'", r': "\1"', fixed)
                fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
                fixed = re.sub(r"\bTrue\b", "true", fixed)
                fixed = re.sub(r"\bFalse\b", "false", fixed)
                fixed = re.sub(r"\bNone\b", "null", fixed)
                try:
                    return json.loads(fixed)
                except json.JSONDecodeError:
                    pass

        return default if default is not None else {"raw_response": raw}
