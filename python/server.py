import json
import logging
import os
import socket
import ssl
import sys
import time

from aiohttp import WSMsgType, web
from zeroconf import IPVersion, ServiceInfo, Zeroconf

# Allow imports from the sibling agent_service package regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_service import AgentApiClient, AnomalyResponseStore
from agent_service.models import AnomalyRequest

# Constants
CONFIG_FILE = "../config.json"
DEFAULT_CONFIG = {
    "hostname": "0.0.0.0",
    "port": 8080,
    "serviceName": "AllSpark Server",
    "keyFile": "keys/test-private.key",
    "certFile": "keys/test-public.crt",
    "uploadPath": "uploads/",
    "agentResponsePath": "uploads/agent_responses/",
    "keepAliveIntervalMs": 5000,
    "agentConfig": {
        "agent_url": "http://localhost:8000/run",
        "agent_app_name": "allspark_agent",
        "agent_user_id": "edge_server_user",
        "agent_session_id": "edge_session",
        "agent_timeout": 300,
        "agent_init_message": "Hey, can you help me do some analysis?"
    },
    "clientConfig": {
        "videoFormat": "mp4",
        "videoChunkDurationMs": 30000,
        "videoBufferMaxMB": 16000,
        "communicationsPolicy": {
            "wifi": True,
            "cellular": True,
            "ethernet": True,
            "usb": True,
            "bluetooth": False,
            "airdrop": False,
            "nfc": False,
            "uwb": False,
            "satellite": False
        }
    }
}

# Global state
upload_states = {}
client_connections = {}
config = {}

# Agent service singletons – initialised in load_config()
_agent_client: AgentApiClient | None = None
_response_store: AnomalyResponseStore | None = None

def load_config():
    global config, _agent_client, _response_store
    config = DEFAULT_CONFIG.copy()
    config["agentConfig"] = DEFAULT_CONFIG["agentConfig"].copy()

    # Load user config if exists
    config_path = os.path.join(os.path.dirname(__file__), CONFIG_FILE)
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
                # Deep merge would be better, but simple update for now
                config.update(user_config)
                # Merge clientConfig specifically if present
                if "clientConfig" in user_config:
                    config["clientConfig"].update(user_config["clientConfig"])
                if "agentConfig" in user_config:
                    config["agentConfig"].update(user_config["agentConfig"])
            print(f"Loaded config from {config_path}")
        except Exception as e:
            print(f"Failed to load config: {e}")
    else:
        print("Using default config")
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
            print(f"Created config.json from internal defaults at {config_path}")
        except Exception as e:
            print(f"Failed to create default config.json: {e}")

    # Initialise agent singletons
    _agent_client = AgentApiClient(config.get("agentConfig", {}))
    response_path = resolve_path(config.get("agentResponsePath", "uploads/agent_responses/"))
    _response_store = AnomalyResponseStore(response_path)
    print(f"Agent client initialised. Responses stored at: {response_path}")

def get_project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def resolve_path(path_str):
    if os.path.isabs(path_str):
        return path_str
    return os.path.join(get_project_root(), path_str)

async def handle_index(request):
    index_path = resolve_path("index.html")
    if os.path.exists(index_path):
        return web.FileResponse(index_path)
    return web.Response(text="index.html not found", status=404)

def get_local_ip():
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    return local_ip

async def handle_health(request):
    return web.json_response({
        "status": "ok",
        "timestamp": time.time(),
        "uptime": time.time() - start_time,
        "protocols": ["wss"] if use_ssl else ["ws"],
        "address": get_local_ip(),
        "port": config["port"]
    })

async def handle_status(request):
    connections = []
    for cid, state in upload_states.items():
        connections.append({
            "id": cid,
            "clientName": state.get("clientName", "Unknown Device"),
            "lastFilename": state.get("lastFilename"),
            "lastFilesize": state.get("lastFilesize")
        })

    return web.json_response({
        "totalConnections": len(upload_states),
        "connections": connections
    })

async def handle_config(request):
    return web.json_response(config.get("clientConfig", {}))

async def handle_agent_analyze(request):
    """
    POST /api/agent/analyze

    Accepts anomaly metadata, calls the AllSpark Agentic Framework, stores
    the response to disk, and returns the result as JSON.

    Request body (JSON):
        clip_path          (str, required)  – path to the anomaly video clip
        log_path           (str, optional)  – path to an associated log file
        anomaly_time       (str, required)  – ISO-8601 anomaly timestamp
        clip_start_time    (str, optional)
        clip_start_timestamp (str, optional)
        error              (str, optional)
        expected_topic     (str, optional)
        mqtt_clip_messages (list, optional)
        video_storage_path (str, optional)
        device_name        (str, optional)  – used to organise stored responses
        extra_metadata     (dict, optional)
    """
    global _agent_client, _response_store

    if _agent_client is None or _response_store is None:
        return web.json_response(
            {"success": False, "error": "Agent service not initialised"},
            status=503,
        )

    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"success": False, "error": "Invalid JSON request body"}, status=400
        )

    clip_path = body.get("clip_path", "")
    anomaly_time = body.get("anomaly_time", "")

    if not clip_path or not anomaly_time:
        return web.json_response(
            {"success": False, "error": "clip_path and anomaly_time are required"},
            status=400,
        )

    anomaly_request = AnomalyRequest(
        clip_path=clip_path,
        log_path=body.get("log_path", ""),
        anomaly_time=anomaly_time,
        clip_start_time=body.get("clip_start_time", ""),
        clip_start_timestamp=str(body.get("clip_start_timestamp", "")),
        error=body.get("error", "N/A"),
        expected_topic=body.get("expected_topic", "N/A"),
        mqtt_clip_messages=body.get("mqtt_clip_messages", []),
        video_storage_path=body.get("video_storage_path", ""),
        extra_metadata=body.get("extra_metadata", {}),
    )

    print(f"[agent/analyze] Received request for clip: {clip_path}")

    agent_response = await _agent_client.analyze_anomaly(anomaly_request)

    device_name = body.get("device_name", "default")
    stored_at = _response_store.save(
        agent_response,
        anomaly_request,
        device_name,
        agent_config=config.get("agentConfig"),
    )

    print(
        f"[agent/analyze] Analysis complete. Status={agent_response.status}. "
        f"Stored at: {stored_at}"
    )

    return web.json_response(
        {
            "success": agent_response.is_success,
            "request_id": agent_response.request_id,
            "session_id": agent_response.session_id,
            "status": agent_response.status,
            "summary": agent_response.summary,
            "stored_at": stored_at,
            "error_message": agent_response.error_message,
        }
    )


async def handle_agent_responses(request):
    """
    GET /api/agent/responses?device_name=<name>&limit=<n>

    Returns a list of stored agent responses, newest first.
    """
    global _response_store

    if _response_store is None:
        return web.json_response(
            {"success": False, "error": "Agent service not initialised"},
            status=503,
        )

    device_name = request.rel_url.query.get("device_name")
    try:
        limit = int(request.rel_url.query.get("limit", 50))
    except ValueError:
        limit = 50

    responses = _response_store.list_responses(device_name=device_name, limit=limit)
    return web.json_response(
        {
            "success": True,
            "count": len(responses),
            "responses": [r.to_dict() for r in responses],
        }
    )


async def handle_agent_response_detail(request):
    """
    GET /api/agent/responses/{stored_at_b64}

    Returns a single stored AgentResponse by its stored_at path
    (URL-safe base64 encoded).
    """
    import base64

    global _response_store

    if _response_store is None:
        return web.json_response(
            {"success": False, "error": "Agent service not initialised"},
            status=503,
        )

    encoded = request.match_info.get("stored_at_b64", "")
    try:
        stored_at = base64.urlsafe_b64decode(encoded.encode()).decode()
    except Exception:
        return web.json_response(
            {"success": False, "error": "Invalid stored_at encoding"}, status=400
        )

    response = _response_store.get_response(stored_at)
    if response is None:
        return web.json_response(
            {"success": False, "error": "Response not found"}, status=404
        )

    return web.json_response({"success": True, "response": response.to_dict()})


async def handle_command_post(request):
    connection_id = request.match_info.get('connection_id')

    if connection_id not in client_connections:
        return web.json_response({"success": False, "error": "Connection not found or closed"}, status=404)

    try:
        data = await request.json()
    except:
        return web.json_response({"success": False, "error": "Invalid request body"}, status=400)

    ws = client_connections[connection_id]

    message = {
        "command": data.get("command"),
        "message": data.get("message", "")
    }

    if message["command"] == "uploadTimeRange":
        if "startTime" not in data or "endTime" not in data:
            return web.json_response({"success": False, "error": "Missing startTime or endTime"}, status=400)
        message["startTime"] = data["startTime"]
        message["endTime"] = data["endTime"]

    try:
        await ws.send_json(message)
        return web.json_response({"success": True, "message": "Command sent"})
    except Exception as e:
        return web.json_response({"success": False, "error": f"Failed to send message: {str(e)}"}, status=500)

async def websocket_handler(request):
    ws = web.WebSocketResponse(max_msg_size=314572800)
    await ws.prepare(request)

    connection_id = os.urandom(4).hex()
    client_connections[connection_id] = ws
    upload_states[connection_id] = {
        "metadata": None,
        "file_handle": None,
        "receivedData": False,
        "clientName": None,
        "lastFilename": None,
        "lastFilesize": None
    }

    print(f"Client connected: {connection_id}")

    # Send client configuration
    if "clientConfig" in config:
        await ws.send_json({
            "type": "clientConfig",
            "config": config["clientConfig"]
        })
        print(f"Sent config to {connection_id}")

    try:
        async for msg in ws:
            state = upload_states[connection_id]

            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    print(f"Received message from {connection_id}:\n{json.dumps(data, indent=2)}")

                    if data.get("type") == "clientInfo":
                        state["clientName"] = data.get("clientName")
                        print(f"Client identified as: {state['clientName']}")

                    elif data.get("type") == "test":
                        await ws.send_json({"status": "success", "message": "Test message received"})

                    elif data.get("type") == "chunkSaved":
                        # Add sender's connectionId so agents know who to request uploads from
                        broadcast_data = dict(data)
                        broadcast_data["connectionId"] = connection_id

                        # Broadcast to connected agents
                        for cid, client_ws in client_connections.items():
                            if cid != connection_id:
                                client_state = upload_states.get(cid, {})
                                if client_state.get("clientName") == "agent":
                                    try:
                                        await client_ws.send_json(broadcast_data)
                                    except Exception:
                                        pass
                        await ws.send_json({"status": "success", "message": "Chunk saved info received"})

                    elif data.get("type") == "upload":
                        if "filename" not in data:
                             await ws.send_json({"status": "error", "message": "Invalid upload metadata"})
                             continue

                        state["metadata"] = data
                        state["receivedData"] = False

                        # Extract timestamp from filename to align folder structure date
                        filename = data["filename"]
                        timestamp_sec = time.time()
                        try:
                            name_without_ext = os.path.splitext(filename)[0]
                            parts = name_without_ext.split('_')
                            parsed_ts = float(parts[-1])
                            timestamp_sec = parsed_ts
                        except Exception:
                            pass

                        # Sanitize client name string
                        import re
                        client_name = state.get("clientName") or "unknown"
                        safe_client_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', client_name)

                        import datetime
                        dt = datetime.datetime.fromtimestamp(timestamp_sec, tz=datetime.timezone.utc)
                        year = f"{dt.year:04d}"
                        month = f"{dt.month:02d}"
                        day = f"{dt.day:02d}"

                        # Prepare upload path
                        base_upload_path = resolve_path(config["uploadPath"])
                        target_dir = os.path.join(
                            base_upload_path,
                            "orgs", "default",
                            "devices", safe_client_name,
                            year,
                            month,
                            day
                        )
                        os.makedirs(target_dir, exist_ok=True)

                        filepath = os.path.join(target_dir, filename)
                        state["filepath"] = filepath # Store for logging later
                        try:
                            state["file_handle"] = open(filepath, "wb")
                        except Exception as e:
                            print(f"Failed to open file for writing: {e}")
                            await ws.send_json({"status": "error", "message": "Failed to write file"})

                    else:
                        if not data.get("filename"):
                             await ws.send_json({"status": "error", "message": "Unknown message type"})

                except json.JSONDecodeError:
                    print("Invalid JSON received")
                    await ws.send_json({"status": "error", "message": "Invalid JSON"})

            elif msg.type == WSMsgType.BINARY:
                if not state["metadata"] or not state["file_handle"]:
                    print("Received binary data without metadata")
                    await ws.send_json({"status": "error", "message": "Metadata not received yet"})
                    continue

                try:
                    state["file_handle"].write(msg.data)
                    state["file_handle"].close()

                    filename = state["metadata"]["filename"]
                    filepath = state.get("filepath", os.path.join(resolve_path(config["uploadPath"]), filename))
                    filesize = len(msg.data)

                    state["lastFilename"] = filename
                    state["lastFilesize"] = filesize
                    state["file_handle"] = None
                    state["metadata"] = None

                    print(f"File uploaded successfully: {filepath} ({filesize} bytes)")
                    await ws.send_json({"status": "success", "message": "Video uploaded successfully"})

                except Exception as e:
                    print(f"Error writing video data: {e}")
                    await ws.send_json({"status": "error", "message": "Failed to write video data"})
                    if state["file_handle"]:
                        state["file_handle"].close()
                        state["file_handle"] = None

            elif msg.type == WSMsgType.ERROR:
                print(f"ws connection closed with exception {ws.exception()}")

    finally:
        print(f"Client disconnected: {connection_id}")
        if connection_id in upload_states:
            state = upload_states[connection_id]
            if state["file_handle"]:
                state["file_handle"].close()
            del upload_states[connection_id]
        if connection_id in client_connections:
            del client_connections[connection_id]

    return ws

async def register_zeroconf(port):
    zeroconf = Zeroconf()

    # Get local IP
    hostname = socket.gethostname()
    local_ip = "127.0.0.1"
    try:
        local_ip = socket.gethostbyname(hostname)
    except:
        pass

    # Service type
    start_type = "_allspark._tcp.local."

    info = ServiceInfo(
        start_type,
        f"{config['serviceName']}.{start_type}",
        addresses=[socket.inet_aton(local_ip)],
        port=port,
        properties={},
        server=f"{hostname}.local."
    )

    zeroconf.register_service(info)
    print(f"Registered Bonjour service: {config['serviceName']} on port {port}")
    return zeroconf, info

async def init_app():
    load_config()

    app = web.Application()

    app.router.add_get('/api/health', handle_health)
    app.router.add_get('/api/status', handle_status)
    app.router.add_get('/api/config', handle_config)
    app.router.add_post('/api/command/{connection_id}', handle_command_post)
    app.router.add_post('/api/agent/analyze', handle_agent_analyze)
    app.router.add_get('/api/agent/responses', handle_agent_responses)
    app.router.add_get('/api/agent/responses/{stored_at_b64}', handle_agent_response_detail)
    app.router.add_static('/third-party', path=os.path.join("..", "third-party"), name='third-party')

    # Actually wait, client connects to wss://host:port/. So root is correct for WS?
    # But I also have handle_index on root.
    # aiohttp handles this if Upgrade header is present.
    # We can share the route.

    # Note: aiohttp separation of WS and HTTP on same URL needs middleware or check in handler.
    # Let's keep it simple: if upgrade header, WS. Else index.
    # But router add_get takes a handler.

    async def root_handler(request):
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await websocket_handler(request)
        else:
            return await handle_index(request)

    app.router.add_get('/', root_handler)

    return app

if __name__ == '__main__':
    start_time = time.time()

    load_config()

    ssl_context = None
    use_ssl = False

    key_path = resolve_path(config.get("keyFile"))
    cert_path = resolve_path(config.get("certFile"))

    if os.path.exists(key_path) and os.path.exists(cert_path):
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        use_ssl = True
        print("SSL certificates loaded successfully")
    else:
        print("\033[33mSSL keys not found, using HTTP\033[0m")

    # Need local IP
    local_ip = get_local_ip()

    protocol = "https" if use_ssl else "http"
    ws_protocol = "wss" if use_ssl else "ws"
    print(f"Server is running on {protocol}://{config['hostname']}:{config['port']}")
    print(f"WebSocket endpoint: {ws_protocol}://{local_ip}:{config['port']}")
    print(f"Setting keep-alive interval to {config['keepAliveIntervalMs']}ms")

    # Start Zeroconf
    zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
    try:
        info = ServiceInfo(
            "_allspark._tcp.local.",
            f"{config['serviceName']}._allspark._tcp.local.",
            addresses=[socket.inet_aton(local_ip)],
            port=config['port'],
            properties={},
            server=f"{local_ip}.local."
        )
        zeroconf.register_service(info)
        print(f"Advertising Bonjour service: {config['serviceName']} on {local_ip}:{config['port']}")
    except Exception as e:
        print(f"Failed to start Zeroconf: {e}")

    # Silence noisy SSL handshake errors on HTTP port
    class TLSHandshakeFilter(logging.Filter):
        def filter(self, record):
            if record.exc_info:
                exc_type, exc_value, _ = record.exc_info
                if "BadStatusLine" in str(exc_type) and "Invalid method encountered" in str(exc_value):
                    # Check for TLS handshake bytes (0x16 = Handshake, 0x03 = SSL 3.0/TLS 1.x)
                    if "\\x16\\x03\\x01" in str(exc_value) or "\\x16\\x03\\x03" in str(exc_value):
                        return False
            return True

    logging.basicConfig(level=logging.INFO)
    aiohttp_logger = logging.getLogger("aiohttp.server")
    aiohttp_logger.addFilter(TLSHandshakeFilter())

    try:
        web.run_app(init_app(), port=config["port"], ssl_context=ssl_context, access_log=None)
    except KeyboardInterrupt:
        pass
    finally:
        zeroconf.unregister_service(info)
        zeroconf.close()
