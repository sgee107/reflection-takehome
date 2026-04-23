"""Agent configuration and LLM model factory."""

from pathlib import Path
from typing import Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import (
    AliasChoices,
    DirectoryPath,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default directories relative to the project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_POLICY_DIR = _PROJECT_ROOT / "data" / "policies"
_DEFAULT_MEMO_DIR = _PROJECT_ROOT / "data" / "memos"

# Supported providers
ProviderLiteral = Literal["anthropic", "openai", "vllm", "ollama"]


class AgentConfig(BaseSettings):
    """Configuration for the procurement agent.

    Values are loaded from environment variables prefixed with PROCUREAI_,
    e.g. PROCUREAI_MODEL_NAME=claude-sonnet-4-6.

    Provider-specific API keys use their standard env var names:
    - ANTHROPIC_API_KEY for anthropic
    - OPENAI_API_KEY for openai (and optionally for vllm/ollama)

    For vLLM and Ollama, set PROCUREAI_BASE_URL to the OpenAI-compatible endpoint
    (e.g. http://localhost:11434/v1 for Ollama).
    """

    model_config = SettingsConfigDict(
        env_prefix="PROCUREAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("anthropic_api_key", "ANTHROPIC_API_KEY"),
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("openai_api_key", "OPENAI_API_KEY"),
    )
    model_provider: ProviderLiteral = "anthropic"
    model_name: str = "claude-sonnet-4-6"
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=4096, gt=0)
    base_url: str | None = None
    policy_dir: DirectoryPath = _DEFAULT_POLICY_DIR
    memo_dir: DirectoryPath = _DEFAULT_MEMO_DIR

    @field_validator("model_name")
    @classmethod
    def model_name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("model_name must not be empty")
        return v

    @model_validator(mode="after")
    def validate_provider_requirements(self) -> "AgentConfig":
        """Validate that required fields are set for the chosen provider."""
        provider = self.model_provider

        # API key checks
        if provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError(
                "anthropic_api_key (ANTHROPIC_API_KEY) is required "
                "when model_provider is 'anthropic'."
            )
        if provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "openai_api_key (OPENAI_API_KEY) is required "
                "when model_provider is 'openai'."
            )

        # Base URL checks for self-hosted providers
        if provider in ("vllm", "ollama"):
            if not self.base_url:
                examples = {
                    "ollama": "http://localhost:11434/v1",
                    "vllm": "http://localhost:8000/v1",
                }
                raise ValueError(
                    f"base_url (PROCUREAI_BASE_URL) is required when model_provider "
                    f"is '{provider}'. Example: {examples[provider]}"
                )
            if not self.base_url.startswith(("http://", "https://")):
                raise ValueError(
                    f"base_url must start with http:// or https://, "
                    f"got: {self.base_url}"
                )

        return self


def get_chat_model(config: AgentConfig) -> BaseChatModel:
    """Return a LangChain chat model based on the config provider."""
    match config.model_provider:
        case "anthropic":
            return ChatAnthropic(
                model=config.model_name,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                api_key=config.anthropic_api_key.get_secret_value(),
            )
        case "openai":
            return ChatOpenAI(
                model=config.model_name,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                api_key=config.openai_api_key.get_secret_value(),
            )
        case "vllm" | "ollama":
            api_key = (
                config.openai_api_key.get_secret_value()
                if config.openai_api_key
                else "not-needed"
            )
            return ChatOpenAI(
                model=config.model_name,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                base_url=config.base_url,
                api_key=api_key,
            )
        case _:
            raise ValueError(f"Unsupported model provider: {config.model_provider}")
