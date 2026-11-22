"""Environment-driven configuration for the agent (see .env.example)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["AgentSettings", "get_agent_settings"]


class AgentSettings(BaseSettings):
    """LLM and loop settings; secrets always come from the environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_key: str | None = None
    llm_model: str = "claude-sonnet-5-5"
    llm_max_tokens: int = 2048
    agent_max_steps: int = 8

    # Pricing is an estimate used by evals; these are fictional placeholder rates.
    cost_input_per_million: float = 3.0
    cost_output_per_million: float = 15.0


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
