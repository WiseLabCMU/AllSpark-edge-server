FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (including C build tools for native Python extensions)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    python3-dev \
    libffi-dev \
    libavahi-compat-libdnssd-dev \
    ffmpeg

# Copy and install Python dependencies
COPY python/requirements.txt requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# Copy application code
COPY main.py .
COPY python/ python/
COPY third-party/ third-party/

# Create runtime directories
RUN mkdir -p uploads/mobile_clients uploads/agent_responses logs/anomalies logs/data/datacapture-rig

# Expose ports: Edge API (9080), Control Plane (9081)
# Note: app listens on these ports directly (set in config.yaml);
# when run with --network host there is no port mapping layer.
EXPOSE 9080 9081

CMD ["python", "main.py"]

