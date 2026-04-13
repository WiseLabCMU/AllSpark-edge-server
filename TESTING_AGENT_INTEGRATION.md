# Testing the AllSpark Agent Integration – End-to-End Guide

This guide walks you through the complete workflow:

1. Starting the **Agentic Framework** (`adk web`)
2. Starting the **Edge Server** (aiohttp on port 8080)
3. Starting the **Control Plane dashboard** (NiceGUI on port 8081)
4. Simulating an anomaly to trigger agent detection
5. Viewing the agent response in the dashboard

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | ≥ 3.11 |
| conda (or Poetry) | any recent |
| Mosquitto MQTT broker | any |
| pytest + pytest-asyncio | installed via `requirements.txt` |

Make sure Mosquitto is running locally on port 1883 (used by the dashboard's MQTT listener):

```bash
brew services start mosquitto   # macOS
# or
mosquitto -d                    # run in background manually
```

---

## Repository Layout (Edge Server)

```
AllSpark-edge-server/
├── config.json                      # ← agentConfig + agentResponsePath added here
├── python/
│   ├── server.py                    # aiohttp Edge Server (port 8080)
│   ├── agent_service/               # NEW: agent integration package
│   │   ├── __init__.py
│   │   ├── models.py                # AnomalyRequest / AgentResponse dataclasses
│   │   ├── client.py                # AgentApiClient (async HTTP → adk web)
│   │   └── response_store.py        # AnomalyResponseStore (file-system persistence)
│   ├── control_plane/
│   │   ├── main.py                  # NiceGUI Control Plane (port 8081)
│   │   └── pages/
│   │       └── agent.py             # UPDATED: live form + response feed
│   └── tests/
│       ├── test_agent_service.py    # 30 unit + async integration tests
│       └── e2e_agent_workflow.py    # CLI smoke-test against a running server
```

---

## Step 1 – Start the Agentic Framework

The Edge Server calls `adk web` on `http://localhost:8000/run` (configurable in `config.json`).

```bash
cd /Users/bos2pi/git/Bosch-Github/allspark-agentic-framework

# Activate the framework environment (conda or Poetry)
conda activate allspark_agent_env
# -- or --
poetry shell

# Start the ADK web server (default port 8000)
adk web
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
# or just check the ADK UI at http://localhost:8000
```

---

## Step 2 – Configure the Edge Server

Open `AllSpark-edge-server/config.json` and confirm / adjust the `agentConfig` block:

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

---

## Step 3 – Start the Edge Server

```bash
cd /Users/bos2pi/git/Bosch-Github/AllSpark-edge-server/python

# Activate the edge server environment
source venv/bin/activate   # or: conda activate <env>

python server.py
```

Expected startup output:

```
Loaded config from .../config.json
Agent client initialised. Responses stored at: .../uploads/agent_responses/
AnomalyResponseStore initialised at .../uploads/agent_responses/
Server is running on http://0.0.0.0:8080
WebSocket endpoint: ws://192.168.x.x:8080
Advertising Bonjour service: AllSpark Server on 192.168.x.x:8080
```

Verify it's up:

```bash
curl http://localhost:8080/api/health
```

---

## Step 4 – Start the Control Plane Dashboard

Open a **new terminal**:

```bash
cd /Users/bos2pi/git/Bosch-Github/AllSpark-edge-server/python/control_plane

source ../venv/bin/activate   # same environment as the edge server

python main.py
```

Open the dashboard in your browser:

```
http://localhost:8081/agent
```

You should see the **Agentic Analysis** page with:
- A form to manually trigger analysis (top)
- An empty "Agent Analysis Responses" feed (bottom, auto-refreshes every 10 s)

---

## Step 5 – Run the Unit Tests (no servers needed)

```bash
cd /Users/bos2pi/git/Bosch-Github/AllSpark-edge-server/python

python -m pytest tests/test_agent_service.py -v
```

Expected: **30 passed** covering models, client, store, and async mocked HTTP.

```
tests/test_agent_service.py::TestAnomalyRequest::test_round_trip_dict PASSED
...
tests/test_agent_service.py::TestAgentApiClientAsync::test_analyze_anomaly_success PASSED
======= 30 passed in 0.25s =======
```

---

## Step 6 – Simulate an Anomaly (Method A: Dashboard Form)

With all three services running, go to:

```
http://localhost:8081/agent
```

Fill in the **Trigger Anomaly Analysis** form:

| Field | Example Value |
|---|---|
| Anomaly Clip Path | `/tmp/test_anomaly_clip.mp4` (or a real clip path) |
| Log Path | `/tmp/mqtt_trace.log` (can be empty) |
| Anomaly Time (ISO-8601) | `2026-04-13T12:00:00` |
| Clip Start Time | `2026-04-13T11:59:30` |
| Device Name | `cesar_rig_a` |
| Error / Label | `missed expected message` |
| Expected MQTT Topic | `allspark/anomaly_detected` |
| Video Storage Path | `/path/to/video/chunks/` (optional) |

Click **"Dispatch to Agentic Framework"**.

The button disables while the agent processes. On completion:
- A green notification shows the `request_id`
- The status label shows the storage path (`✅ Stored at: .../uploads/agent_responses/...`)
- The response feed below refreshes and shows the new card with the agent summary

---

## Step 7 – Simulate an Anomaly (Method B: curl / CLI)

With the edge server running, POST directly to the new endpoint:

```bash
curl -s -X POST http://localhost:8080/api/agent/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "clip_path": "/tmp/test_anomaly_clip.mp4",
    "log_path": "",
    "anomaly_time": "2026-04-13T12:00:00",
    "clip_start_time": "2026-04-13T11:59:30",
    "error": "missed expected message",
    "expected_topic": "allspark/anomaly_detected",
    "device_name": "cesar_rig_a",
    "mqtt_clip_messages": [
      {"topic": "rng120/status", "payload": "ok", "t": 1744545570000}
    ]
  }' | python3 -m json.tool
```

Successful response (agent online):

```json
{
  "success": true,
  "request_id": "2026-04-13T120000_a1b2c3",
  "session_id": "edge_session_2026-04-13T120000_a1b2c3_x9z1f2",
  "status": "success",
  "summary": "The video clip shows the CESAR cell bolt assembly...",
  "stored_at": "/Users/.../uploads/agent_responses/cesar_rig_a/2026-04-13/120000_a1b2c3",
  "error_message": ""
}
```

Graceful response (agent offline):

```json
{
  "success": false,
  "status": "error",
  "error_message": "Session creation failed: ...",
  "stored_at": ""
}
```

---

## Step 8 – Simulate an Anomaly (Method C: MQTT → full pipeline)

This replicates the full `video_clipper_edge_server.py` → `anomaly_agent_sender.py` pipeline using the test publisher from `allspark-datacapture`:

```bash
# Terminal A – video clipper (listens on allspark/anomaly_detected, publishes allspark/clip_generated)
cd /Users/bos2pi/git/Bosch-Github/allspark-datacapture
python utilities/video_clipper_edge_server.py \
  -c configs/mqtt_config.json \
  -v /path/to/video/chunks/ \
  -d 30 \
  -o /tmp/anomaly_clips/

# Terminal B – publish a simulated anomaly MQTT message
python utilities/test_anomaly_publisher.py \
  -c configs/mqtt_config.json \
  -t "2026-04-13_12:00:00"
```

Then use `curl` (Step 7) or the dashboard form (Step 6) to forward the generated clip path to the Edge Server for agent analysis.

> **Or** configure `anomaly_agent_sender.py` to point at the Edge Server endpoint instead of calling the agent directly – see the [existing README](../../../allspark-datacapture/utilities/README_anomaly_agent_sender.md) for that flow.

---

## Step 9 – Run the E2E Smoke Test Script

With **both the edge server and agentic framework running**:

```bash
cd /Users/bos2pi/git/Bosch-Github/AllSpark-edge-server/python

python tests/e2e_agent_workflow.py \
  --port 8080 \
  --clip-path /tmp/test_anomaly_clip.mp4 \
  --anomaly-time "2026-04-13T12:00:00"
```

This script:
1. Checks `/api/health`
2. POSTs to `/api/agent/analyze`
3. GETs `/api/agent/responses?device_name=e2e_test_device`
4. GETs `/api/agent/responses` (all)

Expected final output:

```
✅ All E2E checks passed.
```

> If the agentic framework is offline the script still passes – it accepts a graceful `status=error` from the endpoint. A non-200 HTTP code or an unreachable edge server is the only hard failure.

---

## Step 10 – View Responses in the Dashboard

Go to `http://localhost:8081/agent`.

Each stored response renders as a card:

```
┌─────────────────────────────────────────────────────┐
│ [SUCCESS]  Request: 2026-04-13T120000_a1b2c3         │
│ Anomaly Time: 2026-04-13T12:00:00                   2026-04-13T12:00:04 │
│ Clip: /tmp/test_anomaly_clip.mp4                    │
│ Session: edge_session_2026-...                      │
├─────────────────────────────────────────────────────┤
│ ▼ Agent Summary                                     │
│   The video clip shows the CESAR cell bolt          │
│   assembly at T+5s. The washer was misaligned...    │
├─────────────────────────────────────────────────────┤
│ 📁 .../uploads/agent_responses/cesar_rig_a/2026-04-13/120000_a1b2c3 │
└─────────────────────────────────────────────────────┘
```

Use the **Filter by Device Name** input to narrow results and the **Refresh** button to force an immediate reload.

---

## Stored Response Files

Every analysis creates a timestamped subfolder:

```
uploads/agent_responses/
  <device_name>/
    <YYYY-MM-DD>/
      <HHMMSS_<id>>/
        response.json    ← full AgentResponse (status, summary, raw_response, …)
        request.json     ← original AnomalyRequest (clip_path, anomaly_time, …)
        summary.txt      ← plain-text agent summary (human-readable)
```

List the latest responses via the API:

```bash
curl "http://localhost:8080/api/agent/responses?limit=5" | python3 -m json.tool
```

---

## Troubleshooting

| Symptom | Check |
|---|---|
| `503 Agent service not initialised` | Edge server started without loading config – make sure you run `python server.py` from `AllSpark-edge-server/python/` |
| `Session creation failed: HTTP 404` | `agent_app_name` in `config.json` doesn't match the app registered in `adk web` |
| `Connection refused` on agent URL | Agentic Framework not running – start `adk web` first |
| Agent times out | Increase `agent_timeout` in `config.json` (default 300 s); the VLM video analysis can be slow |
| Dashboard shows "No agent responses found yet" | The edge server and control plane both need to be running; check the edge port (`8080`) in the dashboard URL |
| MQTT "start error" in dashboard | Mosquitto not running on `127.0.0.1:1883` – `brew services start mosquitto` |

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

# 6. Trigger analysis via curl
curl -X POST http://localhost:8080/api/agent/analyze \
  -H "Content-Type: application/json" \
  -d '{"clip_path":"/tmp/clip.mp4","anomaly_time":"2026-04-13T12:00:00","device_name":"rig_a"}'

# 7. Check stored responses
curl "http://localhost:8080/api/agent/responses?limit=5"

# 8. Run E2E smoke test
cd AllSpark-edge-server/python && python tests/e2e_agent_workflow.py --port 8080
```

