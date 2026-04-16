# AllSpark Edge Server - Endpoints

## Quick Reference Summary

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | [`/`](#get-) | API Active status message |
| `GET` | [`/api/health`](#get-apihealth) | Server health, uptime, and protocols |
| `GET` | [`/api/status`](#get-apistatus) | Connected WebSocket clients and state |
| `GET` | [`/api/config`](#get-apiconfig) | Fetch current mobile client UI/upload configuration |
| `POST` | [`/api/command/{connectionId}`](#post-apicommandconnectionid) | Send actions (e.g. `uploadTimeRange`) to a specific client |
| `POST` | [`/api/agent/analyze`](#post-apiagentanalyze) | Start a **new** anomaly analysis (creates new ADK session) |
| `POST` | [`/api/agent/continue`](#post-apiagentcontinue) | Send follow-up to an **existing** ADK session |
| `GET` | [`/api/agent/responses`](#get-apiagentresponses) | List stored responses, newest first (`?limit=N`) |
| `GET` | [`/api/agent/responses/{stored_at_b64}`](#get-apiagentresponsesstored_at_b64) | Retrieve a single response by base64-encoded path |

---

## HTTP Endpoints

### GET `/`
Returns a simple API Active message. The web control plane is now hosted separately on port 8081 via NiceGUI.

**Response:**
- Status: `200`
- Content-Type: `text/plain`
- Body: `AllSpark Edge API Server - API Active`

---

### GET `/api/health`
Health check endpoint that returns server status and uptime information.

**Response:**
```json
{
  "status": "ok",
  "timestamp": 1700000000.0,
  "uptime": 123.45,
  "protocols": ["wss"],
  "address": "192.168.1.5",
  "port": 8080
}
```

**Status:** `200`
**Content-Type:** `application/json`

---

### GET `/api/status`
Returns information about current WebSocket connections and their upload states.

**Response:**
```json
{
  "totalConnections": 2,
  "connections": [
    {
      "id": "abc123def",
      "clientName": "Lab Camera 1",
      "lastFilename": "video.mp4",
      "lastFilesize": 1048576
    }
  ]
}
```

**Status:** `200`
**Content-Type:** `application/json`

---

### GET `/api/config`
Retrieves the dynamic configuration JSON payload served to newly connected mobile clients. Controls video buffering metrics and radio communication policies.

**Response:**
```json
{
  "videoFormat": "mp4",
  "videoChunkDurationMs": 10000,
  "videoBufferMaxMB": 16000,
  "communicationsPolicy": {
    "wifi": true,
    "cellular": true
  }
}
```

**Status:** `200`
**Content-Type:** `application/json`

---

### POST `/api/command/{connectionId}`
Sends a command to a specific connected WebSocket client.

**Parameters:**
- `connectionId` (URL path parameter): The ID of the target connection

**Request Body for Upload Time Range Command:**
```json
{
  "command": "uploadTimeRange",
  "message": "optional message content",
  "startTime": 1700000000.0,
  "endTime": 1700000060.0
}
```

**Success Response:**
```json
{
  "success": true,
  "message": "Command sent"
}
```

**Status:** `200`
**Content-Type:** `application/json`

---

### POST `/api/agent/analyze`
Accepts anomaly metadata, calls the AllSpark Agentic Framework for investigation, stores the response to disk, and returns the analysis.

**Request Body:**
| Field | Type | Required | Notes |
|---|---|---|---|
| `clip_path` | `str` | ✅ | Plain basename – no directory prefix |
| `anomaly_time` | `str` | ✅ | ISO-8601 timestamp |
| `clip_start_time` | `str` | | ISO-8601 clip start |
| `log_path` | `str` | | Path to MQTT/log file |
| `error` | `str` | | Error label |
| `expected_topic` | `str` | | Expected MQTT topic |
| `mqtt_clip_messages` | `list` | | MQTT message dicts |
| `video_storage_path` | `str` | | Root video chunk path |
| `extra_metadata` | `dict` | | Forwarded verbatim to agent |

```json
{
  "clip_path":           "anomaly_clip_20260413_120000.mp4",
  "anomaly_time":        "2026-04-13T12:00:00",
  "log_path":            "/path/to/mqtt_trace.log",
  "clip_start_time":     "2026-04-13T11:59:30",
  "error":               "missed expected message",
  "expected_topic":      "allspark/anomaly_detected",
  "mqtt_clip_messages":  [],
  "video_storage_path":  "",
  "extra_metadata":      {}
}
```

**Response:**
```json
{
  "success": true,
  "request_id": "2025-09-17T14_36_50_abc123",
  "session_id": "edge_session_2025-09-17T14_36_50_abc123_x9z1f2",
  "status": "success",
  "summary": "The video clip shows the CESAR cell bolt assembly...",
  "stored_at": "/Users/.../uploads/agent_responses/Anomaly_2025-09-17/143650_abc123",
  "error_message": ""
}
```

**Status:** `200`
**Content-Type:** `application/json`

---

### POST `/api/agent/continue`
Send a follow-up prompt to an existing ADK session created by a prior `/api/agent/analyze` call.

**Request Body:**
| Field | Type | Required | Notes |
|---|---|---|---|
| `session_id` | `str` | ✅ | The ADK session ID |
| `prompt` | `str` | ✅ | The instruction for the UI |
| `clip_path` | `str` | | Used for metadata attribution |
| `anomaly_time` | `str` | | Used for metadata attribution |

**Response:** Same standard payload as `/analyze`.

**Status:** `200`
**Content-Type:** `application/json`

---

### GET `/api/agent/responses`
List stored agent responses mapping to historical anomaly investigations, returning the newest first.

**Query Parameters:**
- `limit` (integer, optional) - Max number of items to return. Default is `50`.

**Status:** `200`
**Content-Type:** `application/json`

---

### GET `/api/agent/responses/{stored_at_b64}`
Retrieves a single expanded stored AgentResponse by its original `stored_at` path string, encoded using URL-Safe Base64.

**Status:** `200`
**Content-Type:** `application/json`

---

## WebSocket Endpoint

**URL:** `ws://localhost:8080` (or `wss://` for secure connections)

### Connection Flow

1. Client connects to WebSocket server
2. Server immediately sends `clientConfig` JSON
3. Client sends identification info (`clientInfo`)
4. Server assigns a unique `connectionId`
5. Client sends metadata as JSON string (for upload)
6. Server creates output file stream
7. Client sends binary video data
8. Server writes data to file and closes stream

### WebSocket Message Protocol

#### 1. Client Identification Message (String/JSON)

Client sends identification info upon connecting:

**Format:**
```json
{
  "type": "clientInfo",
  "clientName": "Lab Camera 1 (iPhone 14 Pro)"
}
```

#### 2. Client Configuration Message (Server -> Client)

Sent immediately upon connection.

**Format:**
```json
{
  "type": "clientConfig",
  "config": {
    "videoFormat": "mp4",
    "videoChunkDurationMs": 10000,
    "videoBufferMaxMB": 16000
  }
}
```

#### 3. Command Message (Server -> Client)

**Upload Time Range Command:**
```json
{
  "command": "uploadTimeRange",
  "startTime": 1700000000.0,
  "endTime": 1700000060.0
}
```

**Client Behavior:**
- Scans for local files overlapping the time range
- Uploads matching files

---

#### 4. Metadata Message (String/JSON)

Client sends metadata for the file upload:

```json
{
  "type": "upload",
  "filename": "video_10002930.mp4",
  "filesize": 1048576,
  "mimetype": "video/mp4"
}
```

**Server Response on Success:**
- Acknowledgment is implicit; server begins accepting binary data

#### 5. Binary Data Messages (Blob)

Client sends raw binary data (video file contents) after metadata.

**Server Processing:**
- Writes data to file stream inside `uploads/mobile_clients`
- On completion, sends success response

```json
{
  "status": "success",
  "message": "Video uploaded successfully"
}
```
