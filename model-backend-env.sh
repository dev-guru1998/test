#!/usr/bin/env bash

# Normalize and validate the host-side runtime contract before Docker Compose or
# Podman creates a GraphRAG container. The in-image graphrag-offnet entrypoint
# independently validates the same backend contract.
configure_graphrag_runtime_env() {
  GRAPHRAG_MODEL_BACKEND="${GRAPHRAG_MODEL_BACKEND:-ollama}"

  case "${GRAPHRAG_MODEL_BACKEND}" in
    ollama)
      GRAPHRAG_LLM_PROVIDER="${GRAPHRAG_LLM_PROVIDER:-ollama_chat}"
      GRAPHRAG_EMBED_PROVIDER="${GRAPHRAG_EMBED_PROVIDER:-ollama}"
      GRAPHRAG_LLM_API_BASE="${GRAPHRAG_LLM_API_BASE:-${OLLAMA_BASE_URL:-http://ollama:11434}}"
      GRAPHRAG_EMBED_API_BASE="${GRAPHRAG_EMBED_API_BASE:-${OLLAMA_BASE_URL:-http://ollama:11434}}"
      GRAPHRAG_LLM_API_VERSION="${GRAPHRAG_LLM_API_VERSION:-}"
      GRAPHRAG_EMBED_API_VERSION="${GRAPHRAG_EMBED_API_VERSION:-}"
      GRAPHRAG_LLM_DEPLOYMENT="${GRAPHRAG_LLM_DEPLOYMENT:-}"
      GRAPHRAG_EMBED_DEPLOYMENT="${GRAPHRAG_EMBED_DEPLOYMENT:-}"
      GRAPHRAG_LLM_API_KEY="${GRAPHRAG_LLM_API_KEY:-ollama}"
      GRAPHRAG_EMBED_API_KEY="${GRAPHRAG_EMBED_API_KEY:-ollama}"
      GRAPHRAG_CHAT_MODEL="${GRAPHRAG_CHAT_MODEL:-gpt-oss:20b}"
      GRAPHRAG_EMBED_MODEL="${GRAPHRAG_EMBED_MODEL:-nomic-embed-text}"
      GRAPHRAG_EMBED_DIM="${GRAPHRAG_EMBED_DIM:-768}"
      ;;
    azure_openai)
      GRAPHRAG_LLM_PROVIDER="azure"
      GRAPHRAG_EMBED_PROVIDER="azure"
      local key
      for key in \
        GRAPHRAG_LLM_API_BASE GRAPHRAG_EMBED_API_BASE \
        GRAPHRAG_LLM_API_VERSION GRAPHRAG_EMBED_API_VERSION \
        GRAPHRAG_LLM_DEPLOYMENT GRAPHRAG_EMBED_DEPLOYMENT \
        GRAPHRAG_LLM_API_KEY GRAPHRAG_EMBED_API_KEY \
        GRAPHRAG_CHAT_MODEL GRAPHRAG_EMBED_MODEL GRAPHRAG_EMBED_DIM; do
        if [ -z "${!key:-}" ]; then
          echo "Required environment variable is missing for azure_openai: ${key}" >&2
          return 2
        fi
      done
      ;;
    *)
      echo "Unsupported GRAPHRAG_MODEL_BACKEND=${GRAPHRAG_MODEL_BACKEND}; expected ollama or azure_openai." >&2
      return 2
      ;;
  esac

  export GRAPHRAG_MODEL_BACKEND GRAPHRAG_LLM_PROVIDER GRAPHRAG_EMBED_PROVIDER
  export GRAPHRAG_LLM_API_BASE GRAPHRAG_EMBED_API_BASE
  export GRAPHRAG_LLM_API_VERSION GRAPHRAG_EMBED_API_VERSION
  export GRAPHRAG_LLM_DEPLOYMENT GRAPHRAG_EMBED_DEPLOYMENT
  export GRAPHRAG_LLM_API_KEY GRAPHRAG_EMBED_API_KEY
  export GRAPHRAG_CHAT_MODEL GRAPHRAG_EMBED_MODEL GRAPHRAG_EMBED_DIM
}
