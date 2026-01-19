# Perceptor Host Test Server

This server provides HTTP and WebSocket endpoints for testing perceptor functionality, including video file uploads and remote command execution.

## Configuration

The server reads configuration from `config.json` in the same directory. If the file is not found, defaults are used:

```json
{
  "hostname": "localhost",
  "port": 8080
}
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
      "hasMetadata": true,
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

**Request Body:**
```json
{
  "command": "command_name",
  "message": "optional message content"
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
2. Server assigns a unique `connectionId` to the connection
3. Client sends metadata as JSON string
4. Server creates output file stream
5. Client sends binary video data
6. Server writes data to file and closes stream

### WebSocket Message Protocol

#### 1. Metadata Message (String/JSON)

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

#### 2. Binary Data Messages (Blob)

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

## Testing

Quick WebSocket test using `websocat`:
```bash
websocat ws://localhost:8080
```

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
