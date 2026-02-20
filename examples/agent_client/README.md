# AllSpark Edge Server - Examples

This directory contains examples for interacting with the AllSpark Edge Server natively.

## Agent Client Example

The `agent_client_example.py` script demonstrates how an agentic framework can connect to the AllSpark Edge Server using both the REST API and a persistent WebSocket connection.

As an "agent", it establishes a long-running WebSocket connection and identifies itself using `"clientName": "agent"`. This alerts the server to broadcast real-time network payloads directly to the agent socket! Currently, it allows the agent to immediately receive `chunkSaved` events whenever an iOS client finishes recording a video snippet.

### Requirements
- Python 3.7+
- `aiohttp` library

```bash
pip install -r requirements.txt
```

### Manual Verification Usage
1. Make sure your AllSpark Edge Server is running in the background.
    - Python Server: `cd ../../python && python server.py`
    - Node Server: `cd ../../node && node server.js`
2. Run the example script from this folder:
```bash
python agent_client_example.py
```

By default, the script connects to `http://127.0.0.1:8080`.

You can also pass an explicit server URL:
```bash
# Provide a custom server URL
python agent_client_example.py -u https://127.0.0.1:8443
```
3. Boot up the iOS app and connect it to the server.
4. Begin recording clips. When clips finish mapping, you will see the `chunkSaved` payloads output to your terminal instantly by the agent!
