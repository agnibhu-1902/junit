"""
Base agent class providing shared LLM access and MCP tool invocation.
"""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel


def get_llm(
    provider: str = "ollama",
    model: str = "llama3.2",
    temperature: float = 0.2,
    base_url: str = "http://localhost:11434",
) -> BaseChatModel:
    """
    Factory that returns the configured LLM.

    Supported providers:
      - ollama     : Local Llama via Ollama (default, free, no API key needed)
                     Install: https://ollama.com  then: ollama pull llama3.2
      - openai     : OpenAI API (requires OPENAI_API_KEY)
      - anthropic  : Anthropic API (requires ANTHROPIC_API_KEY)
    """
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model, temperature=temperature, base_url=base_url)
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model, temperature=temperature)
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, temperature=temperature)
    else:
        raise ValueError(
            f"Unknown LLM provider '{provider}'. "
            "Choose from: ollama, openai, anthropic"
        )


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
        """Send a system + user message to the LLM and return the text response."""
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        response = self.llm.invoke(messages)
        return response.content

    def call_llm_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Call LLM and parse the response as JSON."""
        raw = self.call_llm(system_prompt, user_prompt)
        # Strip markdown code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Attempt to extract JSON block
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                return json.loads(match.group())
            return {"raw_response": raw}
