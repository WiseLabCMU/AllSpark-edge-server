import json
import yaml
import logging
import os
import socket
import ssl
import time

from aiohttp import WSMsgType, web
from zeroconf import IPVersion, ServiceInfo, Zeroconf

# Constants
CONFIG_FILE = "../config.yaml"
DEFAULT_CONFIG = {
    "hostname": "0.0.0.0",
    "port": 8080,
    "serviceName": "AllSpark Server",
    "keyFile": "keys/test-private.key",
    "certFile": "keys/test-public.crt",
    "uploadPath": "logs/data/mobile-client",
    "keepAliveIntervalMs": 5000,
    "clientConfig": {
        "videoFormat": "mp4",
        "videoChunkDurationMs": 10000,
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

def load_config():
    global config
    config = DEFAULT_CONFIG.copy()

    # Load user config if exists
    config_path = os.path.join(os.path.dirname(__file__), CONFIG_FILE)

    full_config = {}
    needs_save = False

    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                full_config = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Failed to load config: {e}")

    if "mobile_client" in full_config:
        mc_config = full_config["mobile_client"]
        config.update(mc_config)
        if "clientConfig" in mc_config and isinstance(mc_config["clientConfig"], dict):
            config["clientConfig"].update(mc_config["clientConfig"])
    else:
        print("mobile_client section missing in config.yaml. Generating it...")
        full_config["mobile_client"] = DEFAULT_CONFIG
        needs_save = True

    if needs_save:
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(full_config, f, default_flow_style=False, sort_keys=False)
            print(f"Updated config.yaml with mobile_client section at {config_path}")
        except Exception as e:
            print(f"Failed to update config.yaml: {e}")

    print(f"Loaded config from {config_path}")
def get_project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def resolve_path(path_str):
    if os.path.isabs(path_str):
        return path_str
    return os.path.join(get_project_root(), path_str)

async def handle_index(request):
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        return web.FileResponse(index_path)
    return web.Response(text="AllSpark Mobile Client Edge Server - API Active", status=200)

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

                        # Auto-upload feature
                        if config.get("autoUpload", False):
                            start_time = data.get("startTime", 0)
                            end_time = data.get("endTime", time.time())
                            print(f"AutoUpload enabled. Automatically requesting upload from {connection_id} for range {start_time}-{end_time}")
                            await ws.send_json({
                                "command": "uploadTimeRange",
                                "startTime": start_time,
                                "endTime": end_time
                            })

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
                        date_str = dt.strftime("%Y-%m-%d")

                        ext = os.path.splitext(filename)[1].lower()
                        if ext in ['.mp4', '.mov', '.mkv', '.avi', '.webm', '.ts']:
                            media_type = "video"
                        elif ext in ['.jpg', '.jpeg', '.png', '.gif']:
                            media_type = "image"
                        else:
                            media_type = "data"

                        # Prepare upload path
                        base_upload_path = resolve_path(config["uploadPath"])
                        target_dir = os.path.join(
                            base_upload_path,
                            date_str,
                            media_type,
                            safe_client_name
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

                    # Notify agents of new file if needed
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
    app.router.add_static('/third-party', path=resolve_path("third-party"), name='third-party')

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
