#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/work/project}"
TEMPLATE_PATH="${TEMPLATE_PATH:-/opt/graphrag-offnet/templates/settings.models.yaml}"
GRAPHRAG_MODEL_BACKEND="${GRAPHRAG_MODEL_BACKEND:-ollama}"
GRAPHRAG_CHAT_MODEL="${GRAPHRAG_CHAT_MODEL:-}"
GRAPHRAG_EMBED_MODEL="${GRAPHRAG_EMBED_MODEL:-}"
GRAPHRAG_EMBED_DIM="${GRAPHRAG_EMBED_DIM:-}"
GRAPHRAG_CHUNK_SIZE="${GRAPHRAG_CHUNK_SIZE:-800}"
GRAPHRAG_CHUNK_OVERLAP="${GRAPHRAG_CHUNK_OVERLAP:-100}"
GRAPHRAG_MAX_GLEANINGS="${GRAPHRAG_MAX_GLEANINGS:-1}"
GRAPHRAG_COMMUNITY_MAX_INPUT="${GRAPHRAG_COMMUNITY_MAX_INPUT:-4000}"
GRAPHRAG_LLM_TIMEOUT="${GRAPHRAG_LLM_TIMEOUT:-2400}"
GRAPHRAG_EMBED_TIMEOUT="${GRAPHRAG_EMBED_TIMEOUT:-600}"
GRAPHRAG_LLM_MAX_RETRIES="${GRAPHRAG_LLM_MAX_RETRIES:-4}"
GRAPHRAG_EMBED_MAX_RETRIES="${GRAPHRAG_EMBED_MAX_RETRIES:-4}"
GRAPHRAG_RETRY_BASE_DELAY="${GRAPHRAG_RETRY_BASE_DELAY:-2}"
GRAPHRAG_RETRY_MAX_DELAY="${GRAPHRAG_RETRY_MAX_DELAY:-60}"
GRAPHRAG_CONCURRENT_REQUESTS="${GRAPHRAG_CONCURRENT_REQUESTS:-1}"

require_env() {
  local key="$1"
  if [ -z "${!key:-}" ]; then
    echo "Required environment variable is missing for ${GRAPHRAG_MODEL_BACKEND}: ${key}" >&2
    exit 2
  fi
}

configure_model_backend() {
  case "${GRAPHRAG_MODEL_BACKEND}" in
    ollama)
      GRAPHRAG_CHAT_MODEL="${GRAPHRAG_CHAT_MODEL:-gpt-oss:20b}"
      GRAPHRAG_EMBED_MODEL="${GRAPHRAG_EMBED_MODEL:-nomic-embed-text}"
      GRAPHRAG_EMBED_DIM="${GRAPHRAG_EMBED_DIM:-768}"
      GRAPHRAG_LLM_PROVIDER="${GRAPHRAG_LLM_PROVIDER:-ollama_chat}"
      GRAPHRAG_EMBED_PROVIDER="${GRAPHRAG_EMBED_PROVIDER:-ollama}"
      GRAPHRAG_LLM_API_BASE="${GRAPHRAG_LLM_API_BASE:-${OLLAMA_API_BASE:-${OLLAMA_BASE_URL:-http://ollama:11434}}}"
      GRAPHRAG_EMBED_API_BASE="${GRAPHRAG_EMBED_API_BASE:-${OLLAMA_API_BASE:-${OLLAMA_BASE_URL:-http://ollama:11434}}}"
      GRAPHRAG_LLM_API_KEY="${GRAPHRAG_LLM_API_KEY:-${GRAPHRAG_API_KEY:-ollama}}"
      GRAPHRAG_EMBED_API_KEY="${GRAPHRAG_EMBED_API_KEY:-${GRAPHRAG_API_KEY:-ollama}}"
      GRAPHRAG_LLM_API_VERSION="${GRAPHRAG_LLM_API_VERSION:-}"
      GRAPHRAG_EMBED_API_VERSION="${GRAPHRAG_EMBED_API_VERSION:-}"
      GRAPHRAG_LLM_DEPLOYMENT="${GRAPHRAG_LLM_DEPLOYMENT:-}"
      GRAPHRAG_EMBED_DEPLOYMENT="${GRAPHRAG_EMBED_DEPLOYMENT:-}"
      OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-${GRAPHRAG_LLM_API_BASE}}"
      OLLAMA_API_BASE="${OLLAMA_API_BASE:-${GRAPHRAG_LLM_API_BASE}}"
      ;;
    azure_openai)
      GRAPHRAG_LLM_PROVIDER="azure"
      GRAPHRAG_EMBED_PROVIDER="azure"
      require_env GRAPHRAG_LLM_API_BASE
      require_env GRAPHRAG_EMBED_API_BASE
      require_env GRAPHRAG_LLM_API_VERSION
      require_env GRAPHRAG_EMBED_API_VERSION
      require_env GRAPHRAG_LLM_DEPLOYMENT
      require_env GRAPHRAG_EMBED_DEPLOYMENT
      require_env GRAPHRAG_LLM_API_KEY
      require_env GRAPHRAG_EMBED_API_KEY
      require_env GRAPHRAG_CHAT_MODEL
      require_env GRAPHRAG_EMBED_MODEL
      require_env GRAPHRAG_EMBED_DIM
      ;;
    *)
      echo "Unsupported GRAPHRAG_MODEL_BACKEND=${GRAPHRAG_MODEL_BACKEND}; expected ollama or azure_openai." >&2
      exit 2
      ;;
  esac

  export PROJECT_ROOT TEMPLATE_PATH GRAPHRAG_MODEL_BACKEND
  export GRAPHRAG_LLM_PROVIDER GRAPHRAG_EMBED_PROVIDER
  export GRAPHRAG_LLM_API_BASE GRAPHRAG_EMBED_API_BASE
  export GRAPHRAG_LLM_API_VERSION GRAPHRAG_EMBED_API_VERSION
  export GRAPHRAG_LLM_DEPLOYMENT GRAPHRAG_EMBED_DEPLOYMENT
  export GRAPHRAG_LLM_API_KEY GRAPHRAG_EMBED_API_KEY
  export GRAPHRAG_CHAT_MODEL GRAPHRAG_EMBED_MODEL GRAPHRAG_EMBED_DIM
  export GRAPHRAG_CHUNK_SIZE GRAPHRAG_CHUNK_OVERLAP GRAPHRAG_MAX_GLEANINGS
  export GRAPHRAG_COMMUNITY_MAX_INPUT GRAPHRAG_LLM_TIMEOUT GRAPHRAG_EMBED_TIMEOUT
  export GRAPHRAG_LLM_MAX_RETRIES GRAPHRAG_EMBED_MAX_RETRIES
  export GRAPHRAG_RETRY_BASE_DELAY GRAPHRAG_RETRY_MAX_DELAY GRAPHRAG_CONCURRENT_REQUESTS
  if [ "${GRAPHRAG_MODEL_BACKEND}" = "ollama" ]; then
    export OLLAMA_BASE_URL OLLAMA_API_BASE
  fi
}

usage() {
  cat <<'EOF'
Usage:
  graphrag-offnet init
  graphrag-offnet smoke
  graphrag-offnet dry-run [graphrag index args]
  graphrag-offnet index [graphrag index args]
  graphrag-offnet query [graphrag query args]
  graphrag-offnet shell
  graphrag-offnet exec <command> [args]
  graphrag-offnet sleep

The GRAPHRAG_MODEL_BACKEND environment variable selects ollama or azure_openai.
EOF
}

write_project_env() {
  mkdir -p "${PROJECT_ROOT}"
  cat > "${PROJECT_ROOT}/.env" <<EOF
# Provider credentials and endpoints are injected into the container at runtime.
GRAPHRAG_CHUNK_SIZE=${GRAPHRAG_CHUNK_SIZE}
GRAPHRAG_CHUNK_OVERLAP=${GRAPHRAG_CHUNK_OVERLAP}
GRAPHRAG_MAX_GLEANINGS=${GRAPHRAG_MAX_GLEANINGS}
GRAPHRAG_COMMUNITY_MAX_INPUT=${GRAPHRAG_COMMUNITY_MAX_INPUT}
GRAPHRAG_LLM_TIMEOUT=${GRAPHRAG_LLM_TIMEOUT}
GRAPHRAG_EMBED_TIMEOUT=${GRAPHRAG_EMBED_TIMEOUT}
GRAPHRAG_LLM_MAX_RETRIES=${GRAPHRAG_LLM_MAX_RETRIES}
GRAPHRAG_EMBED_MAX_RETRIES=${GRAPHRAG_EMBED_MAX_RETRIES}
GRAPHRAG_RETRY_BASE_DELAY=${GRAPHRAG_RETRY_BASE_DELAY}
GRAPHRAG_RETRY_MAX_DELAY=${GRAPHRAG_RETRY_MAX_DELAY}
GRAPHRAG_CONCURRENT_REQUESTS=${GRAPHRAG_CONCURRENT_REQUESTS}
EOF
}

ensure_config() {
  if [ ! -f "${PROJECT_ROOT}/settings.yaml" ]; then
    echo "Missing ${PROJECT_ROOT}/settings.yaml. Run: graphrag-offnet init" >&2
    exit 2
  fi
}

wait_for_model_backend() {
  if [ "${GRAPHRAG_MODEL_BACKEND}" = "azure_openai" ]; then
    return 0
  fi

  local url="${GRAPHRAG_LLM_API_BASE%/}/api/version"
  local attempt=1
  while [ "${attempt}" -le 60 ]; do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
    attempt=$((attempt + 1))
  done
  echo "Ollama did not become reachable at ${url}" >&2
  exit 1
}

init_project() {
  mkdir -p "${PROJECT_ROOT}/input"
  if [ ! -d "${PROJECT_ROOT}/prompts" ]; then
    graphrag init --root "${PROJECT_ROOT}" --force --model gpt-4.1 --embedding text-embedding-3-small
  fi
  cp "${TEMPLATE_PATH}" "${PROJECT_ROOT}/settings.yaml"
  write_project_env
  echo "Initialized ${PROJECT_ROOT} for ${GRAPHRAG_MODEL_BACKEND}-backed Microsoft GraphRAG."
  echo "Put .txt, .md, .csv, .pdf, .docx, or .xlsx files in ${PROJECT_ROOT}/input, then run: graphrag-offnet index --method standard --verbose"
}

smoke() {
  wait_for_model_backend
  python - <<'PY'
import json
import os
import urllib.error
import urllib.parse
import urllib.request

backend = os.environ["GRAPHRAG_MODEL_BACKEND"]
chat_base = os.environ["GRAPHRAG_LLM_API_BASE"].rstrip("/")
embed_base = os.environ["GRAPHRAG_EMBED_API_BASE"].rstrip("/")
chat_model = os.environ["GRAPHRAG_CHAT_MODEL"]
embed_model = os.environ["GRAPHRAG_EMBED_MODEL"]
timeout = int(os.environ.get("GRAPHRAG_SMOKE_TIMEOUT", "120"))

def post(url, payload, headers):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{url} failed with HTTP {exc.code}: {body}") from exc

if backend == "ollama":
    chat_url = f"{chat_base}/v1/chat/completions"
    embed_url = f"{embed_base}/v1/embeddings"
    chat_headers = {"Authorization": f"Bearer {os.environ['GRAPHRAG_LLM_API_KEY']}"}
    embed_headers = {"Authorization": f"Bearer {os.environ['GRAPHRAG_EMBED_API_KEY']}"}
    chat_payload = {
        "model": chat_model,
        "messages": [{"role": "user", "content": "Reply with the word ok."}],
        "stream": False,
        "max_tokens": 32,
        "temperature": 0,
    }
    embed_payload = {"model": embed_model, "input": "GraphRAG provider smoke test"}
else:
    chat_deployment = urllib.parse.quote(os.environ["GRAPHRAG_LLM_DEPLOYMENT"], safe="")
    embed_deployment = urllib.parse.quote(os.environ["GRAPHRAG_EMBED_DEPLOYMENT"], safe="")
    chat_version = urllib.parse.quote(os.environ["GRAPHRAG_LLM_API_VERSION"], safe="")
    embed_version = urllib.parse.quote(os.environ["GRAPHRAG_EMBED_API_VERSION"], safe="")
    chat_url = f"{chat_base}/openai/deployments/{chat_deployment}/chat/completions?api-version={chat_version}"
    embed_url = f"{embed_base}/openai/deployments/{embed_deployment}/embeddings?api-version={embed_version}"
    chat_headers = {"api-key": os.environ["GRAPHRAG_LLM_API_KEY"]}
    embed_headers = {"api-key": os.environ["GRAPHRAG_EMBED_API_KEY"]}
    chat_payload = {
        "messages": [{"role": "user", "content": "Reply with the word ok."}],
        "max_tokens": 32,
        "temperature": 0,
    }
    embed_payload = {"input": "GraphRAG provider smoke test"}

chat = post(chat_url, chat_payload, chat_headers)
content = chat["choices"][0]["message"]["content"]
if not content.strip():
    raise RuntimeError("Chat model returned an empty response")

embedding = post(embed_url, embed_payload, embed_headers)
vector = embedding["data"][0]["embedding"]
if not isinstance(vector, list) or not vector:
    raise RuntimeError("Embedding model returned an empty vector")

expected_dim = int(os.environ["GRAPHRAG_EMBED_DIM"])
if len(vector) != expected_dim:
    raise RuntimeError(f"Embedding dimension mismatch: expected {expected_dim}, received {len(vector)}")

print(f"Model backend smoke passed: backend={backend}, chat={chat_model}, embedding={embed_model}, embedding_dim={len(vector)}")
PY
}

configure_model_backend

cmd="${1:-help}"
shift || true

case "${cmd}" in
  help|-h|--help)
    usage
    ;;
  init)
    init_project
    ;;
  smoke)
    smoke
    ;;
  dry-run)
    ensure_config
    wait_for_model_backend
    exec graphrag index --root "${PROJECT_ROOT}" --dry-run "$@"
    ;;
  index)
    ensure_config
    wait_for_model_backend
    exec graphrag index --root "${PROJECT_ROOT}" "$@"
    ;;
  query)
    ensure_config
    wait_for_model_backend
    exec graphrag query --root "${PROJECT_ROOT}" "$@"
    ;;
  shell)
    exec bash
    ;;
  exec)
    if [ "$#" -eq 0 ]; then
      echo "graphrag-offnet exec requires a command." >&2
      exit 2
    fi
    exec "$@"
    ;;
  sleep)
    exec sleep infinity
    ;;
  *)
    echo "Unknown command: ${cmd}" >&2
    usage >&2
    exit 2
    ;;
esac
