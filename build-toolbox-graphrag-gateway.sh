#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

ENV_FILE="${ENV_FILE:-.env.build}"
ENV_TEMPLATE="${ENV_TEMPLATE:-.env.build.example}"
if [ ! -f "${ENV_FILE}" ]; then
  cp "${ENV_TEMPLATE}" "${ENV_FILE}"
  echo "Created ${ENV_FILE} from ${ENV_TEMPLATE}. Set the Python base image, then rerun." >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
. "${ENV_FILE}"
set +a

DOCKER_BIN="${DOCKER_BIN:-docker}"
DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
TOOLBOX_GRAPHRAG_VERSION="${TOOLBOX_GRAPHRAG_VERSION:-0.1.0}"
PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.11-slim-bookworm}"
GRAPHRAG_VERSION="${GRAPHRAG_VERSION:-3.0.9}"
TOOLBOX_GRAPHRAG_RUNTIME_IMAGE="${TOOLBOX_GRAPHRAG_RUNTIME_IMAGE:-toolbox/graphrag-offnet:${TOOLBOX_GRAPHRAG_VERSION}}"
TOOLBOX_GRAPHRAG_GATEWAY_IMAGE="${TOOLBOX_GRAPHRAG_GATEWAY_IMAGE:-toolbox/graphrag-gateway:${TOOLBOX_GRAPHRAG_VERSION}}"
LITELLM_VERSION="${LITELLM_VERSION:-1.83.10}"
PIP_VERSION="${PIP_VERSION:-25.3}"
SETUPTOOLS_VERSION="${SETUPTOOLS_VERSION:-82.0.1}"
WHEEL_VERSION="${WHEEL_VERSION:-0.46.2}"
PYARROW_VERSION="${PYARROW_VERSION:-23.0.1}"
CRYPTOGRAPHY_VERSION="${CRYPTOGRAPHY_VERSION:-46.0.6}"
MAMMOTH_VERSION="${MAMMOTH_VERSION:-1.12.0}"
OPENPYXL_VERSION="${OPENPYXL_VERSION:-3.1.5}"
FASTAPI_VERSION="${FASTAPI_VERSION:-0.115.6}"
UVICORN_VERSION="${UVICORN_VERSION:-0.34.0}"
PYTHON_MULTIPART_VERSION="${PYTHON_MULTIPART_VERSION:-0.0.20}"
OUTPUT_DIR="${OUTPUT_DIR:-bundle/toolbox-ansible}"
GATEWAY_TAR_NAME="${GATEWAY_TAR_NAME:-graphrag_gateway_${TOOLBOX_GRAPHRAG_VERSION}.tar}"
RUNTIME_TAR_NAME="${RUNTIME_TAR_NAME:-graphrag_runtime_${TOOLBOX_GRAPHRAG_VERSION}.tar}"
SAVE_RUNTIME_TAR="${SAVE_RUNTIME_TAR:-false}"

is_enabled() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|y|Y|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

if ! command -v "${DOCKER_BIN}" >/dev/null 2>&1; then
  echo "Docker was not found on PATH. Set DOCKER_BIN or install Docker." >&2
  exit 127
fi

mkdir -p "${OUTPUT_DIR}"

build_args=(
  --platform "${DOCKER_PLATFORM}"
  --build-arg "PYTHON_IMAGE=${PYTHON_IMAGE}"
  --build-arg "GRAPHRAG_VERSION=${GRAPHRAG_VERSION}"
  --build-arg "LITELLM_VERSION=${LITELLM_VERSION}"
  --build-arg "PIP_VERSION=${PIP_VERSION}"
  --build-arg "SETUPTOOLS_VERSION=${SETUPTOOLS_VERSION}"
  --build-arg "WHEEL_VERSION=${WHEEL_VERSION}"
  --build-arg "PYARROW_VERSION=${PYARROW_VERSION}"
  --build-arg "CRYPTOGRAPHY_VERSION=${CRYPTOGRAPHY_VERSION}"
  --build-arg "MAMMOTH_VERSION=${MAMMOTH_VERSION}"
  --build-arg "OPENPYXL_VERSION=${OPENPYXL_VERSION}"
)
[ -z "${HTTP_PROXY:-}" ] || build_args+=(--build-arg "HTTP_PROXY=${HTTP_PROXY}" --build-arg "http_proxy=${HTTP_PROXY}")
[ -z "${HTTPS_PROXY:-}" ] || build_args+=(--build-arg "HTTPS_PROXY=${HTTPS_PROXY}" --build-arg "https_proxy=${HTTPS_PROXY}")
[ -z "${NO_PROXY:-}" ] || build_args+=(--build-arg "NO_PROXY=${NO_PROXY}" --build-arg "no_proxy=${NO_PROXY}")

echo "Building GraphRAG runtime image with Docker: ${TOOLBOX_GRAPHRAG_RUNTIME_IMAGE} (${DOCKER_PLATFORM})"
"${DOCKER_BIN}" build \
  "${build_args[@]}" \
  -t "${TOOLBOX_GRAPHRAG_RUNTIME_IMAGE}" \
  -f Containerfile .

echo "Verifying GraphRAG runtime hardening: ${TOOLBOX_GRAPHRAG_RUNTIME_IMAGE}"
"${DOCKER_BIN}" run --rm \
  --entrypoint graphrag-verify-hardening \
  "${TOOLBOX_GRAPHRAG_RUNTIME_IMAGE}"

echo "Building GraphRAG Gateway image with Docker: ${TOOLBOX_GRAPHRAG_GATEWAY_IMAGE}"
"${DOCKER_BIN}" build \
  --platform "${DOCKER_PLATFORM}" \
  --build-arg "GATEWAY_BASE_IMAGE=${TOOLBOX_GRAPHRAG_RUNTIME_IMAGE}" \
  --build-arg "FASTAPI_VERSION=${FASTAPI_VERSION}" \
  --build-arg "UVICORN_VERSION=${UVICORN_VERSION}" \
  --build-arg "PYTHON_MULTIPART_VERSION=${PYTHON_MULTIPART_VERSION}" \
  -t "${TOOLBOX_GRAPHRAG_GATEWAY_IMAGE}" \
  -f Containerfile.gateway .

image_platform="$("${DOCKER_BIN}" image inspect --format '{{.Os}}/{{.Architecture}}' "${TOOLBOX_GRAPHRAG_GATEWAY_IMAGE}")"
expected_arch="${DOCKER_PLATFORM#linux/}"
if [ "${image_platform}" != "linux/${expected_arch}" ]; then
  echo "Gateway image platform mismatch: expected linux/${expected_arch}, found ${image_platform}." >&2
  exit 1
fi

echo "Saving Docker gateway image archive: ${OUTPUT_DIR}/${GATEWAY_TAR_NAME}"
"${DOCKER_BIN}" save \
  -o "${OUTPUT_DIR}/${GATEWAY_TAR_NAME}" \
  "${TOOLBOX_GRAPHRAG_GATEWAY_IMAGE}"

echo "Verifying gateway archive tag: ${TOOLBOX_GRAPHRAG_GATEWAY_IMAGE}"
python3 - "${OUTPUT_DIR}/${GATEWAY_TAR_NAME}" "${TOOLBOX_GRAPHRAG_GATEWAY_IMAGE}" <<'PY'
import json
import sys
import tarfile

archive_path, expected_image = sys.argv[1:3]
repository, tag = expected_image.rsplit(":", 1)
with tarfile.open(archive_path, "r") as archive:
    repositories_file = archive.extractfile("repositories")
    if repositories_file is None:
        raise SystemExit(f"{archive_path} does not contain a repositories manifest")
    repositories = json.load(repositories_file)

if tag not in repositories.get(repository, {}):
    raise SystemExit(f"{archive_path} does not contain expected image tag {expected_image}")
PY

if is_enabled "${SAVE_RUNTIME_TAR}"; then
  echo "Saving optional runtime image archive: ${OUTPUT_DIR}/${RUNTIME_TAR_NAME}"
  "${DOCKER_BIN}" save \
    -o "${OUTPUT_DIR}/${RUNTIME_TAR_NAME}" \
    "${TOOLBOX_GRAPHRAG_RUNTIME_IMAGE}"
fi

if command -v sha256sum >/dev/null 2>&1; then
  (
    cd "${OUTPUT_DIR}"
    sha256sum "${GATEWAY_TAR_NAME}" > "${GATEWAY_TAR_NAME}.sha256"
  )
else
  (
    cd "${OUTPUT_DIR}"
    shasum -a 256 "${GATEWAY_TAR_NAME}" > "${GATEWAY_TAR_NAME}.sha256"
  )
fi

cat > "${OUTPUT_DIR}/manifest.txt" <<EOF
Created: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Purpose: Toolbox Ansible GraphRAG Gateway sidecar
Build engine: Docker
Target platform: ${image_platform}
GraphRAG runtime image: ${TOOLBOX_GRAPHRAG_RUNTIME_IMAGE}
GraphRAG Gateway image: ${TOOLBOX_GRAPHRAG_GATEWAY_IMAGE}
GraphRAG package version: ${GRAPHRAG_VERSION}
LiteLLM override: ${LITELLM_VERSION}
PyArrow override: ${PYARROW_VERSION:-GraphRAG default}
cryptography override: ${CRYPTOGRAPHY_VERSION:-GraphRAG default}
Mammoth override: ${MAMMOTH_VERSION}
openpyxl override: ${OPENPYXL_VERSION}

Required installer artifacts:
- ${GATEWAY_TAR_NAME}
- ${GATEWAY_TAR_NAME}.sha256

Optional scan artifact:
- ${RUNTIME_TAR_NAME}
EOF

echo "Toolbox GraphRAG Gateway Docker archive is ready."
echo "Copy ${OUTPUT_DIR}/${GATEWAY_TAR_NAME} and its checksum to S3 or the installer staging containers directory."
