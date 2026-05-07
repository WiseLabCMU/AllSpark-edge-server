#!/usr/bin/env bash
set -euo pipefail

# Parse flags
USE_PROXY=true
PUSH=true
DEPLOY=true
NTID="add5kor"
for arg in "$@"; do
  case "$arg" in
    --no-proxy)   USE_PROXY=false ;;
    --no-push)    PUSH=false ;;
    --no-deploy)  DEPLOY=false ;;
    --ntid=*)     NTID="${arg#--ntid=}" ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if $USE_PROXY; then
  # Source .bashrc to pick up proxy vars (HTTP_PROXY etc. pointing to localhost:3128 Cntlm).
  # shellcheck source=/dev/null
  [[ -f "$HOME/.bashrc" ]] && source "$HOME/.bashrc"
fi

# Build via docker compose — network: host lets apt-get and pip reach Cntlm on localhost:3128.
if $USE_PROXY; then
  docker compose \
    -f "${SCRIPT_DIR}/docker-compose.yml" \
    build \
    --build-arg HTTP_PROXY="${HTTP_PROXY:-}" \
    --build-arg HTTPS_PROXY="${HTTPS_PROXY:-${HTTP_PROXY:-}}" \
    --build-arg http_proxy="${http_proxy:-${HTTP_PROXY:-}}" \
    --build-arg https_proxy="${https_proxy:-${HTTPS_PROXY:-${HTTP_PROXY:-}}}" \
    --build-arg NO_PROXY="${NO_PROXY:-}" \
    --build-arg no_proxy="${no_proxy:-${NO_PROXY:-}}"
else
  # No-proxy build: pass empty strings to clear any proxy vars inside the image
  docker compose \
    -f "${SCRIPT_DIR}/docker-compose.yml" \
    build \
    --build-arg HTTP_PROXY="" \
    --build-arg HTTPS_PROXY="" \
    --build-arg http_proxy="" \
    --build-arg https_proxy="" \
    --build-arg NO_PROXY="" \
    --build-arg no_proxy=""
fi

# Push to registry
if $PUSH; then
  ENV_FILE="${SCRIPT_DIR}/.env.container_registry"
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: credentials file not found: $ENV_FILE" >&2
    echo "       Copy .env.container_registry.example to .env.container_registry and fill in credentials." >&2
    exit 1
  fi

  # Parse credentials without shell expansion (safe for passwords containing $ or special chars)
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" == \#* ]] && continue
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    export "$key=$value"
  done < "$ENV_FILE"

  if [[ -z "${REGISTRY_USERNAME:-}" ]] || [[ -z "${REGISTRY_PASSWORD:-}" ]]; then
    echo "ERROR: REGISTRY_USERNAME and REGISTRY_PASSWORD must be set in $ENV_FILE" >&2
    exit 1
  fi

  REGISTRY="bcr2.inside.bosch.cloud"
  IMAGE="bcr2.inside.bosch.cloud/spf-ict/ict412_allspark-edge-server:hatvan_v0"

  echo "${REGISTRY_PASSWORD}" | docker login "${REGISTRY}" \
    --username "${REGISTRY_USERNAME}" \
    --password-stdin

  docker push "${IMAGE}"
  echo "Pushed: ${IMAGE}"
fi

# Deploy essential files to remote machine via SCP
if $DEPLOY; then
  REMOTE_HOST="htvvm662.emea.bosch.com"
  REMOTE_USER="rbadmin_app1"
  JUMP_HOST="rb-psmp.bosch.com"
  REMOTE_DIR="/local/home/rbadmin_app1/allspark-edge"

  echo "Deploying files to ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR} ..."

  # Ensure remote directory exists
  ssh -o "ProxyJump ${NTID}@${JUMP_HOST}" \
      "${REMOTE_USER}@${REMOTE_HOST}" \
      "mkdir -p ${REMOTE_DIR}"

  echo "Copying files to ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR} ..."
  scp -o "ProxyJump ${NTID}@${JUMP_HOST}" \
      "${SCRIPT_DIR}/docker-compose.yml" \
      "${SCRIPT_DIR}/python/config.yaml" \
      "${SCRIPT_DIR}/DOCKER_README.md" \
      "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"

  echo "Deployed docker-compose.yml, config.yaml, and DOCKER_README.md to ${REMOTE_HOST}:${REMOTE_DIR}"
fi
