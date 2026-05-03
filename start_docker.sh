#!/usr/bin/env bash
# start_docker.sh — Start/stop the AllSpark Edge Server container (direct podman run).
#
# Image:  bcr2.inside.bosch.cloud/spf-ict/ict412_allspark-edge-server:latest
# Ports:  8080 (Edge API)   8081 (Control Plane UI)
#
# Usage
# -----
#   ./start_docker.sh              # start the container (detached)
#   ./start_docker.sh --stop       # stop and remove the container
#   ./start_docker.sh --restart    # stop then start
#   ./start_docker.sh --status     # show running state
#   ./start_docker.sh --logs       # tail container logs
#   ./start_docker.sh --foreground # run in foreground (see output live)
#
# Prerequisites
# -------------
#   - podman (or docker) available
#   - config.yaml present in the same directory as this script
#   - Image pulled:
#       podman pull bcr2.inside.bosch.cloud/spf-ict/ict412_allspark-edge-server:latest

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="bcr2.inside.bosch.cloud/spf-ict/ict412_allspark-edge-server:latest"
CONTAINER="allspark-edge-server"
PROXY="http://rb-proxy-sl.bosch.com:8080"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'
ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()     { echo -e "${RED}[ERROR]${NC} $*" >&2; }
section() { echo -e "\n${BOLD}${CYAN}===========================================${NC}";
            echo -e "${BOLD}${CYAN}  $*${NC}";
            echo -e "${BOLD}${CYAN}===========================================${NC}"; }

ACTION="start"
DETACH="-d"
for arg in "$@"; do
  case "$arg" in
    --stop)       ACTION="stop" ;;
    --restart)    ACTION="restart" ;;
    --status)     ACTION="status" ;;
    --logs)       ACTION="logs" ;;
    --foreground) DETACH="" ;;
    -h|--help)    sed -n '3,21p' "$0"; exit 0 ;;
    *) err "Unknown flag: $arg  (use --help)"; exit 1 ;;
  esac
done

_check() {
  if [[ ! -f "${SCRIPT_DIR}/config.yaml" ]]; then
    err "config.yaml not found at ${SCRIPT_DIR}"
    err "Deploy it first:  build_all.sh --deploy-only --only=edge  (on dev machine)"
    exit 1
  fi
  mkdir -p "${SCRIPT_DIR}/uploads" "${SCRIPT_DIR}/logs"
}

_stop() {
  info "Stopping ${CONTAINER} ..."
  podman stop "${CONTAINER}" 2>/dev/null || true
  podman rm   "${CONTAINER}" 2>/dev/null || true
  fuser -k 9080/tcp 2>/dev/null || true
  fuser -k 9081/tcp 2>/dev/null || true
  ok "Stopped"
}

_start() {
  _check
  # Remove any existing container with the same name before creating a new one
  podman stop "${CONTAINER}" 2>/dev/null || true
  podman rm   "${CONTAINER}" 2>/dev/null || true
  section "Starting AllSpark Edge Server"
  info "Image  : ${IMAGE}"
  info "Config : ${SCRIPT_DIR}/config.yaml"
  podman run ${DETACH} \
    --name "${CONTAINER}" \
    --security-opt label=disable \
    -v "${SCRIPT_DIR}/config.yaml:/app/python/config.yaml:ro" \
    -v "${SCRIPT_DIR}/uploads:/app/uploads" \
    -v "${SCRIPT_DIR}/logs:/app/logs" \
    -v "/net/htvvm662/fs0/anomaly_events:/net/htvvm662/fs0/anomaly_events:rw" \
    -p 9080:8080 \
    -p 9081:8081 \
    -p 9090:9090 \
    -p 9876:9876 \
    -e http_proxy="${PROXY}" \
    -e https_proxy="${PROXY}" \
    -e HTTP_PROXY="${PROXY}" \
    -e HTTPS_PROXY="${PROXY}" \
    -e no_proxy="localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,host.containers.internal" \
    -e NO_PROXY="localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,host.containers.internal" \
    "${IMAGE}"
  if [[ -n "${DETACH}" ]]; then
    ok "${CONTAINER} started"
    info "Edge API      : http://$(hostname -I | awk '{print $1}'):9080"
    info "Control Plane : http://$(hostname -I | awk '{print $1}'):9081"
    info "Logs : ./start_docker.sh --logs    Stop : ./start_docker.sh --stop"
  fi
}

case "$ACTION" in
  start)   _start ;;
  stop)    _stop ;;
  restart) _stop; _start ;;
  status)  podman ps --filter "name=${CONTAINER}" ;;
  logs)    podman logs -f "${CONTAINER}" ;;
esac
