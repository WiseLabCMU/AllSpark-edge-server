#!/usr/bin/env bash
# AllSpark Stack Launcher
# Starts: Mosquitto MQTT broker, AllSpark Agents (adk web), Edge Server + Control Plane
#
# Usage:
#   ./start_allspark.sh           # start full stack
#   ./start_allspark.sh --stop    # stop all managed processes
#   ./start_allspark.sh --status  # show service health

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDGE_DIR="$SCRIPT_DIR"
AGENT_DIR="$(realpath "$SCRIPT_DIR/../allspark-agents")"
LOG_DIR="$EDGE_DIR/logs/startup"
PID_FILE="$EDGE_DIR/.allspark.pids"

mkdir -p "$LOG_DIR"

# --------------------------------------------------------------------------- #
# Colours
# --------------------------------------------------------------------------- #
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; }

print_header() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}========================================${NC}"
}

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
wait_for_port() {
    local port=$1 label=$2 timeout=${3:-30}
    local i=0
    while ! nc -z localhost "$port" 2>/dev/null; do
        i=$((i+1))
        if [[ $i -ge $timeout ]]; then
            warn "$label did not become ready on port $port within ${timeout}s"
            return 1
        fi
        sleep 1
    done
    ok "$label is up on port $port"
}

save_pid() { echo "$1:$2" >> "$PID_FILE"; }

# --------------------------------------------------------------------------- #
# Stop
# --------------------------------------------------------------------------- #
do_stop() {
    print_header "Stopping AllSpark Stack"
    if [[ ! -f "$PID_FILE" ]]; then
        warn "No PID file found — nothing to stop."
        return
    fi
    while IFS=: read -r label pid; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" && ok "Stopped $label (PID $pid)" || warn "Could not stop $label (PID $pid)"
        else
            info "$label (PID $pid) is not running"
        fi
    done < "$PID_FILE"

    # Stop mosquitto if we started it
    if command -v mosquitto &>/dev/null; then
        pkill -x mosquitto 2>/dev/null && ok "Stopped mosquitto" || true
    fi

    rm -f "$PID_FILE"
    ok "Done."
}

# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
do_status() {
    print_header "AllSpark Stack Status"
    local services=("8000:ADK Agent Framework" "8080:Edge API Server" "8081:Control Plane")
    for entry in "${services[@]}"; do
        local port="${entry%%:*}" label="${entry#*:}"
        if nc -z localhost "$port" 2>/dev/null; then
            ok "$label  →  http://localhost:$port"
        else
            warn "$label  →  not reachable on port $port"
        fi
    done

    echo ""
    if systemctl is-active --quiet mosquitto 2>/dev/null || pgrep -x mosquitto &>/dev/null; then
        ok "Mosquitto MQTT broker  →  port 1883"
    else
        warn "Mosquitto MQTT broker  →  not running"
    fi
}

# --------------------------------------------------------------------------- #
# 1. Mosquitto
# --------------------------------------------------------------------------- #
start_mosquitto() {
    print_header "Step 1 — Mosquitto MQTT Broker"

    if ! command -v mosquitto &>/dev/null; then
        info "mosquitto not found — installing ..."
        if command -v apt-get &>/dev/null; then
            sudo apt-get update -qq
            sudo apt-get install -y --no-install-recommends mosquitto
        elif command -v brew &>/dev/null; then
            brew install mosquitto
        else
            err "Cannot install mosquitto automatically. Please install it manually and re-run."
            exit 1
        fi
    fi

    if systemctl is-active --quiet mosquitto 2>/dev/null; then
        ok "mosquitto already running via systemd"
    elif pgrep -x mosquitto &>/dev/null; then
        ok "mosquitto already running"
    else
        info "Starting mosquitto in background ..."
        mosquitto -d -p 1883
        sleep 1
        if pgrep -x mosquitto &>/dev/null; then
            ok "mosquitto started"
        else
            err "mosquitto failed to start — check system logs"
            exit 1
        fi
    fi
}

# --------------------------------------------------------------------------- #
# 2. AllSpark Agents (adk web)
# --------------------------------------------------------------------------- #
start_agents() {
    print_header "Step 2 — AllSpark Agents (adk web, port 8000)"

    if [[ ! -d "$AGENT_DIR" ]]; then
        err "allspark-agents directory not found at: $AGENT_DIR"
        exit 1
    fi

    # Detect runtime: conda env → poetry → plain python
    AGENT_LOG="$LOG_DIR/agents.log"

    if conda env list 2>/dev/null | grep -q "allspark_agent_env"; then
        info "Using conda environment: allspark_agent_env"
        # shellcheck disable=SC1090
        CONDA_BASE="$(conda info --base 2>/dev/null)"
        source "$CONDA_BASE/etc/profile.d/conda.sh"
        conda activate allspark_agent_env
        (
            cd "$AGENT_DIR"
            adk web > "$AGENT_LOG" 2>&1
        ) &
    elif command -v poetry &>/dev/null && [[ -f "$AGENT_DIR/pyproject.toml" ]]; then
        info "Using Poetry"
        (
            cd "$AGENT_DIR"
            poetry run adk web > "$AGENT_LOG" 2>&1
        ) &
    else
        err "Neither conda env 'allspark_agent_env' nor poetry found."
        err "Run $AGENT_DIR/install_requirements.sh first."
        exit 1
    fi

    save_pid "adk-web" "$!"
    info "Waiting for ADK to be ready ..."
    wait_for_port 8000 "ADK Agent Framework" 60
}

# --------------------------------------------------------------------------- #
# 3. Edge Server + Control Plane (main.py starts both)
# --------------------------------------------------------------------------- #
start_edge() {
    print_header "Step 3 — Edge Server + Control Plane (ports 8080 / 8081)"

    VENV="$EDGE_DIR/venv"
    if [[ ! -f "$VENV/bin/activate" ]]; then
        err "Python venv not found at $VENV — run ./install.sh first."
        exit 1
    fi

    # Ensure config.yaml exists
    if [[ ! -f "$EDGE_DIR/python/config.yaml" ]]; then
        warn "python/config.yaml not found — server will use built-in defaults."
    fi

    EDGE_LOG="$LOG_DIR/edge.log"
    # shellcheck source=/dev/null
    source "$VENV/bin/activate"
    (
        cd "$EDGE_DIR"
        python main.py > "$EDGE_LOG" 2>&1
    ) &
    save_pid "edge-server" "$!"

    info "Waiting for Edge API ..."
    wait_for_port 8080 "Edge API Server" 30
    info "Waiting for Control Plane ..."
    wait_for_port 8081 "Control Plane" 30
}

# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
CMD="${1:-}"

case "$CMD" in
    --stop)
        do_stop
        exit 0
        ;;
    --status)
        do_status
        exit 0
        ;;
    "")
        : # fall through to start
        ;;
    *)
        echo "Usage: $0 [--stop|--status]"
        exit 1
        ;;
esac

# Clean up stale PID file before a fresh start
rm -f "$PID_FILE"

print_header "Starting AllSpark Stack"
start_mosquitto
start_agents
start_edge

echo ""
print_header "All Services Running"
echo ""
ok "MQTT Broker      →  localhost:1883"
ok "ADK Agent UI     →  http://localhost:8000/dev-ui/"
ok "Edge API         →  http://localhost:8080"
ok "Control Plane    →  http://localhost:8081/agent"
echo ""
info "Logs:  $LOG_DIR/"
info "Stop:  $0 --stop"
info "Check: $0 --status"
echo ""
