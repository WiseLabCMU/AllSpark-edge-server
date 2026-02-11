import asyncio
import json
import os
import ssl
import sys
import time
from aiohttp import web, WSMsgType
from zeroconf import Zeroconf, ServiceInfo
import netifaces
import socket

# Constants
CONFIG_FILE = "../config.json"
DEFAULT_CONFIG = {
    "hostname": "0.0.0.0",
    "port": 8888,
    "serviceName": "AllSpark Server",
    "keyFile": "keys/test-private.key",
    "certFile": "keys/test-public.crt",
    "uploadPath": "uploads/",
    "keepAliveIntervalMs": 5000,
    "clientConfig": {
        "videoFormat": "mp4",
        "videoChunkDurationMs": 30000,
        "videoBufferMaxMB": 16000
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
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                user_config = json.load(f)
                # Deep merge would be better, but simple update for now
                config.update(user_config)
                # Merge clientConfig specifically if present
                if "clientConfig" in user_config:
                    config["clientConfig"].update(user_config["clientConfig"])
            print(f"Loaded config from {config_path}")
        except Exception as e:
            print(f"Failed to load config: {e}")
    else:
        print("Using default config")

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

async def handle_health(request):
    return web.json_response({
        "status": "ok",
        "timestamp": time.time(),
        "uptime": time.time() - start_time,
        "protocols": ["wss"] if use_ssl else ["ws"]
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
                    print(f"Received message from {connection_id}: {data}")

                    if data.get("type") == "clientInfo":
                        state["clientName"] = data.get("clientName")
                        print(f"Client identified as: {state['clientName']}")

                    elif data.get("type") == "test":
                        await ws.send_json({"status": "success", "message": "Test message received"})

                    elif data.get("type") == "upload":
                        if "filename" not in data:
                             await ws.send_json({"status": "error", "message": "Invalid upload metadata"})
                             continue

                        state["metadata"] = data
                        state["receivedData"] = False

                        # Prepare upload path
                        upload_path = resolve_path(config["uploadPath"])
                        os.makedirs(upload_path, exist_ok=True)

                        filepath = os.path.join(upload_path, data["filename"])
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
                    filepath = os.path.join(resolve_path(config["uploadPath"]), filename)
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
        print("SSL enabled")
    else:
        print("SSL keys not found, using HTTP")

    # Start Zeroconf
    zeroconf = Zeroconf()
    try:
         # Need real IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()

        info = ServiceInfo(
            "_allspark._tcp.local.",
            f"{config['serviceName']}._allspark._tcp.local.",
            addresses=[socket.inet_aton(local_ip)],
            port=config['port'],
            properties={'path': '/'},
            server=f"{socket.gethostname()}.local."
        )
        zeroconf.register_service(info)
        print(f"Advertising Bonjour service: {config['serviceName']} on {local_ip}:{config['port']}")
    except Exception as e:
        print(f"Failed to start Zeroconf: {e}")

    try:
        web.run_app(init_app(), port=config["port"], ssl_context=ssl_context, access_log=None)
    except KeyboardInterrupt:
        pass
    finally:
        zeroconf.unregister_service(info)
        zeroconf.close()
