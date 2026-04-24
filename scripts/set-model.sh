#!/usr/bin/env bash
# Configure .env for a specific model provider and model name.
#
# Usage:
#   ./scripts/set-model.sh anthropic claude-sonnet-4-6
#   ./scripts/set-model.sh anthropic claude-haiku-4-5
#   ./scripts/set-model.sh ollama qwen2.5:7b
#   ./scripts/set-model.sh vllm Qwen/Qwen2.5-7B-Instruct http://localhost:8000/v1
#
# Provider presets:
#   anthropic  — uses ANTHROPIC_API_KEY (must already be in .env)
#   openai     — uses OPENAI_API_KEY (must already be in .env)
#   ollama     — defaults to http://localhost:11434/v1
#   vllm       — requires base_url as 3rd arg

set -euo pipefail

ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <provider> <model-name> [base-url]"
  echo ""
  echo "Providers: anthropic, openai, ollama, vllm"
  echo ""
  echo "Examples:"
  echo "  $0 anthropic claude-sonnet-4-6"
  echo "  $0 ollama qwen2.5:7b"
  echo "  $0 vllm Qwen/Qwen2.5-7B-Instruct http://localhost:8000/v1"
  exit 1
fi

PROVIDER="$1"
MODEL="$2"
BASE_URL="${3:-}"

# Validate provider
case "$PROVIDER" in
  anthropic|openai|ollama|vllm) ;;
  *)
    echo "ERROR: Unknown provider '$PROVIDER'. Must be: anthropic, openai, ollama, vllm"
    exit 1
    ;;
esac

# Default base URLs
if [[ "$PROVIDER" == "ollama" && -z "$BASE_URL" ]]; then
  BASE_URL="http://localhost:11434/v1"
fi

if [[ "$PROVIDER" == "vllm" && -z "$BASE_URL" ]]; then
  echo "ERROR: vllm provider requires a base_url (3rd argument)"
  exit 1
fi

# Read existing .env to preserve API keys and other settings
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: No .env file found at $ENV_FILE"
  exit 1
fi

# Extract existing API keys (preserve them)
ANTHROPIC_KEY=$(grep -E '^ANTHROPIC_API_KEY=' "$ENV_FILE" 2>/dev/null | head -1 || true)
OPENAI_KEY=$(grep -E '^OPENAI_API_KEY=' "$ENV_FILE" 2>/dev/null | head -1 || true)

# Rebuild .env
{
  [[ -n "$ANTHROPIC_KEY" ]] && echo "$ANTHROPIC_KEY"
  [[ -n "$OPENAI_KEY" ]] && echo "$OPENAI_KEY"
  echo "PROCUREAI_MODEL_PROVIDER=${PROVIDER}"
  echo "PROCUREAI_MODEL_NAME=${MODEL}"
  [[ -n "$BASE_URL" ]] && echo "PROCUREAI_BASE_URL=${BASE_URL}"
  echo ""
  echo "# --- Optional tuning ---"
  echo "# PROCUREAI_TEMPERATURE=0.0"
  echo "# PROCUREAI_MAX_TOKENS=4096"
} > "$ENV_FILE"

echo "✓ .env updated:"
echo "  Provider:  $PROVIDER"
echo "  Model:     $MODEL"
if [[ -n "$BASE_URL" ]]; then
  echo "  Base URL:  $BASE_URL"
fi
