
#!/usr/bin/env bash
# AllSpark Edge Server — unified installer
# Usage:
#   ./install.sh                      # native (venv) install
#   ./install.sh --docker             # Docker install (uses Bosch proxy by default)
#   ./install.sh --docker --no-proxy  # Docker install without proxy
#   ./install.sh --both               # native + Docker install
#   ./install.sh --both  --no-proxy   # native + Docker install without proxy

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Parse arguments — collect mode and optional flags
MODE=""
USE_PROXY=1  # default: route Docker build through Bosch proxy

for arg in "$@"; do
    case "$arg" in
        --no-proxy) USE_PROXY=0 ;;
        --native|--docker|--both) MODE="$arg" ;;
        *)
            echo "Unknown argument: $arg"
            echo "Usage: $0 [--native|--docker|--both] [--no-proxy]"
            exit 1
            ;;
    esac
done

print_header() {
    echo ""
    echo "========================================"
    echo "  $1"
    echo "========================================"
}

# --------------------------------------------------------------------------- #
# Native install
# --------------------------------------------------------------------------- #
install_native() {
    print_header "Native Install"

    # Prerequisites check
    if ! command -v python3 &>/dev/null; then
        echo "ERROR: python3 is not installed. Please install Python 3 and re-run." >&2
        exit 1
    fi

    if ! command -v openssl &>/dev/null; then
        echo "WARNING: openssl not found — skipping SSL certificate generation."
        SKIP_SSL=1
    else
        SKIP_SSL=0
    fi

    # Install C build tools required by netifaces and other native extensions
    if command -v apt-get &>/dev/null; then
        echo "Installing system build dependencies (apt) ..."
        sudo apt-get update -qq
        sudo apt-get install -y --no-install-recommends gcc build-essential python3-dev libffi-dev
    elif command -v dnf &>/dev/null; then
        echo "Installing system build dependencies (dnf) ..."
        sudo dnf install -y gcc python3-devel libffi-devel
    elif command -v yum &>/dev/null; then
        echo "Installing system build dependencies (yum) ..."
        sudo yum install -y gcc python3-devel libffi-devel
    else
        echo "WARNING: Could not detect a supported package manager (apt/dnf/yum)."
        echo "  Please install gcc and Python dev headers manually, then re-run."
    fi

    echo "Creating virtual environment in ./venv ..."
    python3 -m venv venv
    # shellcheck source=/dev/null
    source venv/bin/activate

    echo "Installing Python dependencies ..."
    pip install --upgrade pip --quiet
    pip install -r python/requirements.txt

    # SSL certificates (one-time)
    if [[ "$SKIP_SSL" -eq 0 && ! -f keys/test-public.crt ]]; then
        echo "Generating self-signed SSL certificate in ./keys/ ..."
        mkdir -p keys
        openssl req \
            -new \
            -newkey rsa:2048 \
            -days 365 \
            -nodes \
            -x509 \
            -subj "/CN=localhost" \
            -keyout keys/test-private.key \
            -out keys/test-public.crt
        echo "SSL certificate generated."
    else
        echo "SSL certificate already exists — skipping."
    fi

    # Runtime directories
    mkdir -p uploads/mobile_clients uploads/agent_responses logs/anomalies logs/data/datacapture-rig

    echo ""
    echo "Native install complete."
    echo "To start the server manually:"
    echo "  source venv/bin/activate && python main.py"
}

# --------------------------------------------------------------------------- #
# Docker install
# --------------------------------------------------------------------------- #
install_docker() {
    print_header "Docker Install"

    if ! command -v docker &>/dev/null; then
        echo "ERROR: docker is not installed. Please install Docker and re-run." >&2
        exit 1
    fi

    # Accept both 'docker compose' (v2) and 'docker-compose' (v1)
    if docker compose version &>/dev/null 2>&1; then
        COMPOSE_CMD="docker compose"
    elif command -v docker-compose &>/dev/null; then
        COMPOSE_CMD="docker-compose"
    else
        echo "ERROR: Neither 'docker compose' (v2) nor 'docker-compose' (v1) found." >&2
        exit 1
    fi

    # Runtime directories expected by the volume mounts
    mkdir -p uploads/mobile_clients uploads/agent_responses logs/anomalies logs/data/datacapture-rig

    # Ensure config.yaml exists (required volume mount)
    if [[ ! -f python/config.yaml ]]; then
        echo "WARNING: python/config.yaml not found."
        echo "  The container expects this file to be present."
        echo "  Copy python/config.yaml.example to python/config.yaml and edit it, then re-run."
    fi

    if [[ "$USE_PROXY" -eq 1 ]]; then
        # Proxy setup — if HTTP_PROXY points to localhost/127.0.0.1 (e.g. a local
        # Cntlm forwarder) it is unreachable from inside the Docker build context,
        # so fall back to the upstream Bosch proxy directly.
        BOSCH_PROXY="http://rb-proxy-de.bosch.com:8080"
        PROXY="${HTTP_PROXY:-$BOSCH_PROXY}"
        NO_PROXY_VAL="${NO_PROXY:-localhost,127.0.0.1}"

        if echo "$PROXY" | grep -qE '(localhost|127\.0\.0\.1)'; then
            echo "WARNING: \$HTTP_PROXY points to localhost ($PROXY), which is unreachable inside Docker."
            echo "         Falling back to upstream proxy: $BOSCH_PROXY"
            PROXY="$BOSCH_PROXY"
        fi

        echo "Building Docker image (proxy: $PROXY) ..."
        BUILD_ARGS=(
            --build-arg "HTTP_PROXY=$PROXY"
            --build-arg "HTTPS_PROXY=$PROXY"
            --build-arg "NO_PROXY=$NO_PROXY_VAL"
            --build-arg "http_proxy=$PROXY"
            --build-arg "https_proxy=$PROXY"
            --build-arg "no_proxy=$NO_PROXY_VAL"
        )
        $COMPOSE_CMD build "${BUILD_ARGS[@]}"
    else
        echo "Building Docker image (no proxy) ..."
        $COMPOSE_CMD build
    fi

    # Tag the built image for the Bosch container registry
    REGISTRY_IMAGE="bcr2.inside.bosch.cloud/spf-ict/ict412_allspark_edgeserver:latest"
    LOCAL_IMAGE="allspark-edge-server-edge-server"
    if docker image inspect "$LOCAL_IMAGE" &>/dev/null 2>&1; then
        echo "Tagging image as $REGISTRY_IMAGE ..."
        docker tag "$LOCAL_IMAGE" "$REGISTRY_IMAGE"
        echo "To push to the registry:"
        echo "  docker login bcr2.inside.bosch.cloud"
        echo "  docker push $REGISTRY_IMAGE"
    fi

    echo ""
    echo "Docker install complete."
    echo "To start the server manually:"
    echo "  $COMPOSE_CMD up -d"
    echo ""
    echo "See docs/DOCKER.md for remote deployment and registry push instructions."
}

# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
case "$MODE" in
    --native|"")
        install_native
        ;;
    --docker)
        install_docker
        ;;
    --both)
        install_native
        install_docker
        ;;
esac

print_header "Done"
echo "Edge API  → http://localhost:8080"
echo "Control Plane → http://localhost:8081"
echo ""
