const WebSocket = require("ws");
const fs = require("fs");
const path = require("path");
const http = require("http");

const server = http.createServer((req, res) => {
  // Handle HTTP requests
  if (req.method === "GET" && req.url === "/") {
    const htmlFile = path.join(__dirname, "index.html");
    fs.readFile(htmlFile, "utf8", (err, htmlContent) => {
      if (err) {
        res.writeHead(500, { "Content-Type": "text/plain" });
        res.end("500 - Internal Server Error");
        console.error("Error reading index.html:", err);
        return;
      }
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(htmlContent);
    });
  } else if (req.method === "GET" && req.url === "/api/status") {
    res.writeHead(200, { "Content-Type": "application/json" });
    const connections = Array.from(uploadStates.entries()).map(([id, state]) => ({
      id,
      hasMetadata: state.metadata !== null,
      filename: state.metadata?.filename || null,
      receivedData: state.receivedData
    }));
    res.end(JSON.stringify({
      totalConnections: uploadStates.size,
      connections
    }));
  } else {
    res.writeHead(404, { "Content-Type": "text/plain" });
    res.end("404 - Not Found");
  }
});

const wss = new WebSocket.Server({ server });

// Store upload state per connection
const uploadStates = new Map();

wss.on("connection", function connection(ws) {
  const connectionId = Math.random().toString(36).substr(2, 9);
  uploadStates.set(connectionId, {
    metadata: null,
    fileStream: null,
    receivedData: false
  });

  ws.on("message", function incoming(message) {
    const state = uploadStates.get(connectionId);

    console.log(`Message received - Type: ${typeof message}, IsBuffer: ${Buffer.isBuffer(message)}, Length: ${message.length || message.toString().length}`);

    // Check if message is a string (metadata)
    let isStringMessage = false;
    if (typeof message === "string") {
      isStringMessage = true;
    } else if (Buffer.isBuffer(message)) {
      // Try to detect if it's JSON metadata encoded as buffer
      try {
        const decoded = message.toString("utf8");
        JSON.parse(decoded);
        isStringMessage = true;
        message = decoded;
      } catch (e) {
        // It's binary data
        isStringMessage = false;
      }
    }

    if (isStringMessage) {
      // First message: metadata as JSON string
      try {
        state.metadata = JSON.parse(message);
        console.log(`Received metadata:`, state.metadata);

        // Create uploads directory if it doesn't exist
        if (!fs.existsSync("uploads")) {
          fs.mkdirSync("uploads", { recursive: true });
        }

        // Create write stream for the video file
        const filepath = path.join("uploads", state.metadata.filename);
        state.fileStream = fs.createWriteStream(filepath);

        state.fileStream.on("error", (err) => {
          console.error("File write error:", err);
          ws.send(JSON.stringify({ status: "error", message: "Failed to write file" }));
        });
      } catch (err) {
        console.error("Failed to parse metadata:", err);
        ws.send(JSON.stringify({ status: "error", message: "Invalid metadata format" }));
      }
    } else {
      // Binary video data
      if (!state.metadata) {
        console.error("Received video data before metadata - waiting for metadata first");
        ws.send(JSON.stringify({ status: "error", message: "Metadata not received yet. Please send metadata first." }));
        return;
      }

      if (state.fileStream) {
        state.fileStream.write(message, (err) => {
          if (err) {
            console.error("Error writing video data:", err);
            ws.send(JSON.stringify({ status: "error", message: "Failed to write video data" }));
          } else {
            state.receivedData = true;
            state.fileStream.end();

            const filepath = path.join("uploads", state.metadata.filename);
            console.log(`File uploaded successfully: ${filepath}`);
            ws.send(JSON.stringify({ status: "success", message: "Video uploaded successfully" }));

            // Reset state for next upload on the same connection
            state.metadata = null;
            state.fileStream = null;
            state.receivedData = false;
          }
        });
      }
    }
  });

  ws.on("close", function close() {
    const state = uploadStates.get(connectionId);
    if (state && state.fileStream && !state.receivedData) {
      state.fileStream.destroy();
    }
    uploadStates.delete(connectionId);
    console.log("Client disconnected");
  });

  ws.on("error", function error(err) {
    console.error("WebSocket error:", err);
    const state = uploadStates.get(connectionId);
    if (state && state.fileStream) {
      state.fileStream.destroy();
    }
    uploadStates.delete(connectionId);
  });
});

// Start the HTTP server
server.listen(8080, () => {
  console.log("Server is running on http://localhost:8080");
});
