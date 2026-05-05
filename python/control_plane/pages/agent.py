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
import time
from dataclasses import dataclass
from typing import Any, Dict, List
from urllib.parse import quote

import aiohttp
from nicegui import ui

from theme import menu
from pages.settings import load_config, get_edge_base_url

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_POLL_INTERVAL_SEC = 5.0

# Severity heuristics – derived from the request's `error` label.
_CRITICAL_KEYWORDS = re.compile(
    r"\b(critical|fault|fatal|emergency|stopped|missing|missed|overheat|over[- ]?temp|crash)\b",
    re.IGNORECASE,
)


def _severity_for(r: Dict[str, Any]) -> Dict[str, str]:
    """
    Decide the on-screen severity flag for one anomaly response.
    Returns a dict with keys: level, label, icon, bg, border.

    Levels:
      - agent_error : the agent itself errored (distinct from a flagged anomaly)
      - critical    : error label contains a critical keyword
      - flagged     : default for any anomaly that the agent processed
    """
    status = r.get("status", "")
    error_label = (r.get("error") or "").strip()

    if status == "error":
        return {
            "level": "agent_error",
            "label": "AGENT ERROR",
            "icon": "⚠️",
            "bg": "bg-gray-600",
            "border": "border-l-gray-600",
        }
    if error_label and _CRITICAL_KEYWORDS.search(error_label):
        return {
            "level": "critical",
            "label": "CRITICAL",
            "icon": "🛑",
            "bg": "bg-red-600",
            "border": "border-l-red-600",
        }
    return {
        "level": "flagged",
        "label": "FLAGGED",
        "icon": "🚩",
        "bg": "bg-amber-500",
        "border": "border-l-amber-500",
    }


# Keywords used to spot the line that actually describes the anomaly within
# a longer agent summary (case-insensitive). More specific keywords first.
_ANOMALY_KEYWORDS = (
    "anomaly", "anomalous", "deviation", "deviates", "missing",
    "missed", "fault", "failure", "failed", "issue",
    "problem", "abnormal", "unexpected", "out of", "exceed",
    "stopped", "stall", "stuck", "incomplete", "no signal",
    "no response", "did not", "doesn't", "does not",
)

# Markdown headings that often introduce the anomaly description.
_ANOMALY_HEADINGS = (
    "anomaly", "issue", "problem", "finding", "diagnosis",
    "root cause", "summary", "observation", "conclusion",
)


def _truncate(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _extract_anomaly_line(summary: str, max_chars: int = 240) -> str:
    """
    Pick the most informative line from an agent summary to show as the
    card preview.

      1. Strip markdown formatting.
      2. Prefer the content directly following a heading like
         "Anomaly:" / "Finding:" / "Diagnosis:".
      3. Otherwise, pick the first line containing an anomaly keyword.
      4. Fall back to the first non-trivial sentence.
    """
    if not summary:
        return ""

    raw_lines = [ln.strip() for ln in summary.splitlines()]
    cleaned: List[str] = []
    for ln in raw_lines:
        if not ln:
            continue
        stripped = re.sub(r"^[#>\-\*\d\.\s]+", "", ln).strip()
        stripped = re.sub(r"[*_`]+", "", stripped).strip()
        if stripped:
            cleaned.append(stripped)

    if not cleaned:
        return ""

    # 2a. Inline heading: "Anomaly: pellet feeder stalled"
    for line in cleaned:
        for head in _ANOMALY_HEADINGS:
            m = re.match(rf"^{head}\s*[:\-—]\s*(.+)$", line, re.IGNORECASE)
            if m and len(m.group(1).strip()) > 6:
                return _truncate(m.group(1), max_chars)

    # 2b. Heading on its own line, content on the next.
    for i, line in enumerate(cleaned[:-1]):
        low = line.lower().rstrip(":").strip()
        if low in _ANOMALY_HEADINGS and len(cleaned[i + 1]) > 6:
            return _truncate(cleaned[i + 1], max_chars)

    # 3. First line containing an anomaly keyword.
    for line in cleaned:
        low = line.lower()
        if any(kw in low for kw in _ANOMALY_KEYWORDS):
            return _truncate(line, max_chars)

    # 4. First non-trivial sentence.
    return _truncate(cleaned[0], max_chars)


def _relative_time(iso_ts: str) -> str:
    """Return a short 'N min ago' style string from an ISO timestamp."""
    if not iso_ts:
        return ""
    try:
        from datetime import datetime, timezone
        ts = datetime.strptime(iso_ts[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return ""
    try:
        now = datetime.utcnow()
        delta = now - ts
        secs = int(delta.total_seconds())
        if secs < 0:
            return "just now"
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return ""


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
            f"&session={self.session_id}"
        )
        return f"/agent/session?adk_url={quote(adk_url, safe='')}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _launch_rerun(anomaly_folder: str = "") -> None:
    """
    Launch the Rerun anomaly viewer then navigate to /rerun.

    Workflow:
      1. Kill any previous rerun_server.py still holding the web-viewer port
         (they hold BOTH 9090 and the embedded rerun gRPC port 9876, so we
         must reap them before a new instance can bind).
      2. Spawn a fresh rerun_server.py subprocess (with or without
         ``--anomaly-folder``).
      3. Wait briefly for the web-viewer port to become reachable so the
         iframe on /rerun doesn't load before the server is ready.
      4. Navigate to /rerun?anomaly=<folder-name> so the page header can
         display which anomaly is currently loaded.
    """
    config = load_config()
    cp_config = config.get("control_plane", {}) or {}
    rerun_port: int = int(cp_config.get("rerunPort", 9090))

    rerun_server = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "rerun_server.py")
    )
    if not os.path.exists(rerun_server):
        ui.notify(
            f"Rerun server not found at:\n{rerun_server}",
            type="warning",
            close_button=True,
        )
        ui.navigate.to("/rerun")
        return

    # 1. Kill any previous rerun_server.py instances so we can reclaim ports
    killed = _kill_prior_rerun_servers(rerun_port)
    if killed:
        # Give the OS a moment to release the sockets
        time.sleep(0.5)

    # 2. Spawn the new viewer
    if anomaly_folder:
        ui.notify(
            f"Launching Rerun viewer for {os.path.basename(anomaly_folder)}…",
            type="info",
        )
        cmd = [
            sys.executable,
            rerun_server,
            "--anomaly-folder", anomaly_folder,
            "--port", str(rerun_port),
        ]
    else:
        ui.notify("Launching Rerun viewer…", type="info")
        cmd = [sys.executable, rerun_server, "--port", str(rerun_port)]

    _rerun_log_path = "/tmp/rerun_server.log"
    rerun_log_fh = open(_rerun_log_path, "w")
    subprocess.Popen(
        cmd,
        stdout=rerun_log_fh,
        stderr=rerun_log_fh,
    )
    print(f"[rerun] subprocess log: {_rerun_log_path}", flush=True)

    # 3. Wait for the server to start serving (max ~5 s)
    if _wait_for_port("127.0.0.1", rerun_port, timeout=5.0):
        ui.notify("Rerun viewer is ready.", type="positive")
    else:
        ui.notify(
            "Rerun viewer did not start within 5 s — the page may be blank "
            "until it finishes loading.",
            type="warning",
            close_button=True,
        )

    # 4. Navigate to the viewer page, including which anomaly is loaded
    if anomaly_folder:
        target = f"/rerun?anomaly={quote(os.path.basename(anomaly_folder))}"
    else:
        target = "/rerun"
    ui.navigate.to(target)


def _kill_prior_rerun_servers(rerun_port: int) -> int:
    """
    Terminate any lingering rerun_server.py processes that are holding our
    web-viewer port or the embedded rerun gRPC port (9876).

    Returns the number of processes killed. Safe no-op if ``lsof`` is
    missing or no instance is running.
    """
    import shutil
    import signal

    lsof = shutil.which("lsof")
    if not lsof:
        return 0

    killed = 0
    own_pid = os.getpid()
    for port in (rerun_port, 9876):
        try:
            out = subprocess.run(
                [lsof, f"-iTCP:{port}", "-sTCP:LISTEN", "-t", "-P"],
                capture_output=True, text=True, timeout=2,
            )
        except Exception:
            continue
        for pid_str in out.stdout.split():
            if not pid_str.strip().isdigit():
                continue
            pid = int(pid_str)
            if pid == own_pid:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                killed += 1
            except (ProcessLookupError, PermissionError):
                pass
    return killed


def _wait_for_port(host: str, port: int, timeout: float = 5.0) -> bool:
    """Poll *host:port* until it accepts a TCP connection, or *timeout* elapses."""
    import socket
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


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
                        "Back to Responses",
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
        edge_base_url = get_edge_base_url()

        # Derive ADK coordinates from config
        mc_config = config.get("mobile_client", {})
        agent_cfg: Dict[str, Any] = mc_config.get("agentConfig", {})
        raw_agent_url: str = agent_cfg.get("agent_url", "http://localhost:8000/run")
        adk_base_url: str = re.sub(r"/run$", "", raw_agent_url.rstrip("/"))
        adk_app_name: str = agent_cfg.get("agent_app_name", "allspark_agent")
        adk_user_id: str = agent_cfg.get("agent_user_id", "user")

        with menu("Agent — Anomaly Feed (Factory Floor Monitor)", hide_title=True):

            # Inject the small CSS we need for the LIVE pulse + new-card flash.
            ui.add_head_html("""
            <style>
              @keyframes allspark-pulse {
                0%   { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0.7); }
                70%  { box-shadow: 0 0 0 8px rgba(220, 38, 38, 0); }
                100% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0); }
              }
              .allspark-live-dot {
                width: 10px; height: 10px; border-radius: 50%;
                background: #dc2626; display: inline-block;
                animation: allspark-pulse 1.6s infinite;
              }
              @keyframes allspark-flash {
                0%   { background-color: #fef3c7; }
                100% { background-color: #ffffff; }
              }
              .allspark-new-card {
                animation: allspark-flash 6s ease-out;
                outline: 2px solid #f59e0b;
              }
              .allspark-chip {
                display: inline-block;
                padding: 1px 8px; border-radius: 9999px;
                font-size: 11px; font-weight: 600;
                background: #f1f5f9; color: #334155;
                margin-right: 4px;
              }
            </style>
            """)

            # ── Live header strip ─────────────────────────────────────────────
            with ui.row().classes("w-full justify-between items-center mb-2"):
                with ui.row().classes("items-center gap-3"):
                    ui.html('<span class="allspark-live-dot"></span>')
                    ui.label("LIVE").classes(
                        "text-xs font-extrabold text-red-600 tracking-widest"
                    )
                    ui.label("Anomaly Feed").classes(
                        "text-2xl font-bold text-gray-800"
                    )
                    count_badge = ui.label("0 anomalies").classes(
                        "ml-2 px-2 py-0.5 bg-amber-100 text-amber-800 "
                        "rounded text-xs font-bold"
                    )
                with ui.row().classes("items-center gap-3"):
                    last_updated_label = ui.label("").classes(
                        "text-xs text-gray-400"
                    )
                    refresh_btn = ui.button("Refresh", icon="refresh").props(
                        "flat dense"
                    ).classes("text-blue-600")

            ui.label(
                "Live triage view for factory-floor engineers — every flagged "
                "anomaly the agent has analysed, newest first. Cards flash amber "
                "when a new anomaly arrives."
            ).classes("text-sm text-gray-500 mb-4")

            # ── Filter toolbar ────────────────────────────────────────────────
            with ui.row().classes("w-full items-center gap-4 mb-3"):
                ui.label("Filter:").classes("text-xs font-bold text-gray-500")
                severity_filter = ui.toggle(
                    ["All", "Critical", "Flagged", "Agent Errors"],
                    value="All",
                ).props("dense unelevated")
                ui.label("|").classes("text-gray-300 text-xs")
                since_restart_toggle = ui.checkbox(
                    "Since last restart", value=True
                ).props("dense").classes("text-xs text-gray-600").tooltip(
                    "Show only anomalies submitted after the edge server last started. "
                    "Uncheck to view full history including previous runs."
                )

            responses_container = ui.column().classes("w-full gap-3")

            # State carried across refresh ticks
            seen_ids: set = set()
            new_ids: set = set()
            current_responses: List[Dict[str, Any]] = []
            first_render: Dict[str, bool] = {"value": True}
            # Signature of what's currently rendered, used to skip needless
            # re-renders so open ui.expansion drop-downs stay open across polls.
            render_sig: Dict[str, Any] = {"value": None}
            # Server start time (epoch float) — populated on first API call.
            # Used by the "Since restart" filter to hide pre-boot history.
            server_start_time: Dict[str, float] = {"value": 0.0}

            # ── Response rendering ────────────────────────────────────────────

            def _filter_responses(responses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
                mode = severity_filter.value or "All"
                result = responses

                # Since-restart filter: hide anomalies submitted before the server booted.
                # Uses the request_id prefix (ISO timestamp) or stored_at filename timestamp
                # rather than file mtime — mtime is unreliable after container restarts.
                boot_ts = server_start_time["value"]
                if since_restart_toggle.value and boot_ts > 0:
                    from datetime import datetime, timezone
                    import re as _re

                    def _submitted_epoch(r: Dict[str, Any]) -> float:
                        """Return the time this response was written (submission time, not event time)."""
                        # created_at is set to datetime.now() when the agent saves the response
                        ca = r.get("created_at", "")
                        if ca:
                            try:
                                from datetime import datetime, timezone
                                dt = datetime.fromisoformat(ca.replace("Z", "+00:00"))
                                return dt.timestamp()
                            except Exception:
                                pass
                        # Fallback: mtime of the stored_at directory on NFS
                        sp = r.get("stored_at", "")
                        if sp:
                            try:
                                import os
                                return os.path.getmtime(sp)
                            except Exception:
                                pass
                        return boot_ts  # unknown — let it through
                    result = [r for r in result if _submitted_epoch(r) >= boot_ts]

                if mode == "All":
                    return result
                target = {
                    "Critical":      "critical",
                    "Flagged":       "flagged",
                    "Agent Errors":  "agent_error",
                }.get(mode)
                if not target:
                    return result
                return [r for r in result if _severity_for(r)["level"] == target]

            def _render_responses(responses: List[Dict[str, Any]], force: bool = False) -> None:
                visible = _filter_responses(responses)

                # Always update header timestamp so engineers see liveness,
                # even when the actual list of cards has not changed.
                from datetime import datetime
                count_badge.set_text(
                    f"{len(visible)} anomal{'y' if len(visible) == 1 else 'ies'}"
                )
                last_updated_label.set_text(
                    f"Last updated {datetime.now().strftime('%H:%M:%S')}"
                )

                # Compute a lightweight signature of what's about to be rendered.
                # If the same set of cards (and their flash state, and the active
                # filter) is already on screen, do NOT clear the container — that
                # would collapse any drop-down the engineer has opened.
                sig = (
                    severity_filter.value,
                    since_restart_toggle.value,
                    tuple(r.get("request_id", "") for r in visible),
                    tuple(sorted(new_ids)),
                )
                if not force and sig == render_sig["value"]:
                    return
                render_sig["value"] = sig

                responses_container.clear()
                with responses_container:
                    if not visible:
                        with ui.card().classes("w-full p-6 text-center bg-green-50 border border-green-200"):
                            ui.icon("check_circle", size="3rem").classes("text-green-400 mb-2")
                            ui.label("No anomalies detected — system nominal ✅").classes(
                                "text-green-700 font-semibold"
                            )
                            ui.label(
                                "When the agent flags a new anomaly it will appear here automatically."
                            ).classes("text-green-500 text-sm mt-1")
                        return
                    for r in visible:
                        _render_response_card(r)

            def _render_response_card(r: Dict[str, Any]) -> None:
                sev = _severity_for(r)
                anomaly_time = r.get("anomaly_time", "N/A")
                clip_path = r.get("clip_path", "")
                clip_basename = os.path.basename(clip_path) if clip_path else "N/A"
                session_id: str = r.get("session_id", "")
                summary = r.get("summary", "")
                stored_at = r.get("stored_at", "")
                error_msg = r.get("error_message", "")
                anomaly_folder = r.get("anomaly_folder", "")
                error_label = r.get("error", "")
                expected_topic = r.get("expected_topic", "")
                video_clip_url = r.get("video_clip_url", "")
                request_id = r.get("request_id", "")
                # analysis_mode is stored in extra_metadata by kafka-profiler
                extra_meta = r.get("extra_metadata", {}) or {}
                analysis_mode = extra_meta.get("analysis_mode", "")

                opt = AnomalyOption(
                    session_id=session_id,
                    user_id=adk_user_id,
                    app_name=adk_app_name,
                    adk_base_url=adk_base_url,
                )

                # New-card flash highlight
                is_new = request_id in new_ids
                extra_class = " allspark-new-card" if is_new else ""

                card_classes = (
                    f"w-full shadow-sm bg-white border border-gray-100 "
                    f"border-l-4 {sev['border']}{extra_class}"
                )

                with ui.card().classes(card_classes):
                    with ui.row().classes("w-full items-stretch no-wrap gap-4"):
                        # ── LEFT RAIL: severity + time ────────────────────
                        with ui.column().classes("items-center justify-start gap-1 min-w-[140px]"):
                            ui.html(
                                f'<span class="px-2 py-1 {sev["bg"]} text-white '
                                f'rounded text-xs font-extrabold tracking-wide">'
                                f'{sev["icon"]} {sev["label"]}</span>'
                            )
                            time_part = anomaly_time[11:19] if len(anomaly_time) >= 19 else anomaly_time
                            date_part = anomaly_time[:10] if len(anomaly_time) >= 10 else ""
                            ui.label(time_part).classes(
                                "text-2xl font-bold text-gray-800 mt-1"
                            )
                            if date_part:
                                ui.label(date_part).classes("text-xs text-gray-400")
                            rel = _relative_time(anomaly_time)
                            if rel:
                                ui.label(rel).classes("text-xs text-gray-500 italic")
                            # Analysis mode badge (historical vs live)
                            if analysis_mode == "historical":
                                ui.html(
                                    '<span class="px-1.5 py-0.5 bg-blue-100 text-blue-700 '
                                    'rounded text-[10px] font-bold tracking-wide mt-1">'
                                    '🕘 HISTORICAL</span>'
                                ).tooltip(
                                    "Submitted during the lookback drain at profiler startup "
                                    "(replayed from Kafka history, not a live event)"
                                )
                            elif analysis_mode == "live":
                                ui.html(
                                    '<span class="px-1.5 py-0.5 bg-green-100 text-green-700 '
                                    'rounded text-[10px] font-bold tracking-wide mt-1">'
                                    '🟢 LIVE</span>'
                                ).tooltip("Detected in real-time by the Kafka profiler")

                        # ── CENTER: machine, chips, summary preview ───────
                        with ui.column().classes("flex-1 min-w-0 gap-1"):
                            ui.label(clip_basename).classes(
                                "font-bold text-gray-800 truncate"
                            ).tooltip(clip_path or "")

                            # Triage chips: error label + expected topic
                            chip_html = ""
                            if error_label and error_label != "N/A":
                                chip_html += (
                                    f'<span class="allspark-chip" '
                                    f'style="background:#fee2e2;color:#991b1b">'
                                    f'⚑ {error_label}</span>'
                                )
                            if expected_topic and expected_topic != "N/A":
                                chip_html += (
                                    f'<span class="allspark-chip" '
                                    f'style="background:#e0e7ff;color:#3730a3">'
                                    f'📡 {expected_topic}</span>'
                                )
                            if chip_html:
                                ui.html(chip_html)

                            # Summary preview (2-line clamp)
                            if sev["level"] == "agent_error":
                                ui.label(error_msg or "Unknown agent error").classes(
                                    "text-red-600 text-sm"
                                )
                            elif summary:
                                preview = _extract_anomaly_line(summary, max_chars=240)
                                if not preview:
                                    preview = _truncate(
                                        summary.strip().replace("\n", " "), 240
                                    )
                                ui.label(preview).classes(
                                    "text-sm text-gray-700 mt-1 font-medium"
                                )
                            else:
                                ui.label("No summary available.").classes(
                                    "text-gray-400 text-sm italic"
                                )

                            # Footer micro-line: session id + storage tooltip
                            with ui.row().classes("items-center gap-3 mt-1"):
                                if session_id:
                                    ui.label(f"session: {session_id}").classes(
                                        "text-[10px] text-gray-400 font-mono truncate"
                                    ).tooltip(session_id)
                                if stored_at:
                                    ui.icon("folder", size="xs").classes(
                                        "text-gray-300"
                                    ).tooltip(stored_at)

                        # ── RIGHT RAIL: actions ───────────────────────────
                        with ui.column().classes("items-stretch gap-1 min-w-[170px]"):
                            if video_clip_url:
                                ui.button(
                                    "▶ Watch Clip",
                                    on_click=lambda u=video_clip_url, n=clip_basename:
                                        _open_video_dialog(u, n),
                                ).props("dense").classes(
                                    "bg-amber-500 text-white text-xs"
                                )
                            if opt.has_session:
                                viewer_url = opt.session_viewer_url()
                                ui.button(
                                    "🧠 Investigate",
                                    on_click=lambda v=viewer_url: ui.navigate.to(v),
                                ).props("dense").classes(
                                    "bg-indigo-600 text-white text-xs"
                                )
                            if sev["level"] != "agent_error":
                                rerun_btn = ui.button(
                                    "📊 Rerun Viewer",
                                    on_click=lambda f=anomaly_folder: _launch_rerun(f),
                                ).props("dense flat").classes("text-gray-500 text-xs")
                                if anomaly_folder:
                                    rerun_btn.tooltip(
                                        f"Open per-anomaly viewer for "
                                        f"{os.path.basename(anomaly_folder)}"
                                    )

                    # ── Inline drop-down: Full Agent Summary ──────────────
                    if summary and sev["level"] != "agent_error":
                        with ui.expansion(
                            "📄 Full Summary", icon=None, value=False,
                        ).classes("w-full mt-2 border-t border-gray-100"):
                            ui.markdown(summary).classes("text-sm text-gray-700")

            # ── Dialog helpers ────────────────────────────────────────────────

            def _open_video_dialog(video_url: str, title: str) -> None:
                with ui.dialog() as dialog, ui.card().classes("p-2"):
                    ui.label(title).classes("font-bold text-sm mb-1")
                    ui.video(video_url).classes("w-[640px] max-w-full")
                    with ui.row().classes("justify-end w-full mt-1"):
                        ui.button("Close", on_click=dialog.close).props("flat dense")
                dialog.open()

            # ── Data fetching ─────────────────────────────────────────────────

            async def _refresh_responses() -> None:
                try:
                    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as http:
                        async with http.get(
                            f"{edge_base_url}/api/agent/responses",
                            params={"limit": "50"},
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as resp:
                            if resp.status != 200:
                                return
                            data = await resp.json(content_type=None)
                except Exception:
                    return

                responses = data.get("responses", [])
                current_responses[:] = responses

                # Update server boot time (used by since-restart filter)
                new_boot = data.get("server_start_time", 0.0)
                if new_boot and new_boot != server_start_time["value"]:
                    server_start_time["value"] = float(new_boot)

                # Diff against last seen request_ids to highlight new anomalies
                fetched_ids = {r.get("request_id", "") for r in responses if r.get("request_id")}
                if first_render["value"]:
                    # First load – seed seen_ids without flashing anything
                    seen_ids.update(fetched_ids)
                    new_ids.clear()
                    first_render["value"] = False
                else:
                    fresh = fetched_ids - seen_ids
                    if fresh:
                        new_ids.clear()
                        new_ids.update(fresh)
                        seen_ids.update(fresh)
                        n = len(fresh)
                        ui.notify(
                            f"🚩 {n} new anomaly flagged" if n == 1
                            else f"🚩 {n} new anomalies flagged",
                            type="warning",
                            position="top-right",
                            timeout=5000,
                        )
                        # Update browser tab title
                        ui.run_javascript(
                            f"document.title = '({n}) Anomaly Feed — AllSpark';"
                        )
                    else:
                        new_ids.clear()

                _render_responses(responses)

            def _on_filter_change() -> None:
                _render_responses(current_responses, force=True)

            severity_filter.on_value_change(_on_filter_change)
            since_restart_toggle.on_value_change(_on_filter_change)
            refresh_btn.on_click(_refresh_responses)
            ui.timer(_POLL_INTERVAL_SEC, _refresh_responses)
            ui.timer(0.1, _refresh_responses, once=True)


