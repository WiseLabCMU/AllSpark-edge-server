# Perceptor Host Test Server

This server provides HTTP and WebSocket endpoints for testing perceptor functionality, including video file uploads and remote command execution.

## Requirements
- Node
- OpenSSL

## Features
- **HTTP & WebSocket Server**: Handles connection upgrades and video stream uploads.
- **Bonjour/mDNS Advertising**: Automatically advertises service as `_allspark._tcp` for client discovery.
- **Client Configuration Sync**: Pushes configuration (chunk duration, storage limits) to connected clients.
- **Remote Commands**: Send commands to clients to request video uploads for specific time ranges.

## Configuration

The server reads configuration from `config.json` in the same directory. If the file is not found, defaults are used:

```json
{
  "hostname": "localhost",
  "port": 8080,
  "serviceName": "AllSpark Server",
  "keyFile": "keys/test-private.key",
  "certFile": "keys/test-public.crt",
  "uploadPath": "uploads/",
  "clientConfig": {
    "videoFormat": "mp4",
    "videoChunkDurationMs": 30000,
    "videoBufferMaxMB": 16000
  }
}
```

### Configuration Options
- **serviceName**: The name advertised via Bonjour (default: "AllSpark Server")
- **clientConfig**: Settings pushed to clients upon connection
  - **videoFormat**: Preferred video encoding ("mp4" or "mov")
  - **videoChunkDurationMs**: Duration of recording chunks in milliseconds
  - **videoBufferMaxMB**: Max storage usage on client before old files are deleted

## Testing

1. Generate a testing-only self-signed certificate to secure the websocket transport (you only need to do this once):
  ```bash
  mkdir keys
  openssl req \
      -new \
      -newkey rsa:2048 \
      -days 365 \
      -nodes \
      -x509 \
      -subj "/CN=localhost" \
      -keyout keys/test-private.key \
      -out keys/test-public.crt
  ```

2. Launch the Perceptor Server Test:
  ```bash
  node server.js
  ```

3. Quick WebSocket test using [`websocat`](https://github.com/vi/websocat?tab=readme-ov-file#installation):
  ```bash
  websocat --insecure wss://localhost:8080
  ```
  or
  ```bash
  websocat ws://localhost:8080
  ```

## HTTP Endpoints

### GET `/`
Serves the HTML interface from `index.html`.

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

---

#### 2. Metadata Message (String/JSON)

Client sends metadata for the file upload:

```json
{
  "filename": "video.mp4",
  "type": "video/mp4"
}
```

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

## Upload Directory

Uploaded files are stored in the `uploads/` directory, which is created automatically if it doesn't exist.

## Web Interface (index.html)

The server provides a web-based control interface at `http://localhost:8080` for monitoring connections and sending remote commands.

### Features

1. **Active Connections List**
   - Shows all connected clients with their display names
   - Device names automatically sent by clients (customizable in Settings)
   - Format: "CustomName (DeviceModel)" or just "DeviceModel"
   - Connection ID shown in smaller text below the name
   - Displays metadata status and received data status
   - Real-time updates every 5 seconds

2. **Request Upload Time Range**
   - **Start Time / End Time**: Date and time pickers to define the range of video to request.
   - **Quick Presets**: "Last 1 min", "Last 5 mins", "Last 1 hour", "Now".
   - **Request Upload Button**: Sends the `uploadTimeRange` command to the client.
   - **Persistence**: Remembers selected times per connection ID.

### Example Workflow

1. Navigate to `http://localhost:8080` in a web browser
2. View connected iOS devices in the "Active Connections" section
3. Select a time range (e.g., "Last 5 mins") using the preset buttons or date pickers
4. Click "Request Upload Time Range"
5. Client receives command and automatically:
   - Checks local storage for recordings within that range
   - Uploads any matching files to the server
   - Files appear in `uploads/` directory on the server

## Troubleshooting

**Connection not found:**
- Verify the `connectionId` is correct via `/api/status`
- Ensure the client connection is still active

**File write errors:**
- Check that the `uploads/` directory is writable
- Verify disk space is available

**Binary data before metadata:**
- Ensure metadata JSON is sent first before any binary data
- Server will reject binary data received before metadata with an error message
