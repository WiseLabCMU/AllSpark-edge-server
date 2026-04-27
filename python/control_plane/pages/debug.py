"""
Debug Page – AllSpark Control Plane
=====================================

Provides the Manual Anomaly Trigger form for development and testing.
Equivalent to running submit_anomaly_to_edge.py from the CLI.

Accessible via the "Debug" link in the header bar (between Agent and Settings).
Not part of the primary operator workflow – intended for developers and integrators.

Workflow
--------
Fill in the form (pre-filled with working defaults) and click
"Dispatch to Agentic Framework" to POST to /api/agent/analyze.
This creates a fresh ADK session, runs the full agent analysis pipeline,
and stores the response on disk.  The result (including the ADK session URL)
is displayed inline so the operator can immediately open the session in the
ADK dev-ui.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import aiohttp
from nicegui import ui

from theme import menu
from pages.settings import load_config, get_edge_base_url


# ---------------------------------------------------------------------------
# Working defaults – mirror submit_anomaly_to_edge.py
# ---------------------------------------------------------------------------

_DEFAULTS: Dict[str, str] = {
    "clip_path": "anomaly_clip_20250917_143650.mp4",
    "log_path": "",
    "anomaly_time": "2025-09-17T14:36:50",
    "clip_start_time": "2025-09-17T14:36:20",
    "error": "missed expected message",
    "expected_topic": "allspark/anomaly_detected",
    "video_storage_path": "",
    "mqtt_messages": "",
}


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def create_page() -> None:

    @ui.page("/debug")
    async def debug_page() -> None:
        edge_base_url = get_edge_base_url()

        full_config = load_config()
        config = full_config.get("mobile_client", {})
        
        # Derive the ADK dev-ui base URL from the agent_url in config
        import re as _re
        agent_url: str = config.get("agentConfig", {}).get(
            "agent_url", "http://localhost:8000/run"
        )
        adk_base_url: str = _re.sub(r"/run$", "", agent_url.rstrip("/"))
        app_name: str = config.get("agentConfig", {}).get(
            "agent_app_name", "allspark_agent"
        )

        with menu("Debug – Manual Anomaly Trigger"):

            ui.label(
                "Start a new agent analysis session by submitting an anomaly manually. "
                "Pre-filled with working defaults – adjust as needed. "
                "This is the UI equivalent of running submit_anomaly_to_edge.py."
            ).classes("text-sm text-gray-500 mb-6")

            with ui.card().classes("w-full p-6"):

                # ── Form fields ───────────────────────────────────────────────
                with ui.row().classes("w-full gap-4 flex-wrap"):
                    clip_path_input = ui.input(
                        "Anomaly Clip Filename",
                        value=_DEFAULTS["clip_path"],
                    ).classes("flex-1 min-w-[300px]").tooltip(
                        "Plain filename (basename only). The agent's VideoDataLoader "
                        "resolves it via endswith() against its configured data folder. "
                        "Use submit_anomaly_to_edge.py to stage the file automatically."
                    )
                    log_path_input = ui.input(
                        "Log Path (optional)",
                        value=_DEFAULTS["log_path"],
                        placeholder="/path/to/mqtt_trace.log",
                    ).classes("flex-1 min-w-[300px]")

                with ui.row().classes("w-full gap-4 flex-wrap"):
                    anomaly_time_input = ui.input(
                        "Anomaly Time (ISO-8601)",
                        value=_DEFAULTS["anomaly_time"],
                    ).classes("flex-1 min-w-[200px]")
                    clip_start_input = ui.input(
                        "Clip Start Time (ISO-8601, optional)",
                        value=_DEFAULTS["clip_start_time"],
                    ).classes("flex-1 min-w-[200px]")

                with ui.row().classes("w-full gap-4 flex-wrap"):
                    error_input = ui.input(
                        "Error / Label",
                        value=_DEFAULTS["error"],
                    ).classes("flex-1 min-w-[200px]")
                    expected_topic_input = ui.input(
                        "Expected MQTT Topic",
                        value=_DEFAULTS["expected_topic"],
                    ).classes("flex-1 min-w-[200px]")

                video_storage_input = ui.input(
                    "Video Storage Path (optional)",
                    value=_DEFAULTS["video_storage_path"],
                    placeholder="/path/to/video/chunks/",
                ).classes("w-full")

                mqtt_messages_input = ui.textarea(
                    "MQTT Messages (JSON array, optional)",
                    value=_DEFAULTS["mqtt_messages"],
                    placeholder='[{"topic": "rng120/status", "payload": "ok", "t": 1744545570000}]',
                ).classes("w-full font-mono text-sm")

                # ── Submit ────────────────────────────────────────────────────
                with ui.row().classes("items-center gap-4 mt-4"):
                    submit_btn = ui.button(
                        "Dispatch to Agentic Framework",
                        icon="science",
                    ).classes("bg-blue-600 text-white")
                    status_label = ui.label("").classes("text-sm text-gray-600")

                # ── Result panel (shown after successful dispatch) ─────────────
                result_card = ui.card().classes("w-full mt-6 p-4 hidden")
                with result_card:
                    result_title = ui.label("").classes("font-bold text-gray-800 mb-2")
                    result_body = ui.column().classes("w-full gap-2")

                # ── Submit handler ────────────────────────────────────────────
                async def on_submit() -> None:
                    clip_path = clip_path_input.value.strip()
                    anomaly_time = anomaly_time_input.value.strip()

                    if not clip_path or not anomaly_time:
                        ui.notify(
                            "Clip filename and anomaly time are required.",
                            type="warning",
                        )
                        return

                    # Parse optional MQTT messages JSON
                    mqtt_msgs: List[Dict[str, Any]] = []
                    raw_mqtt = mqtt_messages_input.value.strip()
                    if raw_mqtt:
                        try:
                            parsed = json.loads(raw_mqtt)
                            if isinstance(parsed, list):
                                mqtt_msgs = parsed
                            else:
                                ui.notify(
                                    "MQTT Messages must be a JSON array.",
                                    type="warning",
                                )
                        except json.JSONDecodeError as exc:
                            ui.notify(
                                f"Invalid MQTT Messages JSON: {exc}",
                                type="warning",
                            )
                            return

                    submit_btn.disable()
                    status_label.set_text("Dispatching to agent framework…")
                    result_card.classes(remove="hidden")

                    payload: Dict[str, Any] = {
                        "clip_path": clip_path,
                        "log_path": log_path_input.value.strip(),
                        "anomaly_time": anomaly_time,
                        "clip_start_time": clip_start_input.value.strip(),
                        "error": error_input.value.strip(),
                        "expected_topic": expected_topic_input.value.strip(),
                        "video_storage_path": video_storage_input.value.strip(),
                        "mqtt_clip_messages": mqtt_msgs,
                    }

                    try:
                        async with aiohttp.ClientSession() as http:
                            async with http.post(
                                f"{edge_base_url}/api/agent/analyze",
                                json=payload,
                                timeout=aiohttp.ClientTimeout(total=360),
                            ) as resp:
                                result = await resp.json(content_type=None)

                        success: bool = result.get("success", False)
                        session_id: str = result.get("session_id", "")
                        request_id: str = result.get("request_id", "N/A")
                        stored_at: str = result.get("stored_at", "")
                        error_msg: str = (
                            result.get("error_message") or result.get("error", "")
                        )

                        result_body.clear()
                        with result_body:
                            if success:
                                result_title.set_text(
                                    f"✅ Analysis complete – Request {request_id}"
                                )
                                status_label.set_text("")

                                # Session URL for ADK dev-ui
                                if session_id:
                                    adk_session_url = (
                                        f"{adk_base_url}/dev-ui/"
                                        f"?app={app_name}&session={session_id}"
                                    )
                                    with ui.row().classes("items-center gap-2 flex-wrap"):
                                        ui.label("ADK Session:").classes(
                                            "text-sm font-semibold text-gray-700"
                                        )
                                        ui.label(session_id).classes(
                                            "text-xs font-mono text-gray-500 truncate"
                                        )
                                    with ui.row().classes("items-center gap-2 mt-1"):
                                        ui.button(
                                            "Open Session in ADK",
                                            icon="open_in_new",
                                            on_click=lambda u=adk_session_url: ui.run_javascript(
                                                f"window.open({json.dumps(u)}, '_blank')"
                                            ),
                                        ).classes("bg-indigo-600 text-white text-sm")
                                        ui.label(adk_session_url).classes(
                                            "text-xs font-mono text-gray-400 truncate"
                                        ).tooltip(adk_session_url)

                                if stored_at:
                                    ui.label(f"📁 {stored_at}").classes(
                                        "text-xs font-mono text-gray-400 mt-1 truncate"
                                    ).tooltip(stored_at)

                                summary: str = result.get("summary", "")
                                if summary:
                                    with ui.expansion(
                                        "Agent Summary", icon="psychology", value=True
                                    ).classes("w-full mt-2"):
                                        ui.markdown(summary).classes("text-sm")

                                ui.notify(
                                    f"Analysis complete – Request ID: {request_id}",
                                    type="positive",
                                )
                            else:
                                result_title.set_text("❌ Analysis failed")
                                status_label.set_text(f"Error: {error_msg}")
                                ui.label(error_msg or "Unknown error").classes(
                                    "text-red-600 text-sm"
                                )
                                ui.notify(
                                    f"Analysis failed: {error_msg}", type="negative"
                                )

                    except Exception as exc:
                        result_body.clear()
                        with result_body:
                            result_title.set_text("❌ Request failed")
                            ui.label(str(exc)).classes("text-red-600 text-sm")
                        status_label.set_text(f"❌ {exc}")
                        ui.notify(f"Request failed: {exc}", type="negative")
                    finally:
                        submit_btn.enable()

                submit_btn.on_click(on_submit)

            # ── CLI hint ──────────────────────────────────────────────────────
            with ui.expansion("CLI equivalent (submit_anomaly_to_edge.py)", icon="terminal").classes("w-full mt-4"):
                ui.code(
                    "python tests/submit_anomaly_to_edge.py \\\n"
                    f"    --clip-path /path/to/{_DEFAULTS['clip_path']} \\\n"
                    f"    --anomaly-time {_DEFAULTS['anomaly_time']} \\\n"
                    f"    --clip-start-time {_DEFAULTS['clip_start_time']} \\\n"
                    f"    --error \"{_DEFAULTS['error']}\" \\\n"
                    f"    --expected-topic {_DEFAULTS['expected_topic']} \\\n"
                    "    --edge-port 8080",
                    language="bash",
                ).classes("w-full text-sm")
                ui.label(
                    "The CLI version also stages the clip into the agent's data folder automatically."
                ).classes("text-xs text-gray-500 mt-1")

