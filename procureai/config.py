"""Agent configuration and LLM model factory."""

from pathlib import Path
from typing import Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from pydantic import AliasChoices, DirectoryPath, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default directories relative to the project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_POLICY_DIR = _PROJECT_ROOT / "data" / "policies"
_DEFAULT_MEMO_DIR = _PROJECT_ROOT / "data" / "memos"


class AgentConfig(BaseSettings):
    """Configuration for the procurement agent.

    Values are loaded from environment variables prefixed with PROCUREAI_,
    e.g. PROCUREAI_MODEL_NAME=claude-sonnet-4-6.

    The Anthropic API key is read from ANTHROPIC_API_KEY (no prefix).
    """

    model_config = SettingsConfigDict(
        env_prefix="PROCUREAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: SecretStr = Field(
        validation_alias=AliasChoices("anthropic_api_key", "ANTHROPIC_API_KEY"),
    )
    model_provider: Literal["anthropic"] = "anthropic"
    model_name: str = "claude-sonnet-4-6"
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=4096, gt=0)
    policy_dir: DirectoryPath = _DEFAULT_POLICY_DIR
    memo_dir: DirectoryPath = _DEFAULT_MEMO_DIR

    @field_validator("model_name")
    @classmethod
    def model_name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("model_name must not be empty")
        return v


def get_chat_model(config: AgentConfig) -> BaseChatModel:
    """Return a LangChain chat model based on the config provider."""
    if config.model_provider == "anthropic":
        return ChatAnthropic(
            model=config.model_name,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            api_key=config.anthropic_api_key.get_secret_value(),
        )
    # Literal type makes this unreachable, but keeps the contract explicit
    raise ValueError(f"Unsupported model provider: {config.model_provider}")
