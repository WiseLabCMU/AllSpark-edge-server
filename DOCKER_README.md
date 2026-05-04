# Docker Setup for AllSpark Edge Server

## Prerequisites
- Docker Engine 20.10+
- Docker Compose V2 (integrated in Docker CLI)

---

## Option A — Local Build and Run

### 1. Setup Configuration

```bash
# Edit config.yaml to point the agent URL to your running allspark-agent instance
nano python/config.yaml
# Set: agentConfig.agent_url: http://<agent-host>:8000/run
```

### 2. Build and Run

```bash
# Build the image (with corporate proxy via Cntlm — default)
./build.sh --no-push --no-deploy

# Build without a proxy (direct internet access)
./build.sh --no-proxy --no-push --no-deploy

# Start the container
docker compose up -d

# View logs
docker compose logs -f

# Stop the container
docker compose down
```

### 3. Access the Application

| Service | URL |
|---|---|
| Edge API (mobile client uploads) | http://localhost:8080 |
| Control Plane dashboard | http://localhost:8081 |

---

## Option B — Push to Registry and Run Remotely

### Step 1 — Create the registry credentials file

Create `.env.container_registry` in the repo root — **never commit this file**:

```dotenv
REGISTRY_USERNAME=your_username
REGISTRY_PASSWORD=your_token_or_password
```

See `.env.container_registry.example` for a template. Verify it is in `.gitignore`:
```bash
grep container_registry .gitignore
```

### Step 2 — Build and push in one command

```bash
# Build with proxy + push + deploy (all defaults on)
./build.sh

# Build without proxy + push + deploy
./build.sh --no-proxy

# Build and push only (skip deploy)
./build.sh --no-deploy

# Override NT ID (default: add5kor)
./build.sh --ntid=xyz1abc
```

`build.sh` reads `REGISTRY_USERNAME` and `REGISTRY_PASSWORD` from `.env.container_registry`,
logs in to `bcr2.inside.bosch.cloud` via `--password-stdin`, then pushes:
```
bcr2.inside.bosch.cloud/spf-ict/ict412_allspark-edge-server:latest
```

### Step 3 — Deploy files to the remote machine

`build.sh --deploy` (on by default) automatically copies `docker-compose.yml` and `config.yaml`
to `/local/home/rbadmin_app1/allspark-edge/` on the remote via SCP through the Bosch jump host.

This is equivalent to:
```bash
scp -o "ProxyJump add5kor@rb-psmp.bosch.com" \
    docker-compose.yml config.yaml \
    rbadmin_app1@htvvm662.emea.bosch.com:/local/home/rbadmin_app1/allspark-edge/
```

> **Note:** Re-run `./build.sh --no-push` (or just `./build.sh`) whenever `config.yaml` changes
> to push the updated file to the remote.

> **Tip:** Add to `~/.ssh/config` for easier access:
> ```
> Host htvvm662
>     HostName htvvm662.emea.bosch.com
>     User rbadmin_app1
>     ProxyJump add5kor@rb-psmp.bosch.com
> ```

### Step 4 — Prepare the remote machine

Only these need to exist on the remote — no source code required.

> **SSL / certificates:** Not needed for server-to-server use (agent integration).
> If no key/cert files are present the server automatically falls back to plain HTTP/WS.
> Only required if connecting the iOS mobile app, which needs `wss://`.

#### 4a. Upload and log directories

Created automatically by the container on first run via the volume mounts in `docker-compose.yml`.

### Step 5 — Pull and run on the remote machine

The remote machine uses **Podman**. SSH in and run:

```bash
ssh rbadmin_app1@htvvm662.emea.bosch.com   # via jump host if needed

# Use pull_containers.sh from allspark-hatvan-pipeline (recommended)
./pull_containers.sh --only=edge
```

Or run directly with podman:
```bash
podman stop allspark-edge-server 2>/dev/null || true
podman rm   allspark-edge-server 2>/dev/null || true
podman run -d \
  --name allspark-edge-server \
  --network host \
  --security-opt label=disable \
  -v /local/home/rbadmin_app1/allspark-edge/config.yaml:/app/python/config.yaml:ro \
  -v /local/home/rbadmin_app1/allspark-edge/uploads:/app/uploads \
  -v /local/home/rbadmin_app1/allspark-edge/logs:/app/logs \
  -v /net/htvvm662/fs0/anomaly_events:/net/htvvm662/fs0/anomaly_events:rw \
  -e http_proxy=http://rb-proxy-sl.bosch.com:8080 \
  -e https_proxy=http://rb-proxy-sl.bosch.com:8080 \
  -e HTTP_PROXY=http://rb-proxy-sl.bosch.com:8080 \
  -e HTTPS_PROXY=http://rb-proxy-sl.bosch.com:8080 \
  -e NO_PROXY=localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16 \
  -e no_proxy=localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16 \
  -e PYTHONUNBUFFERED=1 \
  bcr2.inside.bosch.cloud/spf-ict/ict412_allspark-edge-server:latest
```

> **Network mode:** `--network host` is required so the edge server can reach
> the agent at `localhost:8000` without bridge networking issues. The app listens
> directly on host ports 9080/9081/9090/9876 as configured in `config.yaml`.
>
> **SELinux:** `--security-opt label=disable` is required on RHEL/Rocky hosts
> to allow glibc's `mprotect` syscall (exit 127 without this flag).

### Step 6 — Access the Application

| Service | URL |
|---|---|
| Edge API | http://\<remote-machine-ip\>:9080 |
| Control Plane dashboard | http://\<remote-machine-ip\>:9081 |
| Rerun web viewer | http://\<remote-machine-ip\>:9090 |

> **Port note:** With `--network host`, the app binds directly on these ports.
> Ports 9090 (Rerun web) and 9876 (Rerun gRPC) are spawned on-demand when
> "View in Rerun" is clicked — they show as offline until first use.

Ensure ports `9080`, `9081`, `9090`, and `9876` are open in the remote machine's firewall.

---

## Externally Configured Files Summary

| File / Directory | Location on remote | Mount point inside container | Deployed by |
|---|---|---|---|
| `config.yaml` | `~/allspark-edge/config.yaml` | `/app/python/config.yaml` | `build_all.sh` (allspark-hatvan-pipeline) |
| `docker-compose.yml` | `~/allspark-edge/docker-compose.yml` | — | `build_all.sh` (allspark-hatvan-pipeline) |
| `uploads/` | `~/allspark-edge/uploads/` | `/app/uploads/` | Created by container |
| `logs/` | `~/allspark-edge/logs/` | `/app/logs/` | Created by container |
| NFS anomaly events | `/net/htvvm662/fs0/anomaly_events` | `/net/htvvm662/fs0/anomaly_events` | Pre-existing NFS mount |
| `keys/` *(optional)* | `~/allspark-edge/keys/` | `/app/keys/` | Manual — only needed for iOS mobile app (HTTPS/WSS) |
