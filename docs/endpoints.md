# AllSpark Edge Server - Endpoints

## HTTP Endpoints

### GET `/`
Serves the HTML interface from `../index.html`.

**Response:**
- Status: `200`
- Content-Type: `text/html; charset=utf-8`
- Body: HTML file contents

**Error Handling:**
- Returns `500` if `index.html` cannot be read

---

### GET `/api/health`
Health check endpoint that returns server status and uptime information.

**Response:**
```json
{
  "status": "ok",
  "timestamp": "2026-01-18T12:34:56.789Z",
  "uptime": 123.45
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
      "clientName": "Lab Camera 1 (iPhone 14 Pro)",
      "filename": "video.mp4",
      "receivedData": true
    }
  ]
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

**Command Parameters:**
- `command` (required): The command type (`"uploadTimeRange"`)
- `startTime` (required for uploadTimeRange): Start timestamp (Unix epoch seconds)
- `endTime` (required for uploadTimeRange): End timestamp (Unix epoch seconds)
- `message` (optional): Additional context for the user

**Success Response:**
```json
{
  "success": true,
  "message": "Command sent"
}
```

**Status:** `200`
**Content-Type:** `application/json`

**Error Responses:**

1. Connection not found or closed:
   - Status: `404`
   - Body: `{ "success": false, "error": "Connection not found or closed" }`

2. Failed to send message:
   - Status: `500`
   - Body: `{ "success": false, "error": "Failed to send message" }`

3. Invalid request body:
   - Status: `400`
   - Body: `{ "success": false, "error": "Invalid request body" }`

---

### Other Routes
Any request that doesn't match the above endpoints returns a `404` error.

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

**Parameters:**
- `type`: `"clientInfo"` - Identifies this as a client identification message
- `clientName`: Display name for this client, shown in server's web interface
  - Format: "CustomName (DeviceModel)" if custom name is set
  - Format: "DeviceModel" if no custom name is set

**Server Behavior:**
- Stores clientName for the connection
- Returns it in `/api/status` endpoint for display on web interface
- Helps identify which device is which in multi-client scenarios

---

#### 2. Client Configuration Message (Server -> Client)

Sent immediately upon connection.

**Format:**
```json
{
  "type": "clientConfig",
  "config": {
    "videoFormat": "mp4",
    "videoChunkDurationMs": 30000,
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

**Record Command (Generic):**
```json
{
  "command": "record",
  "message": "Optional additional context or instructions"
}
```

**Client Behavior:**
- Starts recording with optional duration and auto-upload
- Displays command notification to user

---

#### 2. Metadata Message (String/JSON)

Client sends metadata for the file upload:

```json
{
  "filename": "video.mp4",
  "filesize": 1048576,
  "mimetype": "video/mp4"
}
```

**Parameters:**
- `filename` (required): Name of the file to save
- `filesize` (optional): Size of the incoming binary file in bytes
- `mimetype` (optional): The MIME type of the file (e.g., `"video/mp4"` or `"video/quicktime"`)

**Server Response on Success:**
- Acknowledgment is implicit; server begins accepting binary data

**Server Response on Error:**
```json
{
  "status": "error",
  "message": "Invalid metadata format"
}
```

#### 3. Binary Data Messages (Blob)

Client sends raw binary data (video file contents) after metadata.

**Server Processing:**
- Writes data to file stream
- On completion, sends success response

**Server Response on Success:**
```json
{
  "status": "success",
  "message": "Video uploaded successfully"
}
```

**Server Response on Error:**
```json
{
  "status": "error",
  "message": "Failed to write video data"
}
```

### Event Handlers

#### `connection`
Fired when a new WebSocket client connects.
- Creates a unique `connectionId`
- Initializes upload state storage
- Sets up message, close, and error handlers

#### `message`
Fired when the server receives a message from a connected client.
- Detects whether message is JSON metadata or binary video data
- For metadata: Parses JSON and creates file write stream
- For binary data: Writes to file stream
- Creates `uploads/` directory if it doesn't exist

#### `close`
Fired when a client disconnects.
- Cleans up file streams if still open
- Removes connection state from memory

#### `error`
Fired when a WebSocket error occurs.
- Logs error details
- Cleans up associated file streams and connection state
