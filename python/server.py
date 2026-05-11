import asyncio
import json
import yaml
import logging
import os
from pathlib import Path
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
CONFIG_FILE = "config.yaml"
DEFAULT_CONFIG = {
    "hostname": "0.0.0.0",
    "port": 8080,
    "autoUpload": True,
    "serviceName": "AllSpark Server",
    "keyFile": "keys/test-private.key",
    "certFile": "keys/test-public.crt",
    "uploadPath": "uploads/",
    "clientUploadsPath": "uploads/mobile_clients/",
    "agentResponsePath": "uploads/agent_responses/",
    "keepAliveIntervalMs": 5000,
    "agentConfig": {
        "agent_url": "http://localhost:8000/run",
        "agent_app_name": "allspark_agent",
        "agent_user_id": "user",
        "agent_session_id": "edge_session",
        "agent_timeout": 300,
        "agent_init_message": "Hey, can you help me do some analysis?"
    },
    "clientConfig": {
        "videoFormat": "mp4",
        "audioFormat": "wav",
        "depthFormat": "png",
        "poseFormat": "json",
        "timestampFormat": "txt",
        "fps": 30.0,
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
# Set at module load so handle_agent_responses() always has a valid value
# regardless of whether the module is run directly or imported.
start_time: float = time.time()

# Agent service singletons – initialised in load_config()
_agent_client: AgentApiClient | None = None
_response_store: AnomalyResponseStore | None = None
# Semaphore serialising agent calls.  Limit is read from
# agentConfig.agent_concurrency in config.yaml (default 2).
# Keep this low: ADK creates one SQLite session file per call;
# very high concurrency can hit NFS/SQLite locking on the shared volume.
_agent_semaphore: asyncio.Semaphore = asyncio.Semaphore(2)
# Strong references to in-flight background tasks so Python's GC cannot collect
# them before they complete (asyncio.create_task() only keeps a weak ref).
_pending_tasks: set[asyncio.Task] = set()

def load_config():
    global config, _agent_client, _response_store, _agent_semaphore
    import copy
    config = copy.deepcopy(DEFAULT_CONFIG)

    # Load user config if exists
    config_path = os.path.join(os.path.dirname(__file__), CONFIG_FILE)

    full_config = {}
    needs_save = False
    original_mc = {}

    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                full_config = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Failed to load config: {e}")

    def _deep_update(d, u):
        for k, v in u.items():
            if isinstance(v, dict) and k in d and isinstance(d[k], dict):
                _deep_update(d[k], v)
            else:
                d[k] = v
        return d

    if "mobile_client" in full_config:
        original_mc = full_config["mobile_client"]
        _deep_update(config, original_mc)
    else:
        print("mobile_client section missing in config.yaml. Generating it...")
        # config is already DEFAULT_CONFIG

    if original_mc != config:
        import copy
        full_config["mobile_client"] = copy.deepcopy(config)
        needs_save = True

    if needs_save:
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(full_config, f, default_flow_style=False, sort_keys=False)
            print(f"Updated config.yaml with mobile_client section at {config_path}")
        except Exception as e:
            print(f"Failed to update config.yaml: {e}")

    print(f"Loaded config from {config_path}")

    # Environment variable overrides (set via .env / docker-compose)
    _anomaly_dir = os.environ.get("ANOMALY_DATA_DIR", "").strip()
    if _anomaly_dir:
        config["anomalyEventDirs"] = [_anomaly_dir]
    _agent_url = os.environ.get("AGENT_URL", "").strip()
    if _agent_url:
        config.setdefault("agentConfig", {})["agent_url"] = _agent_url

    # Initialise agent singletons
    _agent_client = AgentApiClient(
        config.get("agentConfig", {}),
        anomaly_event_dirs=config.get("anomalyEventDirs", []),
    )
    # Limit concurrent agent calls.  agent_concurrency=2 allows two anomalies
    # to be analysed in parallel — fast enough to drain historical replay without
    # hammering the ADK SQLite session store.
    global _agent_semaphore
    _concurrency = int(config.get("agentConfig", {}).get("agent_concurrency", 2))
    _agent_semaphore = asyncio.Semaphore(_concurrency)
    print(f"Agent concurrency limit: {_concurrency}")
    response_path = resolve_path(config.get("agentResponsePath", "uploads/agent_responses/"))
    # Optional extra NFS/anomaly-event roots to scan for agent responses written
    # by the kafka-profiler (e.g. /net/htvvm662/fs0/anomaly_events).
    anomaly_event_dirs = config.get("anomalyEventDirs", [])
    _response_store = AnomalyResponseStore(response_path, anomaly_event_dirs=anomaly_event_dirs)
    print(f"Agent client initialised. Responses stored at: {response_path}")
    if anomaly_event_dirs:
        print(f"Also scanning anomaly event dirs: {anomaly_event_dirs}")
def get_project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def resolve_path(path_str):
    if os.path.isabs(path_str):
        return path_str
    return os.path.join(get_project_root(), path_str)

async def handle_index(request):
    return web.Response(text="AllSpark Edge API Server - API Active", status=200)

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
        error              (str, optional)  – composite error detail string
        error_description  (str, optional)  – human-readable error code description
        video_storage_path (str, optional)
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

    # clip_path is optional — NVR capture may still be in progress when the
    # Kafka client submits. The agent discovers clips via report_folder_content
    # using anomaly_folder. Only anomaly_time is strictly required.
    if not anomaly_time:
        return web.json_response(
            {"success": False, "error": "anomaly_time is required"},
            status=400,
        )

    anomaly_request = AnomalyRequest(
        clip_path=clip_path,
        log_path=body.get("log_path", ""),
        anomaly_time=anomaly_time,
        clip_start_time=body.get("clip_start_time", ""),
        clip_start_timestamp=str(body.get("clip_start_timestamp", "")),
        error=body.get("error", "N/A"),
        error_description=body.get("error_description", ""),
        video_storage_path=body.get("video_storage_path", ""),
        data_source=body.get("data_source", "mqtt"),
        anomaly_folder=body.get("anomaly_folder", ""),
        extra_metadata=body.get("extra_metadata", {}),
    )

    # Generate a request_id immediately so the caller gets a stable handle.
    import re as _re, uuid as _uuid
    safe_ts = _re.sub(r"[^a-zA-Z0-9_\-]", "_", anomaly_request.anomaly_time)[:32]
    request_id = f"{safe_ts}_{_uuid.uuid4().hex[:6]}"

    print(f"[agent/analyze] Queuing request {request_id} for clip: {clip_path}")

    # Fire-and-forget: process under the semaphore in the background so the
    # caller gets an immediate 202 instead of waiting for the full agent run.
    # This prevents HTTP timeouts when historical replay queues many requests.
    async def _run_analysis() -> None:
        # ------------------------------------------------------------------
        # If no clip yet, wait efficiently for NVR capture to finish before
        # acquiring the agent semaphore.  Strategy:
        #   1. Sleep for clip_duration_seconds (the earliest the clip can exist).
        #   2. Then poll every poll_interval_seconds using asyncio.sleep (zero
        #      CPU between wakes — no busy-polling).
        #   3. Give up after clip_wait_timeout_seconds and let the agent run
        #      without a clip (it will use kafka log context only).
        # This wait runs OUTSIDE the semaphore so all queued requests can wait
        # for their clips concurrently while only one calls the agent at a time.
        # ------------------------------------------------------------------
        try:
            if not anomaly_request.clip_path and anomaly_request.anomaly_folder:
                _nvr_cfg   = config.get("nvrWaitConfig", {})
                _initial   = float(_nvr_cfg.get("clip_duration_seconds",    120))
                _interval  = float(_nvr_cfg.get("poll_interval_seconds",     10))
                _timeout   = float(_nvr_cfg.get("clip_wait_timeout_seconds", 360))
                _video_dir = Path(anomaly_request.anomaly_folder) / "video_anomaly_data"

                # Prefer clips for the specific NVR channels that were captured.
                # captured_channels is a list of 1-based channel IDs; the clip
                # filename uses 0-based file index: clip_ch<ch-1>_*.mp4
                _preferred_channels: list = []
                if anomaly_request.extra_metadata:
                    _preferred_channels = anomaly_request.extra_metadata.get(
                        "captured_channels", []
                    )

                def _scan_for_clip() -> str:
                    """Blocking NFS glob — runs in a thread pool to avoid stalling the event loop."""
                    if not _video_dir.exists():
                        return ""
                    if _preferred_channels:
                        for _ch in sorted(_preferred_channels):
                            _file_ch = _ch - 1
                            for _pat in (
                                f"clip_ch{_file_ch}_*.h264.mp4",
                                f"clip_ch{_file_ch}_*.mp4",
                            ):
                                _hits = sorted(
                                    _video_dir.glob(_pat),
                                    key=lambda p: p.stat().st_mtime,
                                )
                                if _hits:
                                    return str(_hits[-1])
                    # Fallback: any clip (alphabetical first — original behaviour)
                    for _pat in ("clip_ch*.h264.mp4", "clip_ch*.mp4"):
                        _hits = sorted(_video_dir.glob(_pat))
                        if _hits:
                            return str(_hits[0])
                    return ""

                # --- Fast-path: clip already on disk (e.g. resubmit after channel-map fix)
                # Skip the 120s sleep entirely and go straight to the semaphore.
                _found_clip = await asyncio.to_thread(_scan_for_clip)
                if _found_clip:
                    print(
                        f"[agent/analyze] {request_id}: clip already present — "
                        f"skipping NVR wait → {Path(_found_clip).name}"
                    )
                    anomaly_request.clip_path = _found_clip
                else:
                    # Clip not ready yet — NVR is still recording.
                    # Sleep for clip_duration_seconds (earliest the clip can exist),
                    # then poll every poll_interval_seconds.
                    print(
                        f"[agent/analyze] {request_id}: clip_path empty — "
                        f"sleeping {_initial:.0f}s then polling every {_interval:.0f}s "
                        f"(max {_timeout:.0f}s) for clip in {_video_dir}"
                    )
                    await asyncio.sleep(_initial)
                    _loop     = asyncio.get_running_loop()
                    _deadline = _loop.time() + (_timeout - _initial)
                    _found_clip = ""

                    while _loop.time() < _deadline:
                        _found_clip = await asyncio.to_thread(_scan_for_clip)
                        if _found_clip:
                            break
                        await asyncio.sleep(_interval)

                    if _found_clip:
                        print(f"[agent/analyze] {request_id}: clip ready → {Path(_found_clip).name}")
                        anomaly_request.clip_path = _found_clip
                    else:
                        print(
                            f"[agent/analyze] {request_id}: timed out waiting for clip — "
                            f"proceeding without video"
                        )

            async with _agent_semaphore:
                agent_response = await _agent_client.analyze_anomaly(anomaly_request)
            stored_at = _response_store.save(
                agent_response,
                anomaly_request,
                agent_config=config.get("agentConfig"),
            )
            print(
                f"[agent/analyze] Done {request_id}. Status={agent_response.status}. "
                f"Stored at: {stored_at}"
            )
        except asyncio.CancelledError:
            print(f"[agent/analyze] {request_id}: task cancelled", flush=True)
            raise  # must re-raise so asyncio can clean up properly
        except Exception as _exc:
            print(
                f"[agent/analyze] {request_id}: ERROR — {type(_exc).__name__}: {_exc}",
                flush=True,
            )
        finally:
            _pending_tasks.discard(asyncio.current_task())

    _task = asyncio.create_task(_run_analysis())
    _pending_tasks.add(_task)  # strong ref prevents premature GC

    return web.json_response(
        {
            "success": True,
            "request_id": request_id,
            "status": "queued",
            "message": "Analysis queued — result will appear on the dashboard when complete.",
        },
        status=202,
    )


async def handle_agent_continue(request):
    """
    POST /api/agent/continue

    Continue an investigation by sending a follow-up prompt to an existing
    ADK session.  The session must have been created by a prior /api/agent/analyze
    call; its session_id is stored in the AgentResponse.

    Request body (JSON):
        session_id    (str, required) – the ADK session to continue
        prompt        (str, required) – the follow-up question / instruction
        clip_path     (str, optional) – carried forward for attribution
        anomaly_time  (str, optional) – carried forward for attribution
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

    session_id = body.get("session_id", "").strip()
    prompt = body.get("prompt", "").strip()

    if not session_id or not prompt:
        return web.json_response(
            {"success": False, "error": "session_id and prompt are required"},
            status=400,
        )

    clip_path = body.get("clip_path", "")
    anomaly_time = body.get("anomaly_time", "")

    print(f"[agent/continue] Continuing session {session_id} with prompt: {prompt[:80]}…")

    agent_response = await _agent_client.continue_session(
        session_id=session_id,
        prompt=prompt,
        clip_path=clip_path,
        anomaly_time=anomaly_time,
    )

    stored_at = _response_store.save(
        agent_response,
        agent_config=config.get("agentConfig"),
    )

    print(
        f"[agent/continue] Follow-up complete. Status={agent_response.status}. "
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
    GET /api/agent/responses?limit=<n>

    Returns a list of stored agent responses, newest first.
    """
    global _response_store

    if _response_store is None:
        return web.json_response(
            {"success": False, "error": "Agent service not initialised"},
            status=503,
        )

    try:
        limit = int(request.rel_url.query.get("limit", 50))
    except ValueError:
        limit = 50

    responses = _response_store.list_responses(limit=limit)
    return web.json_response(
        {
            "success": True,
            "count": len(responses),
            "responses": _response_store.list_response_dicts(limit=limit),
            "server_start_time": start_time,
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
                            if parsed_ts > 9999999999: # If it's in milliseconds (13 digits) instead of seconds (10 digits)
                                parsed_ts /= 1000.0
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
                        base_upload_path = resolve_path(config.get("clientUploadsPath", "uploads/mobile_clients"))
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
                    filepath = state.get("filepath", os.path.join(resolve_path(config.get("clientUploadsPath", "uploads/mobile_clients")), filename))
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
    app.router.add_post('/api/agent/analyze', handle_agent_analyze)
    app.router.add_post('/api/agent/continue', handle_agent_continue)
    app.router.add_get('/api/agent/responses', handle_agent_responses)
    app.router.add_get('/api/agent/responses/{stored_at_b64}', handle_agent_response_detail)
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
