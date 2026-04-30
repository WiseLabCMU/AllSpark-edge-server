FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (including C build tools for native Python extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    python3-dev \
    libffi-dev \
    libavahi-compat-libdnssd-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY python/requirements.txt requirements.txt
RUN pip install -r requirements.txt

# Copy application code
COPY main.py .
COPY python/ python/
COPY third-party/ third-party/

# Create runtime directories
RUN mkdir -p uploads/mobile_clients uploads/agent_responses logs/anomalies logs/data/datacapture-rig

# Expose ports: Edge API (8080), Control Plane (8081)
EXPOSE 8080 8081

CMD ["python", "main.py"]

