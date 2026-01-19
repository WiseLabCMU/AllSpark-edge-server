// quick test:
// websocat ws://localhost:8080

const WebSocket = require("ws");
const fs = require("fs");
const path = require("path");
const http = require("http");
const https = require("https");

// Load configuration
let config;
let useSSL = false;
let protocols = ["ws"];

try {
  const configFile = path.join(__dirname, "config.json");
  const configData = fs.readFileSync(configFile, "utf8");
  config = JSON.parse(configData);
} catch (err) {
  console.error("Failed to load config.json, using defaults:", err);
  config = {
    hostname: "localhost",
    port: 8080
  };
}

// Try to load SSL certificates if specified in config
let serverOptions = {};
if (config.keyFile && config.certFile) {
  try {
    const keyPath = path.join(__dirname, config.keyFile);
    const certPath = path.join(__dirname, config.certFile);

    if (fs.existsSync(keyPath) && fs.existsSync(certPath)) {
      serverOptions.key = fs.readFileSync(keyPath);
      serverOptions.cert = fs.readFileSync(certPath);
      useSSL = true;
      protocols = ["wss"];
      console.log("SSL certificates loaded successfully");
    }
  } catch (err) {
    console.warn("Failed to load SSL certificates:", err.message);
  }
}

const server = useSSL ? https.createServer(serverOptions, requestHandler) : http.createServer(requestHandler);

function requestHandler(req, res) {
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
  } else if (req.method === "GET" && req.url === "/api/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({
      status: "ok",
      timestamp: new Date().toISOString(),
      uptime: process.uptime(),
      protocols: protocols
    }));
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
  } else if (req.method === "POST" && req.url.startsWith("/api/command/")) {
    const connectionId = req.url.substring("/api/command/".length);
    const ws = clientConnections.get(connectionId);

    if (!ws || ws.readyState !== WebSocket.OPEN) {
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ success: false, error: "Connection not found or closed" }));
      return;
    }

    let body = "";
    req.on("data", (chunk) => {
      body += chunk.toString();
    });
    req.on("end", () => {
      try {
        const data = JSON.parse(body);
        const message = JSON.stringify({
          command: data.command,
          message: data.message || ""
        });

        ws.send(message, (err) => {
          if (err) {
            res.writeHead(500, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ success: false, error: "Failed to send message" }));
          } else {
            res.writeHead(200, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ success: true, message: "Command sent" }));
          }
        });
      } catch (err) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ success: false, error: "Invalid request body" }));
      }
    });
  } else {
    res.writeHead(404, { "Content-Type": "text/plain" });
    res.end("404 - Not Found");
  }
}

const wss = new WebSocket.Server({ server });

// Store upload state and WebSocket reference per connection
const uploadStates = new Map();
const clientConnections = new Map();

wss.on("connection", function connection(ws) {
  const connectionId = Math.random().toString(36).substr(2, 9);
  uploadStates.set(connectionId, {
    metadata: null,
    fileStream: null,
    receivedData: false
  });
  clientConnections.set(connectionId, ws);
  console.log(`Client connected ${connectionId}`);

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
      // First message: metadata or test message as JSON string
      try {
        const parsedMessage = JSON.parse(message);
        console.log(`Received message:`, parsedMessage);

        // Check if this is a test message
        if (parsedMessage.type === "test") {
          ws.send(JSON.stringify({ status: "success", message: "Test message received" }));
          console.log("Test message acknowledged");
          return;
        }

        // Otherwise treat as metadata for upload
        if (parsedMessage.type !== "upload" && !parsedMessage.filename) {
          ws.send(JSON.stringify({ status: "error", message: "Invalid message format. Expected type: 'upload' or 'test'" }));
          console.error("Invalid message format");
          return;
        }

        state.metadata = parsedMessage;

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
        console.error("Failed to parse message:", err);
        ws.send(JSON.stringify({ status: "error", message: "Invalid JSON format" }));
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
    clientConnections.delete(connectionId);
    console.log(`Client disconnected ${connectionId}`);
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
server.listen(config.port, config.hostname, () => {
  const protocol = useSSL ? "https" : "http";
  console.log(`Server is running on ${protocol}://${config.hostname}:${config.port}`);
  console.log(`WebSocket endpoint: ${protocols[0]}://${config.hostname}:${config.port}`);
});
