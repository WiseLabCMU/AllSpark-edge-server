"""
Agent Page – AllSpark Control Plane
=====================================

Full-width response feed showing all stored agent analyses.

Each response card shows: status badge, clip name, anomaly time, session ID,
expandable agent summary, and a "Continue Investigation" button that navigates
to the embedded ADK session viewer (/agent/session) – an iframe page that
embeds the ADK dev-ui directly at the correct session URL (including app,
user ID, and session ID).

New-anomaly workflow
--------------------
Use the Debug page (/debug, linked in the header nav) to submit a new anomaly
analysis via POST /api/agent/analyze.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Dict, List
from urllib.parse import quote

import aiohttp
from nicegui import ui

from theme import menu
from pages.settings import load_config


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_POLL_INTERVAL_SEC = 10.0


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AnomalyOption:
    """Carries ADK session context for one stored response."""
    session_id: str
    user_id: str
    app_name: str
    adk_base_url: str

    @property
    def has_session(self) -> bool:
        return bool(self.session_id)

    def session_viewer_url(self) -> str:
        """
        Build the /agent/session URL that the embedded iframe page will use.
        Encodes the full ADK dev-ui address as a query parameter so the
        viewer page can construct the iframe src dynamically.
        """
        adk_url = (
            f"{self.adk_base_url}/dev-ui/"
            f"?app={self.app_name}"
            f"&user={self.user_id}"
            f"&session={self.session_id}"
        )
        return f"/agent/session?adk_url={quote(adk_url, safe='')}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _status_badge_color(status: str) -> str:
    return "green" if status == "success" else "red"


def _launch_rerun() -> None:
    ui.notify("Launching Rerun native viewer…", type="info")
    gui_app = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..", "..", "..", "..", "..",
            "allspark-datacapture", "GUI", "app.py",
        )
    )
    dummy_server = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "dummy_rerun_server.py")
    )
    if os.path.exists(gui_app):
        subprocess.Popen([sys.executable, gui_app, "--root_folder", "/tmp", "--lean"])
    elif os.path.exists(dummy_server):
        subprocess.Popen([sys.executable, dummy_server])
    ui.navigate.to("/rerun")


# ---------------------------------------------------------------------------
# Embedded ADK session viewer page  (/agent/session?adk_url=...)
# ---------------------------------------------------------------------------

def _create_session_viewer() -> None:
    """
    Registers a full-screen iframe page at /agent/session.

    The ADK dev-ui URL is passed as the `adk_url` query parameter so the
    iframe src is set dynamically per session without any server-side state.
    """
    @ui.page("/agent/session")
    async def session_viewer(adk_url: str = "") -> None:
        # Decode is handled automatically by NiceGUI's query param binding.
        # Fall back to the bare ADK UI root if no URL is provided.
        if not adk_url:
            adk_url = "http://localhost:8000/dev-ui/"

        with menu("Agent Session – ADK Developer UI"):
            with ui.row().classes("w-full justify-between items-center mb-4"):
                ui.label("AllSpark Agent Session").classes(
                    "text-xl font-bold text-gray-800"
                )
                with ui.row().classes("items-center gap-2"):
                    ui.button(
                        "Open in New Window",
                        icon="open_in_browser",
                        on_click=lambda: ui.run_javascript(
                            f"window.open({json.dumps(adk_url)}, '_blank')"
                        ),
                    ).props("flat")
                    ui.button(
                        "← Back to Responses",
                        icon="arrow_back",
                        on_click=lambda: ui.navigate.to("/agent"),
                    ).props("flat")

            ui.label(adk_url).classes(
                "text-xs font-mono text-gray-400 mb-2 truncate w-full"
            ).tooltip(adk_url)

            # Iframe embedding the ADK dev-ui at the specific session
            ui.html(
                f'<iframe src="{adk_url}" '
                f'class="w-full" '
                f'style="height: 75vh; border: 1px solid #ccc; border-radius: 8px;" '
                f'allow="clipboard-read; clipboard-write">'
                f"Your browser does not support iframes, or the ADK server is offline."
                f"</iframe>"
            ).classes("w-full")


# ---------------------------------------------------------------------------
# Main agent page  (/agent)
# ---------------------------------------------------------------------------

def create_page() -> None:

    # Register the embedded session viewer sub-page first
    _create_session_viewer()

    @ui.page("/agent")
    async def agent_page() -> None:
        config = load_config()
        edge_port = config.get("port", 8080)
        base_url = f"http://127.0.0.1:{edge_port}"

        # Derive ADK coordinates from config
        agent_cfg: Dict[str, Any] = config.get("agentConfig", {})
        raw_agent_url: str = agent_cfg.get("agent_url", "http://localhost:8000/run")
        adk_base_url: str = re.sub(r"/run$", "", raw_agent_url.rstrip("/"))
        adk_app_name: str = agent_cfg.get("agent_app_name", "allspark_agent")
        adk_user_id: str = agent_cfg.get("agent_user_id", "edge_server_user")

        with menu("Agentic Framework Control"):

            with ui.row().classes("w-full justify-between items-center mb-4"):
                ui.label("Recent Responses").classes("text-xl font-bold")
                refresh_btn = ui.button("Refresh", icon="refresh").props(
                    "flat dense"
                ).classes("text-blue-600")

            responses_container = ui.column().classes("w-full gap-4")

            # ── Response rendering ────────────────────────────────────────────

            def _render_responses(responses: List[Dict[str, Any]]) -> None:
                responses_container.clear()
                with responses_container:
                    if not responses:
                        with ui.card().classes("w-full p-6 text-center bg-gray-50"):
                            ui.icon("search_off", size="3rem").classes("text-gray-300 mb-2")
                            ui.label("No agent responses yet.").classes(
                                "text-gray-500 font-semibold"
                            )
                            ui.label(
                                "Submit a new anomaly via the Debug page to start an investigation."
                            ).classes("text-gray-400 text-sm mt-1")
                            ui.html(
                                '<a href="/debug" class="text-indigo-500 text-sm mt-2 inline-block hover:underline">'
                                "→ Go to Debug page"
                                "</a>"
                            )
                        return
                    for r in responses:
                        _render_response_card(r)

            def _render_response_card(r: Dict[str, Any]) -> None:
                status = r.get("status", "unknown")
                color = _status_badge_color(status)
                anomaly_time = r.get("anomaly_time", "N/A")
                clip_path = r.get("clip_path", "N/A")
                clip_basename = os.path.basename(clip_path) if clip_path else "N/A"
                session_id: str = r.get("session_id", "")
                summary = r.get("summary", "")
                created_at = r.get("created_at", "")
                stored_at = r.get("stored_at", "")
                error_msg = r.get("error_message", "")

                opt = AnomalyOption(
                    session_id=session_id,
                    user_id=adk_user_id,
                    app_name=adk_app_name,
                    adk_base_url=adk_base_url,
                )

                with ui.card().classes("w-full shadow-sm bg-white border border-gray-100"):

                    # ── Card header ───────────────────────────────────────────
                    with ui.row().classes("w-full justify-between items-start"):
                        with ui.column().classes("gap-1 flex-1 min-w-0"):
                            with ui.row().classes("items-center gap-2 flex-wrap"):
                                ui.badge(status.upper(), color=color).classes("text-xs")
                                ui.label(clip_basename).classes(
                                    "font-bold text-gray-800 truncate"
                                )
                            ui.label(f"Anomaly Time: {anomaly_time}").classes(
                                "text-sm text-gray-600"
                            )
                            ui.label(f"Clip: {clip_path}").classes(
                                "text-xs text-gray-400 font-mono truncate"
                            ).tooltip(clip_path)
                            if session_id:
                                ui.label(f"Session: {session_id}").classes(
                                    "text-xs text-gray-300 font-mono truncate"
                                ).tooltip(session_id)
                        ui.label(created_at[:19] if created_at else "").classes(
                            "text-xs text-gray-400 whitespace-nowrap ml-2 mt-1"
                        )

                    ui.separator().classes("my-2")

                    # ── Card body ─────────────────────────────────────────────
                    if status == "success" and summary:
                        with ui.expansion(
                            "Agent Summary", icon="psychology", value=True
                        ).classes("w-full"):
                            ui.markdown(summary).classes("text-sm")
                    elif status == "error":
                        with ui.row().classes("items-start gap-2"):
                            ui.icon("error_outline", size="sm").classes("text-red-400 mt-0.5")
                            ui.label(error_msg or "Unknown error").classes(
                                "text-red-600 text-sm"
                            )
                    else:
                        ui.label("No summary available.").classes(
                            "text-gray-400 text-sm italic"
                        )

                    # ── Card footer ───────────────────────────────────────────
                    with ui.row().classes("items-center gap-2 mt-3 flex-wrap"):
                        if opt.has_session:
                            viewer_url = opt.session_viewer_url()
                            ui.button(
                                "Continue Investigation",
                                icon="psychology_alt",
                                on_click=lambda v=viewer_url: ui.navigate.to(v),
                            ).classes("bg-indigo-600 text-white text-sm")

                        if status == "success":
                            ui.button(
                                "View in Rerun.io",
                                icon="bar_chart",
                                on_click=_launch_rerun,
                            ).props("flat").classes("text-gray-500 text-sm")

                    if stored_at:
                        ui.label(f"📁 {stored_at}").classes(
                            "text-xs text-gray-300 font-mono mt-2 truncate"
                        ).tooltip(stored_at)

            # ── Data fetching ─────────────────────────────────────────────────

            async def _refresh_responses() -> None:
                try:
                    async with aiohttp.ClientSession() as http:
                        async with http.get(
                            f"{base_url}/api/agent/responses",
                            params={"limit": "30"},
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as resp:
                            if resp.status != 200:
                                return
                            data = await resp.json(content_type=None)
                except Exception:
                    return

                _render_responses(data.get("responses", []))

            refresh_btn.on_click(_refresh_responses)
            ui.timer(_POLL_INTERVAL_SEC, _refresh_responses)
            ui.timer(0.1, _refresh_responses, once=True)

