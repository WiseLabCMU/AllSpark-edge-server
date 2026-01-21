# Perceptor Host Test Server

This server provides HTTP and WebSocket endpoints for testing perceptor functionality, including video file uploads and remote command execution.

## Requirements
- Node
- OpenSSL

## Configuration

The server reads configuration from `config.json` in the same directory. If the file is not found, defaults are used:

```json
{
  "hostname": "localhost",
  "port": 8080,
  "keyFile": "keys/test-private.key",
  "certFile": "keys/test-public.crt",
  "uploadPath": "uploads/"
}
```

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
      -out  keys/test-public.crt
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

4. Launch the iOS AllSpark Mobile app, and make sure to use the same port and and IP address in the `Settings` tab of the machine you are running `server.js` on. The `Camera` tab will make the websocket connection automatically. You may need to query your own IP address with something like:
  ```bash
  ifconfig | awk '/inet / {sub(/\/.*/, "", $2); print $2}' | tail -1
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

**Request Body for Record Command:**
```json
{
  "command": "record",
  "message": "optional message content",
  "duration": 5000,
  "autoUpload": true
}
```

**Request Body for Generic Command:**
```json
{
  "command": "command_name",
  "message": "optional message content"
}
```

**Command Parameters:**
- `command` (required): The command type (e.g., `"record"`)
- `message` (optional): Additional context or instructions
- `duration` (optional, record command only): Recording duration in milliseconds (default: 30000)
- `autoUpload` (optional, record command only): Whether to auto-upload after recording (default: false)

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

#### 2. Command Message from Server (String/JSON)

Server sends commands to client:

**Record Command (with optional duration and auto-upload):**
```json
{
  "command": "record",
  "message": "optional message content",
  "duration": 5000,
  "autoUpload": true
}
```

**Parameters:**
- `command`: `"record"` - requests client to start recording
- `message` (optional): Additional context displayed to user
- `duration` (optional): Recording duration in milliseconds (default: 30000 = 30 seconds)
- `autoUpload` (optional): Auto-upload after recording (default: false)

**Client Behavior:**
- Immediately starts recording video
- Records for specified duration
- Automatically stops when duration expires
- If `autoUpload` is true, uploads the recorded file to server
- If `autoUpload` is false, saves file locally without uploading
- Displays alert showing command parameters

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

2. **Send Record Command**
   - **Duration Input**: Set recording duration in milliseconds (default: 30000)
   - **Auto Upload Checkbox**: Enable/disable automatic upload after recording (default: checked/true)
   - **Send Record Command Button**: Transmit the command to the selected connection
   - Both fields persist across page refreshes

3. **Command Confirmation**
   - Displays alert with connection ID, duration, and auto-upload setting
   - Confirms successful delivery to client

### Example Workflow

1. Navigate to `http://localhost:8080` in a web browser
2. View connected iOS devices in the "Active Connections" section
3. For each connection, optionally adjust the Duration (e.g., 10000 for 10 seconds)
4. Check/uncheck the "Auto Upload" checkbox based on desired behavior
5. Click "Send Record Command" to trigger remote recording
6. Client receives command and automatically:
   - Starts recording for specified duration
   - Stops recording when time expires
   - (If auto-upload enabled) Uploads recorded video to server

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
