# Testing the AllSpark Agent Integration – End-to-End Guide

This guide walks you through the complete workflow:

1. Starting the **Agentic Framework** (`adk web`)
2. Starting the **Edge Server** (aiohttp on port 8080)
3. Starting the **Control Plane dashboard** (NiceGUI on port 8081)
4. Submitting a new anomaly for agent analysis
5. Reviewing and resuming an investigation via the ADK dev-ui
6. Viewing agent responses in the dashboard

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | ≥ 3.11 |
| conda (or Poetry) | any recent |
| Mosquitto MQTT broker | any |
| pytest + pytest-asyncio | installed via `python/requirements.txt` |

Make sure Mosquitto is running locally on port 1883 (used by the dashboard's MQTT listener):

```bash
brew services start mosquitto   # macOS
# or
mosquitto -d                    # run in background manually
```

---

## Repository Layout (Edge Server)

For the complete file-system tree map encompassing Python backend services, NiceGUI control plane modules, testing pipelines, and default configurations, see the **[Directory Structure](README.md#directory-structure)** in the primary repository documentation.

---

## Step 1 – Start the Agentic Framework

The Edge Server calls `adk web` on `http://localhost:8000/run` (configurable in `python/config.yaml`).

```bash
cd /Users/bos2pi/git/Bosch-Github/allspark-agentic-framework

# Activate the framework environment via Conda:
conda activate allspark_agent_env
adk web

# -- or start it via Poetry --
# (In Poetry 2.0+, 'poetry shell' is deprecated. Use 'poetry run' directly)
poetry run adk web
```

You should see:

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
--- Initializing Agent Framework ---
--- Root Agent Created Successfully ---
```

> **Tip:** The active profile is set in `allspark_agent/config/config.yaml`.
> For CESAR/anomaly video analysis, make sure `active_profile: cesar_config.yaml` is set.

Verify the framework is reachable:

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
```

The ADK developer UI is available at:
```
http://localhost:8000/dev-ui/
```

---

## Step 2 – Configure the Edge Server

Open `AllSpark-edge-server/python/config.yaml` and confirm / adjust the `agentConfig` block:

```json
{
  "port": 8080,
  "agentResponsePath": "uploads/agent_responses/",
  "agentConfig": {
    "agent_url":        "http://localhost:8000/run",
    "agent_app_name":   "allspark_agent",
    "agent_user_id":    "edge_server_user",
    "agent_session_id": "edge_session",
    "agent_timeout":    300,
    "agent_init_message": "Hey, can you help me do some analysis?"
  }
}
```

> `agent_app_name` must match the app name registered in `adk web` (default: `allspark_agent`).
> The control plane reads `agent_url` to derive the ADK base URL for the "Open in ADK" button.

---

## Step 3 – Start the Edge Server

```bash
cd /Users/bos2pi/git/Bosch-Github/AllSpark-edge-server/python

source venv/bin/activate   # or: conda activate <env>
python server.py
```

Expected startup output:

```
Loaded config from .../config.yaml
Agent client initialised. Responses stored at: .../uploads/agent_responses/
AnomalyResponseStore initialised at .../uploads/agent_responses/
Server is running on http://0.0.0.0:8080
```

Verify:

```bash
curl http://localhost:8080/api/health
```

---

## Step 4 – Start the Control Plane Dashboard

```bash
cd /Users/bos2pi/git/Bosch-Github/AllSpark-edge-server/python/control_plane

source ../venv/bin/activate
python main.py
```

Open the dashboard:

```
http://localhost:8081/agent
```

### Header navigation

For detailed explanations of the routing parameters exposed by the Control Plane, refer to the **[Web Interface](README.md#web-interface)** documentation in the primary README.

---

## Step 5 – Run the Unit Tests (no servers needed)

```bash
cd /Users/bos2pi/git/Bosch-Github/AllSpark-edge-server/python
python -m pytest tests/test_agent_service.py -v
```

Expected: **30 passed** (models, client, store, async mocked HTTP).

---

## Step 6 – Submit a New Anomaly (Method A: Debug page)

Navigate to `http://localhost:8081/debug` (or click **Debug** in the header).

The form is pre-filled with working defaults:

| Field | Default Value |
|---|---|
| Anomaly Clip Filename | `anomaly_clip_20250917_143650.mp4` |
| Anomaly Time (ISO-8601) | `2025-09-17T14:36:50` |
| Clip Start Time | `2025-09-17T14:36:20` |
| Error / Label | `missed expected message` |
| Expected MQTT Topic | `allspark/anomaly_detected` |
| MQTT Messages | *(empty or paste a JSON array)* |

Click **"Dispatch to Agentic Framework"**.

> **Important:** The clip filename must be the plain basename (no directory prefix).
> The agent's `VideoDataLoader` locates the file by `endswith()` matching against its configured
> data folder (`allspark_agent/sample_data/cesar/camera-video/`).
> Use `submit_anomaly_to_edge.py` (Method B below) to stage the file automatically.

After dispatch succeeds, the result panel shows inline:
- The **ADK session ID** and an **"Open Session in ADK"** button
- The full ADK dev-ui URL for that session
- The storage path on disk
- An expandable agent summary

---

## Step 7 – Submit a New Anomaly (Method B: CLI)

The CLI script stages the clip into the agent's data folder before submission:

```bash
cd /Users/bos2pi/git/Bosch-Github/AllSpark-edge-server/python

# Minimal – timestamp auto-derived from filename
python tests/submit_anomaly_to_edge.py \
    --clip-path /path/to/anomaly_clip_20250917_143650.mp4

# Full specification
python tests/submit_anomaly_to_edge.py \
    --clip-path /path/to/anomaly_clip_20250917_143650.mp4 \
    --anomaly-time 2025-09-17T14:36:50 \
    --clip-start-time 2025-09-17T14:36:20 \
    --error "missed expected message" \
    --expected-topic allspark/anomaly_detected \
    --mqtt-messages '[{"topic":"rng120/status","payload":"bolt_tightened"}]' \
    --edge-port 8080
```

The script prints the ADK session ID and a direct dev-ui URL at the end:

```
╔══════════════════════════════════════════════════════════════╗
║  AllSpark Agent – ADK Session Lookup                         ║
║                                                              ║
║  Session ID  : edge_session_2025-09-17T14_36_50_abc123       ║
║                                                              ║
║  Open ADK UI : http://localhost:8000                         ║
║  Navigate to : Sessions tab                                  ║
║  Search for  : edge_session_2025-09-17T14_36_50_abc123       ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Step 8 – Submit a New Anomaly (Method C: curl)

```bash
curl -s -X POST http://localhost:8080/api/agent/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "clip_path": "anomaly_clip_20250917_143650.mp4",
    "anomaly_time": "2025-09-17T14:36:50",
    "clip_start_time": "2025-09-17T14:36:20",
    "error": "missed expected message",
    "expected_topic": "allspark/anomaly_detected",
    "mqtt_clip_messages": [
      {"topic": "rng120/status", "payload": "bolt_tightened"}
    ]
  }' | python3 -m json.tool
```

Successful response:

```json
{
  "success": true,
  "request_id": "2025-09-17T14_36_50_abc123",
  "session_id": "edge_session_2025-09-17T14_36_50_abc123_x9z1f2",
  "status": "success",
  "summary": "The video clip shows the CESAR cell bolt assembly…",
  "stored_at": "/Users/.../uploads/agent_responses/Anomaly_2025-09-17/143650_abc123",
  "error_message": ""
}
```

---

## Step 9 – Resume an Investigation via the Agent Page

Once at least one response is stored, go to `http://localhost:8081/agent`.

The Agent page shows a full-width **Recent Responses** feed (auto-refreshes every 10 s).

Each stored response renders as a card with:
- Status badge (SUCCESS / ERROR), clip filename, anomaly time, session ID
- Expandable **Agent Summary** (markdown)
- **Continue Investigation** button – opens the embedded ADK session viewer at `/agent/session`
- **View in Rerun.io** button (successful analyses only)
- Storage path on disk

### Embedded ADK session viewer (`/agent/session`)

Clicking **Continue Investigation** navigates to a dedicated page that embeds the ADK
developer UI in an iframe, directly at the session for that anomaly:

```
http://localhost:8000/dev-ui/?app=allspark_agent&user=edge_server_user&session=<session_id>
```

The page also provides:
- **"Open in New Window"** – pops the same URL into a full browser tab
- **"← Back to Responses"** – returns to the main Agent page

Inside the ADK dev-ui session you can:
- Read the full conversation history
- Send follow-up messages to the agent
- Inspect tool calls and intermediate reasoning steps

The session URL includes the `user` parameter derived from `agent_user_id` in `python/config.yaml`.

---

## Step 10 – Run the E2E Smoke Test

With **both the edge server and agentic framework running**:

```bash
cd /Users/bos2pi/git/Bosch-Github/AllSpark-edge-server/python

python tests/e2e_agent_workflow.py \
  --port 8080 \
  --clip-path /tmp/test_anomaly_clip.mp4 \
  --anomaly-time "2025-09-17T14:36:50"
```

Expected final output:

```
✅ All E2E checks passed.
```

---

## Stored Response Files

Every analysis creates a timestamped subfolder:

```
uploads/agent_responses/
  Anomaly_<YYYY-MM-DD>/
    <HHMMSS_<id>>/
      response.json          ← full AgentResponse
      request.json           ← original AnomalyRequest
      summary.txt            ← plain-text agent summary
      session_info.txt       ← ADK session lookup details (URL, session ID, user ID)
      video_clips/
      machine_anomaly_data/
```

List via API:

```bash
curl "http://localhost:8080/api/agent/responses?limit=5" | python3 -m json.tool
```

---

## API Reference

See the full [API endpoints schema](docs/endpoints.md) describing all HTTP endpoints, agentic framework integration routes, and WebSocket message protocols has been extracted.

- Note the  [`/api/agent/analyze`](docs/endpoints.md#post-apiagentanalyze) request body format.

---

## ADK Session URL Format

The control plane constructs the ADK dev-ui session URL using values from `/python/config.yaml`:

```
{agent_url stripped of /run}/dev-ui/?app={agent_app_name}&user={agent_user_id}&session={session_id}
```

Example (with default config):
```
http://localhost:8000/dev-ui/?app=allspark_agent&user=edge_server_user&session=edge_session_2025-09-17T14_36_50_abc123_x9z1f2
```

This URL is passed (URL-encoded) as the `adk_url` query parameter to `/agent/session`,
which renders it in an iframe identical to the Rerun viewer page.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| `503 Agent service not initialised` | Run `python server.py` from `AllSpark-edge-server/python/` |
| `Session creation failed: HTTP 404` | `agent_app_name` in `python/config.yaml` doesn't match `adk web` |
| `Connection refused` on agent URL | Start `adk web` first |
| Agent times out | Increase `agent_timeout` in `python/config.yaml` (default 300 s) |
| Dashboard shows "No agent responses yet" | Edge server not running or wrong port |
| Dropdown shows "No anomalies found" | Submit a new anomaly via the Debug page |
| "Continue Investigation" not shown | Response has no session ID – resubmit via Debug |
| Clip not found by agent | Ensure basename exists in `allspark_agent/sample_data/cesar/camera-video/` |
| MQTT "start error" | Run `brew services start mosquitto` |

---

## Quick-Reference: All Commands

```bash
# 1. Start MQTT broker
brew services start mosquitto

# 2. Start Agentic Framework (port 8000)
cd allspark-agentic-framework && conda activate allspark_agent_env && adk web

# 3. Start Edge Server (port 8080) – new terminal
cd AllSpark-edge-server/python && source venv/bin/activate && python server.py

# 4. Start Control Plane (port 8081) – new terminal
cd AllSpark-edge-server/python/control_plane && source ../venv/bin/activate && python main.py

# 5. Run unit tests
cd AllSpark-edge-server/python && python -m pytest tests/test_agent_service.py -v

# 6a. Submit new anomaly via CLI (recommended – stages clip automatically)
python tests/submit_anomaly_to_edge.py \
  --clip-path /path/to/anomaly_clip_20250917_143650.mp4

# 6b. Submit new anomaly via curl
curl -X POST http://localhost:8080/api/agent/analyze \
  -H "Content-Type: application/json" \
  -d '{"clip_path":"anomaly_clip_20250917_143650.mp4","anomaly_time":"2025-09-17T14:36:50"}'

# 7. Open investigation in embedded ADK viewer (from Agent page → Continue Investigation)
open "http://localhost:8081/agent/session?adk_url=http%3A%2F%2Flocalhost%3A8000%2Fdev-ui%2F%3Fapp%3Dallspark_agent%26user%3Dedge_server_user%26session%3D<session_id>"

# 8. Check stored responses
curl "http://localhost:8080/api/agent/responses?limit=5"

# 9. Run E2E smoke test
cd AllSpark-edge-server/python && python tests/e2e_agent_workflow.py --port 8080
```
