// quick test:
// websocat ws://0.0.0.0:8080

const WebSocket = require("ws");
const fs = require("fs");
const path = require("path");
const http = require("http");
const https = require("https");
const { Bonjour } = require('bonjour-service');
const os = require("os");

// Load configuration
let config;
let useSSL = false;
let protocols = ["ws"];

// Hardcoded defaults
const defaultConfig = {
  hostname: "0.0.0.0",
  port: 8080,
  serviceName: "AllSpark Server",
  keyFile: "../keys/test-private.key",
  certFile: "../keys/test-public.crt",
  uploadPath: "../uploads/",
  keepAliveIntervalMs: 5000,
  clientConfig: {
    videoFormat: "mp4",
    videoChunkDurationMs: 30000,
    videoBufferMaxMB: 16000
  }
};

// Handle user config
const configFile = path.join(__dirname, "../config.json");
const configExists = fs.existsSync(configFile);

if (!configExists) {
  // Create config.json from defaults if it doesn't exist
  try {
    fs.writeFileSync(configFile, JSON.stringify(defaultConfig, null, 2));
    console.log("Created config.json from internal defaults");
  } catch (err) {
    console.warn("Failed to create config.json:", err.message);
  }
}

// Helper function for deep merge
function deepMerge(target, source) {
  for (const key in source) {
    if (source[key] instanceof Object && key in target) {
      Object.assign(source[key], deepMerge(target[key], source[key]));
    }
  }
  Object.assign(target || {}, source);
  return target;
}

// Load user config and merge with defaults
let userConfig = {};
try {
  const configData = fs.readFileSync(configFile, "utf8");
  userConfig = JSON.parse(configData);
  console.log("Loaded user config from config.json");
} catch (err) {
  console.error("Failed to load config.json:", err.message);
}

// Deep merge defaults into user config
// We want defaults to fill in gaps in userConfig, but userConfig to override values
// So we start with defaults, then overlay userConfig.
// Wait, deepMerge usage: deepMerge(target, source) -> modifies target.
// To fill defaults: deepMerge(defaultsCopy, userConfig)
config = JSON.parse(JSON.stringify(defaultConfig)); // Deep copy defaults
function mergeRecursive(target, source) {
  for (const p in source) {
    try {
      // Property in destination object set; update its value.
      if (source[p].constructor == Object) {
        target[p] = mergeRecursive(target[p], source[p]);
      } else {
        target[p] = source[p];
      }
    } catch (e) {
      // Property in destination object not set; create it and set its value.
      target[p] = source[p];
    }
  }
  return target;
}

config = mergeRecursive(config, userConfig);

// Update config.json on disk with any missing default values
// We actually want the reverse for updating the file: missing values from defaults added to userConfig
// But we don't want to overwrite user modifications.
// Strategy: merge defaults into userConfig, then save userConfig.
let updatedUserConfig = mergeRecursive(JSON.parse(JSON.stringify(defaultConfig)), userConfig);

try {
  // Check if we need to update the file (simple check: stringify comparison)
  // Note: this might reorder keys, but that's fine for JSON.
  // Actually, merged result 'updatedUserConfig' contains ALL keys.
  fs.writeFileSync(configFile, JSON.stringify(updatedUserConfig, null, 2));
  console.log("Updated config.json with merged defaults");
} catch (err) {
  console.error("Failed to update config.json:", err.message);
}

// Try to load SSL certificates if specified in config
let serverOptions = {};
if (config.keyFile && config.certFile) {
  try {

    const projectRoot = path.join(__dirname, "../");
    const keyPath = path.join(projectRoot, config.keyFile);
    const certPath = path.join(projectRoot, config.certFile);

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
    const htmlFile = path.join(__dirname, "../index.html");
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
      clientName: state.clientName || "Unknown Device",
      lastFilename: state.lastFilename,
      lastFilesize: state.lastFilesize
    }));
    res.end(JSON.stringify({
      totalConnections: uploadStates.size,
      connections
    }));
  } else if (req.method === "GET" && req.url === "/api/config") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(config.clientConfig || {}));
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
        const message = {
          command: data.command,
          message: data.message || ""
        };

        // For record command, optionally include duration (in milliseconds)
        // If not provided, client will default to 30 seconds (30000 ms)
        // For uploadTimeRange command, require startTime and endTime
        if (data.command === "uploadTimeRange") {
          if (data.startTime === undefined || data.endTime === undefined) {
             res.writeHead(400, { "Content-Type": "application/json" });
             res.end(JSON.stringify({ success: false, error: "Missing startTime or endTime" }));
             return;
          }
          message.startTime = data.startTime;
          message.endTime = data.endTime;
        }

        ws.send(JSON.stringify(message), (err) => {
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

// Keep-Alive Mechanism
function noop() {}

function heartbeat() {
  this.isAlive = true;
}

const keepAliveInterval = config.keepAliveIntervalMs;
console.log(`Setting keep-alive interval to ${keepAliveInterval}ms`);

const interval = setInterval(function ping() {
  wss.clients.forEach(function each(ws) {
    if (ws.isAlive === false) {
      console.log("Terminating inactive client");
      return ws.terminate();
    }

    ws.isAlive = false;
    ws.ping(noop);
  });
}, keepAliveInterval);

wss.on('close', function close() {
  clearInterval(interval);
});

// Store upload state and WebSocket reference per connection
const uploadStates = new Map();
const clientConnections = new Map();

wss.on("connection", function connection(ws) {
  ws.isAlive = true;
  ws.on('pong', heartbeat);

  const connectionId = Math.random().toString(36).substr(2, 9);
  uploadStates.set(connectionId, {
    metadata: null,
    fileStream: null,
    receivedData: false,
    clientName: null,
    lastFilename: null,
    lastFilesize: null
  });
  clientConnections.set(connectionId, ws);
  console.log(`Client connected ${connectionId}`);

  // Send client configuration immediately
  if (config.clientConfig) {
    ws.send(JSON.stringify({
      type: "clientConfig",
      config: config.clientConfig
    }));
    console.log(`Sent client config to ${connectionId}`);
  }

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
      // First message: metadata, test message, or client info as JSON string
      try {
        const parsedMessage = JSON.parse(message);
        console.log(`Received message:`, parsedMessage);

        // Check if this is a client info message
        if (parsedMessage.type === "clientInfo") {
          state.clientName = parsedMessage.clientName;
          console.log(`Client identified as: ${state.clientName}`);
          return;
        }

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
        state.receivedData = false;

        // Resolve upload path relative to project root if it's relative
        const projectRoot = path.join(__dirname, "../");
        // Check if config.uploadPath is absolute or relative
        const uploadDir = path.isAbsolute(config.uploadPath) ? config.uploadPath : path.join(projectRoot, config.uploadPath);

        // Create uploads directory if it doesn't exist
        if (!fs.existsSync(uploadDir)) {
          fs.mkdirSync(uploadDir, { recursive: true });
        }

        // Create write stream for the video file
        const filepath = path.join(uploadDir, state.metadata.filename);
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

      const currentStream = state.fileStream;
      const currentMetadata = state.metadata;

      if (currentStream) {
        currentStream.write(message, (err) => {
          if (err) {
            console.error("Error writing video data:", err);
            ws.send(JSON.stringify({ status: "error", message: "Failed to write video data" }));
          } else {
            currentStream.end();

            const projectRoot = path.join(__dirname, "../");
            const uploadDir = path.isAbsolute(config.uploadPath) ? config.uploadPath : path.join(projectRoot, config.uploadPath);
            const filepath = path.join(uploadDir, currentMetadata.filename);
            // Store the last filename and filesize
            state.lastFilename = currentMetadata.filename;
            state.lastFilesize = currentMetadata.filesize || message.length;
            console.log(`File uploaded successfully: ${filepath}`);
            ws.send(JSON.stringify({ status: "success", message: "Video uploaded successfully" }));

            // Reset state for next upload ONLY if it hasn't changed (started new upload)
            if (state.metadata === currentMetadata) {
                state.metadata = null;
            }
            if (state.fileStream === currentStream) {
                state.fileStream = null;
                state.receivedData = false;
            } else {
                console.log("State changed during write, preserving new state.");
            }
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

  // Advertise service via Bonjour
  const bonjour = new Bonjour();
  const serviceName = config.serviceName;

  // Find local IP
  let localIP = "0.0.0.0";
  const interfaces = os.networkInterfaces();
  for (const name of Object.keys(interfaces)) {
    for (const iface of interfaces[name]) {
      if (iface.family === 'IPv4' && !iface.internal) {
        localIP = iface.address;
        break;
      }
    }
    if (localIP !== "0.0.0.0") break;
  }

  console.log(`Server is running on ${protocol}://${config.hostname}:${config.port}`);
  console.log(`WebSocket endpoint: ${protocols[0]}://${localIP}:${config.port}`);
  console.log(`Advertising Bonjour service: ${serviceName} on ${localIP}:${config.port}`);

  bonjour.publish({
    name: serviceName,
    type: 'allspark',
    port: config.port,
    protocol: 'tcp'
  });
});
