FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libavahi-compat-libdnssd-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY python/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .
COPY python/ python/
COPY third-party/ third-party/

# Create runtime directories
RUN mkdir -p uploads/mobile_clients uploads/agent_responses logs/anomalies logs/data/datacapture-rig

# Expose ports: Edge API (8080), Control Plane (8081)
EXPOSE 8080 8081

CMD ["python", "main.py"]

