# AllSpark Edge Server — Docker Registry & Remote Deployment

Registry: **`bcr2.inside.bosch.cloud/spf-ict/ict412_allspark_edgeserver`**

---

## Contents

1. [Build & Push (developer machine)](#1-build--push-developer-machine)
2. [Pull & Run on a Remote Machine](#2-pull--run-on-a-remote-machine)
3. [Volume Setup](#3-volume-setup)
4. [Deployment Options — Compose vs plain Docker](#4-deployment-options--compose-vs-plain-docker)
5. [Proxy Notes](#5-proxy-notes)
6. [Quick Reference](#6-quick-reference)

---

## 1. Build & Push (developer machine)

### 1.1 Log in to the Bosch Container Registry

```bash
docker login bcr2.inside.bosch.cloud
# enter your Bosch NTID and password / personal access token when prompted
```

> If you are behind the Bosch corporate proxy, ensure your Docker daemon is
> configured to route traffic through it (see [Proxy Notes](#5-proxy-notes)).

### 1.2 Build and tag

```bash
# From the repo root
IMAGE=bcr2.inside.bosch.cloud/spf-ict/ict412_allspark_edgeserver:latest

docker build \
  --build-arg HTTP_PROXY=http://rb-proxy-de.bosch.com:8080 \
  --build-arg HTTPS_PROXY=http://rb-proxy-de.bosch.com:8080 \
  --build-arg NO_PROXY=localhost,127.0.0.1 \
  --build-arg http_proxy=http://rb-proxy-de.bosch.com:8080 \
  --build-arg https_proxy=http://rb-proxy-de.bosch.com:8080 \
  --build-arg no_proxy=localhost,127.0.0.1 \
  -t "$IMAGE" .
```

Or use the install script, which handles proxy detection automatically, then
tag afterward:

```bash
./install.sh --docker          # builds as 'allspark-edge-server-edge-server'
docker tag allspark-edge-server-edge-server:latest "$IMAGE"
```

### 1.3 Push to the registry

```bash
docker push bcr2.inside.bosch.cloud/spf-ict/ict412_allspark_edgeserver:latest
```

---

## 2. Pull & Run on a Remote Machine

### 2.1 Prerequisites on the remote machine

| Requirement | Notes |
|---|---|
| Docker Engine ≥ 24 | `docker --version` |
| Internet / registry access | Must reach `bcr2.inside.bosch.cloud` (port 443) |
| `python/config.yaml` | Must exist on the remote host (see [Volume Setup](#3-volume-setup)) |

### 2.2 Log in and pull

```bash
# On the remote machine
docker login bcr2.inside.bosch.cloud
docker pull bcr2.inside.bosch.cloud/spf-ict/ict412_allspark_edgeserver:latest
```

If the remote machine is also behind the Bosch proxy, configure the Docker
daemon first — see [Proxy Notes § Runtime proxy](#52-runtime-proxy-docker-daemon).

---

## 3. Volume Setup

The container expects three bind-mounts:

| Host path (remote) | Container path | Purpose |
|---|---|---|
| `./uploads` | `/app/uploads` | Mobile client uploads and agent responses (persistent data) |
| `./logs` | `/app/logs` | Server and anomaly logs (persistent data) |
| `./python/config.yaml` | `/app/python/config.yaml` | Runtime configuration (required) |

Create the directories and drop in a `config.yaml` **before** starting the
container:

```bash
mkdir -p ~/allspark/{uploads/mobile_clients,uploads/agent_responses,logs/anomalies,logs/data/datacapture-rig,python}

# Copy your config.yaml to the remote host (from developer machine):
scp python/config.yaml user@remote-host:~/allspark/python/config.yaml
```

Key values to adjust in `config.yaml` for a remote deployment:

```yaml
hostname: "0.0.0.0"        # keep — binds to all interfaces
agentConfig:
  agent_url: "http://<ADK-host>:8000/run"   # point to where adk web is running
```

---

## 4. Deployment Options — Compose vs plain Docker

### Option A — Docker Compose (recommended)

Copy only the `docker-compose.yml` to the remote machine. You do **not** need
the rest of the source tree — the image is pulled from the registry.

```bash
# On the remote machine — minimal directory layout needed:
~/allspark/
├── docker-compose.yml        ← copy this from the repo
├── python/
│   └── config.yaml           ← copy / edit this
├── uploads/                  ← created by mkdir above
└── logs/                     ← created by mkdir above
```

Edit the `docker-compose.yml` image reference so it pulls instead of building:

```yaml
services:
  edge-server:
    image: bcr2.inside.bosch.cloud/spf-ict/ict412_allspark_edgeserver:latest
    # Remove the 'build: .' line — not needed on the remote machine
    container_name: allspark-edge-server
    ports:
      - "8080:8080"
      - "8081:8081"
    volumes:
      - ./uploads:/app/uploads
      - ./logs:/app/logs
      - ./python/config.yaml:/app/python/config.yaml
    environment:
      - PYTHONUNBUFFERED=1
    restart: unless-stopped
    networks:
      - allspark

networks:
  allspark:
    driver: bridge
```

Start:

```bash
cd ~/allspark
docker compose pull   # fetch latest image from registry
docker compose up -d  # start detached
docker compose logs -f edge-server  # follow logs
```

Stop / update:

```bash
docker compose pull && docker compose up -d --force-recreate
```

---

### Option B — Plain `docker run` (no Compose file needed)

```bash
docker run -d \
  --name allspark-edge-server \
  --restart unless-stopped \
  -p 8080:8080 \
  -p 8081:8081 \
  -v ~/allspark/uploads:/app/uploads \
  -v ~/allspark/logs:/app/logs \
  -v ~/allspark/python/config.yaml:/app/python/config.yaml \
  -e PYTHONUNBUFFERED=1 \
  bcr2.inside.bosch.cloud/spf-ict/ict412_allspark_edgeserver:latest
```

---

## 5. Proxy Notes

### 5.1 Build-time proxy (affects `apt-get` and `pip` inside the image)

Pass proxy settings as `--build-arg` — already handled by `install.sh`.
The Dockerfile does **not** bake proxy settings into the image, so the image
is portable to non-proxy environments.

### 5.2 Runtime proxy (Docker daemon — affects `docker pull`)

On a machine that needs a proxy to reach `bcr2.inside.bosch.cloud`, configure
the Docker daemon's proxy settings (this is separate from container-level proxy
env vars):

```bash
# /etc/systemd/system/docker.service.d/proxy.conf
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/proxy.conf <<'EOF'
[Service]
Environment="HTTP_PROXY=http://rb-proxy-de.bosch.com:8080"
Environment="HTTPS_PROXY=http://rb-proxy-de.bosch.com:8080"
Environment="NO_PROXY=localhost,127.0.0.1,.bosch.com"
EOF

sudo systemctl daemon-reload
sudo systemctl restart docker
```

Verify:

```bash
docker info | grep -i proxy
```

### 5.3 Container runtime proxy (affects outbound calls from the running container)

If the running container itself needs to reach external services (e.g. the ADK
agent at a proxied address), pass proxy env vars at runtime:

```yaml
# in docker-compose.yml
environment:
  - PYTHONUNBUFFERED=1
  - HTTP_PROXY=http://rb-proxy-de.bosch.com:8080
  - HTTPS_PROXY=http://rb-proxy-de.bosch.com:8080
  - NO_PROXY=localhost,127.0.0.1
```

---

## 6. Quick Reference

```bash
# --- Developer machine ---
IMAGE=bcr2.inside.bosch.cloud/spf-ict/ict412_allspark_edgeserver:latest

docker login bcr2.inside.bosch.cloud
docker build --build-arg HTTP_PROXY=http://rb-proxy-de.bosch.com:8080 \
             --build-arg HTTPS_PROXY=http://rb-proxy-de.bosch.com:8080 \
             -t "$IMAGE" .
docker push "$IMAGE"

# --- Remote machine ---
docker login bcr2.inside.bosch.cloud
docker pull bcr2.inside.bosch.cloud/spf-ict/ict412_allspark_edgeserver:latest

cd ~/allspark
docker compose up -d
```

Services once running:

| Service | URL |
|---|---|
| Edge API | `http://<host>:8080` |
| Control Plane | `http://<host>:8081` |
