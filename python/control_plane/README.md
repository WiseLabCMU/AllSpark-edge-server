# AllSpark Control Plane

This directory contains the NiceGUI-based reactive control plane for the AllSpark Edge Server.

The control plane runs as a decoupled **sidecar** UI alongside the core edge network server. This separation ensures the high-throughput WebSocket operations of the main node remain unaffected by the UI data rendering.

## Setup Requirements

The control plane shares dependencies with the Python Edge Server. Ensure all dependencies are installed:

```bash
cd ../  # Navigate up to the python edge server root
pip install -r requirements.txt
```

> **Note**: The core edge server must run to generate the default `config.yaml` before the control plane can read the settings correctly.

## Running the Servers (Order of Operations)

Because the control plane binds asynchronously and depends on the main edge configuration and API (`/api/status`), you should boot up the edge server **first**.

**Step 1. Run the Core Edge Server**
In your first terminal:
```bash
cd allspark_agent/edge_server/python
python server.py
```
*You should see output indicating it is running on `http://0.0.0.0:8080` (and `wss://...`). If this is the first run, it will automatically generate a default `config.yaml` in the `edge_server/` root.*

**Step 2. Run the Control Plane Sidecar**
In a second terminal:
```bash
cd allspark_agent/edge_server/python
python control_plane/main.py
```
*NiceGUI will automatically read your `config.yaml` and start the remote dashboard on `http://127.0.0.1:8081` (Edge Server Port + 1).*

**Step 3. (Optional) Run the Mock Rerun.io Viewer**
To populate the `/rerun` visualization iframe in the control plane dashboard with dummy data until the true data plane is integrated, launch the rerun mock script in a third terminal:
```bash
cd allspark_agent/edge_server/python
python control_plane/dummy_rerun_server.py
```
*This will spin up a local rerun web viewer on port `9090` which the control plane will iFrame natively.*

## Key Features

1. **Reactive Dashboard:** Monitors your local MQTT anomaly topic streams on `127.0.0.1:1883` in real-time.
2. **Client Health UI:** Actively polls the `/api/status` endpoint to display connected mobile rigs and trigger on-demand data uploads.
3. **Capture Browser:** Traverses the local `.quic`, `.mp4`, and `.json` files inside your edge upload directory and streams them natively to the browser.
4. **Agent Testing Stub:** Quick shortcut for sending anomalies to the main Agentic framework for resolution.
