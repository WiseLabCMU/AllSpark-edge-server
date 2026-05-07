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

import base64
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


def _clip_video_url(clip_path: str) -> str:
    """Return the /api/clip-video URL for *clip_path*, or '' if not available."""
    if not clip_path:
        return ""
    encoded = base64.urlsafe_b64encode(clip_path.encode()).rstrip(b"=").decode()
    return f"/api/clip-video?path={encoded}"


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

    Returns the number of processes killed. Safe no-op if no instance is running.
    Uses /proc (Linux) when lsof is unavailable.
    """
    import shutil
    import signal

    own_pid = os.getpid()
    pids_to_kill: set[int] = set()

    # Strategy 1: lsof (macOS / most Linux distros)
    lsof = shutil.which("lsof")
    if lsof:
        for port in (rerun_port, 9876):
            try:
                out = subprocess.run(
                    [lsof, f"-iTCP:{port}", "-sTCP:LISTEN", "-t", "-P"],
                    capture_output=True, text=True, timeout=2,
                )
                for pid_str in out.stdout.split():
                    if pid_str.strip().isdigit():
                        pids_to_kill.add(int(pid_str))
            except Exception:
                pass

    # Strategy 2: /proc scan (Linux containers without lsof)
    if not pids_to_kill:
        try:
            import re as _re
            proc_root = Path("/proc")
            target_ports = {rerun_port, 9876}
            hex_ports = {f"{p:04X}" for p in target_ports}
            for pid_dir in proc_root.iterdir():
                if not pid_dir.name.isdigit():
                    continue
                pid = int(pid_dir.name)
                if pid == own_pid:
                    continue
                # Check /proc/<pid>/net/tcp6 and tcp for listening sockets
                for net_file in (pid_dir / "net" / "tcp6", pid_dir / "net" / "tcp"):
                    try:
                        content = net_file.read_text()
                        for line in content.splitlines()[1:]:
                            parts = line.split()
                            if len(parts) < 4:
                                continue
                            # local_address field: IPADDR:PORT_HEX, state=0A means LISTEN
                            local = parts[1]
                            state = parts[3]
                            if state != "0A":
                                continue
                            port_hex = local.split(":")[-1]
                            if port_hex in hex_ports:
                                pids_to_kill.add(pid)
                    except (PermissionError, FileNotFoundError):
                        pass
        except Exception:
            pass

    # Strategy 3: pkill rerun_server.py by name (last resort)
    if not pids_to_kill:
        pkill = shutil.which("pkill")
        if pkill:
            try:
                subprocess.run(
                    [pkill, "-f", "rerun_server.py"],
                    timeout=2, capture_output=True,
                )
                return 1  # best-effort, count unknown
            except Exception:
                pass

    killed = 0
    for pid in pids_to_kill:
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
# Error frequency chart helpers
# ---------------------------------------------------------------------------

import re as _re_ec
import csv as _csv
from pathlib import Path as _Path

# Regex to pull the short error code (e.g. "DG052", "IME014") out of the
# full error detail string produced by kafka_error_event_monitor.
_ERROR_CODE_RE = _re_ec.compile(
    r'code=([A-Z]{2,6}\d{3,6})',
    _re_ec.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Load error_codes.csv once at import time.
# File lives at python/error_codes.csv (sibling of control_plane/).
# Format: Machine Error Code;Error Text;MES error Code
# ---------------------------------------------------------------------------
_ERROR_CODES_CSV = _Path(__file__).parent.parent.parent / "error_codes.csv"
_ERROR_CODE_MAP: Dict[str, str] = {}

def _load_error_code_map() -> Dict[str, str]:
    result: Dict[str, str] = {}
    try:
        with _ERROR_CODES_CSV.open(encoding="utf-8") as fh:
            reader = _csv.reader(fh, delimiter=";")
            next(reader, None)  # skip header
            for row in reader:
                if len(row) >= 2 and row[0].strip():
                    result[row[0].strip().upper()] = row[1].strip()
    except Exception:
        pass
    return result

_ERROR_CODE_MAP = _load_error_code_map()

# mtime-based cache so interarrival_stats.json is only parsed when it changes.
_interarrival_cache: Dict[str, Any] = {"path": "", "mtime": -1.0, "counts": {}}


def _extract_error_code(error_label: str) -> str:
    """Return the short error code from an error detail string, or ''."""
    if not error_label:
        return ""
    m = _ERROR_CODE_RE.search(error_label)
    return m.group(1).upper() if m else ""


def _extract_error_desc(error_label: str) -> str:
    """
    Return a human-readable description for the error code in *error_label*.
    Priority:
      1. error_codes.csv lookup (authoritative)
      2. text="..." field embedded in the Kafka detail string
    """
    code = _extract_error_code(error_label)
    if code and code in _ERROR_CODE_MAP:
        return _ERROR_CODE_MAP[code]
    # fallback: text="..." field in the Kafka detail string
    m = _re_ec.search(r'text=["\u201c]([^"\u201d]+)["\u201d]', error_label)
    if m:
        return m.group(1).strip()
    return ""


def _load_interarrival_counts(anomaly_base_dirs: List[str]) -> Dict[str, int]:
    """
    Read lookback_errors.summary from interarrival_stats.json in the first
    anomaly base directory that contains the file.
    Returns a dict mapping error_code -> count, or {} on any failure.
    Caches by file mtime so disk is only read when the file actually changes.
    """
    import json as _json
    import os as _os
    from pathlib import Path as _Path
    for base in anomaly_base_dirs:
        candidate = _Path(base) / "interarrival_stats.json"
        path_str = str(candidate)
        try:
            mtime = _os.path.getmtime(path_str)
            if (
                path_str == _interarrival_cache["path"]
                and mtime == _interarrival_cache["mtime"]
            ):
                return _interarrival_cache["counts"]
            data = _json.loads(candidate.read_text(encoding="utf-8"))
            summary = data.get("lookback_errors", {}).get("summary", {})
            counts = {k.upper(): int(v) for k, v in summary.items() if v}
            _interarrival_cache.update({"path": path_str, "mtime": mtime, "counts": counts})
            return counts
        except Exception:
            continue
    return {}


def _build_error_chart_data(
    responses: List[Dict[str, Any]],
    hours: int = 48,
    top_n: int = 5,
    extra_counts: Dict[str, int] | None = None,
    extra_descs: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    """
    Count error code occurrences in *responses* within the last *hours* hours,
    merged with *extra_counts* (e.g. from interarrival_stats.json).
    Returns a dict ready to pass to ui.echart's ``options``.
    """
    from datetime import datetime, timezone, timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    # hist_counts is the authoritative Kafka-level 48h event count from
    # interarrival_stats.json.  live_counts tallies agent responses in the
    # same window.  We take max(hist, live) per code so we:
    #  - never double-count (responses are a subset of the Kafka events), and
    #  - still pick up codes that arrived after the JSON's last write.
    hist_counts: Dict[str, int] = dict(extra_counts or {})
    descs:       Dict[str, str] = dict(extra_descs or {})
    live_counts: Dict[str, int] = {}

    for r in responses:
        ts_str = r.get("anomaly_time", "")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
            except Exception:
                continue  # skip entries whose timestamp cannot be parsed

        err = r.get("error", "")
        code = _extract_error_code(err)
        if not code:
            continue
        live_counts[code] = live_counts.get(code, 0) + 1
        if code not in descs:
            descs[code] = _extract_error_desc(err)

    # Merge: authoritative JSON counts are the floor; live counts win only
    # when they exceed the JSON (i.e. new errors since last JSON write).
    counts: Dict[str, int] = dict(hist_counts)
    for code, cnt in live_counts.items():
        counts[code] = max(counts.get(code, 0), cnt)

    if not counts:
        return {}

    # Enrich descriptions from CSV for any code that has no description yet
    for code in counts:
        if code not in descs and code in _ERROR_CODE_MAP:
            descs[code] = _ERROR_CODE_MAP[code]

    top = sorted(counts.items(), key=lambda x: -x[1])[:top_n]
    codes  = [c for c, _ in top]
    values = [v for _, v in top]
    descriptions = [descs.get(c, "") for c in codes]

    # Colour palette — warm amber/red tones matching the dashboard severity palette
    colours = ["#f59e0b", "#ef4444", "#f97316", "#a855f7", "#3b82f6"]

    return {
        "_codes":        codes,
        "_values":       values,
        "_descriptions": descriptions,
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "shadow"},
            "formatter": "function(p){return p[0].name+': <b>'+p[0].value+'</b> occurrences';}",
        },
        "grid": {"top": 12, "bottom": 4, "left": 40, "right": 12, "containLabel": True},
        "xAxis": {
            "type": "category",
            "data": codes,
            "axisLabel": {"fontWeight": "bold", "fontSize": 13, "color": "#1f2937"},
            "axisTick": {"alignWithLabel": True},
        },
        "yAxis": {
            "type": "value",
            "minInterval": 1,
            "axisLabel": {"fontSize": 11, "color": "#6b7280"},
            "splitLine": {"lineStyle": {"type": "dashed", "color": "#f3f4f6"}},
        },
        "series": [{
            "type": "bar",
            "data": [
                {"value": v, "itemStyle": {"color": colours[i % len(colours)]}}
                for i, v in enumerate(values)
            ],
            "barMaxWidth": 56,
            "label": {
                "show": True,
                "position": "top",
                "fontWeight": "bold",
                "fontSize": 13,
                "color": "#1f2937",
            },
        }],
    }


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
                    "Since last restart", value=False
                ).props("dense").classes("text-xs text-gray-600").tooltip(
                    "Show only anomalies submitted after the edge server last started. "
                    "Uncheck to view full history including previous runs."
                )

            # ── Two-column layout: chart left, cards right ────────────────
            with ui.row().classes("w-full items-start gap-2 no-wrap"):

                # ── LEFT: error frequency chart ────────────────────────────
                with ui.column().classes("shrink-0 gap-0").style("width:220px"):
                    with ui.card().classes(
                        "shadow-sm border border-gray-100 bg-white p-2 w-full"
                    ):
                        with ui.row().classes("w-full items-center justify-between mb-1"):
                            ui.label("Top Errors").classes(
                                "text-xs font-bold text-gray-600 tracking-wide uppercase"
                            )
                            chart_window_label = ui.label("").classes(
                                "text-[10px] text-gray-400"
                            )
                        ui.label("Last 48 h  •  lookback + live").classes(
                            "text-[10px] text-gray-400 mb-2"
                        )
                        chart_el = ui.echart({}).classes("w-full").style("height:260px")
                        chart_desc_col = ui.column().classes("w-full gap-1 mt-2")

                # ── RIGHT: anomaly response cards ──────────────────────────
                responses_container = ui.column().classes("flex-1 min-w-0 gap-3")

            # ── chart update helper — called on every poll ───────────────────
            anomaly_base_dirs: List[str] = (
                mc_config.get("anomalyEventDirs", []) or []
            )

            def _update_chart(responses: List[Dict[str, Any]]) -> None:
                from datetime import datetime
                # Merge lookback counts from interarrival_stats.json with
                # live agent response counts for a complete 48h picture.
                hist_counts = _load_interarrival_counts(anomaly_base_dirs)
                opts = _build_error_chart_data(
                    responses, hours=48, top_n=5,
                    extra_counts=hist_counts,
                )
                if not opts:
                    empty_opts = {
                        "graphic": [{
                            "type": "text",
                            "left": "center", "top": "middle",
                            "style": {"text": "No errors recorded",
                                      "fill": "#9ca3af", "fontSize": 13},
                        }],
                        "xAxis": {"show": False},
                        "yAxis": {"show": False},
                        "series": [],
                    }
                    chart_el.options.clear()
                    chart_el.options.update(empty_opts)
                    chart_el.update()
                    chart_desc_col.clear()
                    chart_window_label.set_text("")
                    return

                colours = ["#f59e0b", "#ef4444", "#f97316", "#a855f7", "#3b82f6"]
                codes  = opts.pop("_codes", [])
                descs  = opts.pop("_descriptions", [])
                opts.pop("_values", None)

                # Horizontal bar chart (easier to read in a narrow column)
                opts["xAxis"] = {
                    "type": "value",
                    "minInterval": 1,
                    "axisLabel": {"fontSize": 10, "color": "#6b7280"},
                    "splitLine": {"lineStyle": {"type": "dashed", "color": "#f3f4f6"}},
                }
                opts["yAxis"] = {
                    "type": "category",
                    "data": codes[::-1],  # highest count at top
                    "axisLabel": {"fontWeight": "bold", "fontSize": 12, "color": "#1f2937"},
                }
                opts["grid"] = {"top": 4, "bottom": 4, "left": 8, "right": 32, "containLabel": True}
                opts["series"][0]["data"] = [
                    {"value": opts["series"][0]["data"][i]["value"],
                     "itemStyle": {"color": colours[(len(codes)-1-i) % len(colours)],
                                   "borderRadius": [0, 3, 3, 0]}}
                    for i in range(len(codes) - 1, -1, -1)
                ]
                opts["series"][0]["label"] = {
                    "show": True, "position": "right",
                    "fontWeight": "bold", "fontSize": 12, "color": "#1f2937",
                }
                opts["series"][0]["barMaxWidth"] = 28

                chart_el.options.clear()
                chart_el.options.update(opts)
                chart_el.update()

                # Description legend — one line per error code, code + full description
                chart_desc_col.clear()
                with chart_desc_col:
                    for i, (code, desc) in enumerate(zip(codes, descs)):
                        colour = colours[i % len(colours)]
                        desc_text = desc if desc else ""
                        ui.html(
                            f'<span style="display:inline-flex;align-items:flex-start;'
                            f'gap:5px;font-size:10px;line-height:1.5">'
                            f'<span style="width:8px;height:8px;border-radius:2px;margin-top:3px;'
                            f'background:{colour};flex-shrink:0"></span>'
                            f'<span><b style="color:#374151;font-size:11px">{code}</b>'
                            + (f'<br><span style="color:#6b7280">{desc_text}</span>' if desc_text else '') +
                            f'</span></span>'
                        )

                chart_window_label.set_text(
                    f"{datetime.now().strftime('%H:%M')}"
                )

            # State carried across refresh ticks
            seen_ids: set = set()
            new_ids: set = set()
            # Separate sig for chart updates — only rebuild the chart when the
            # set of error codes/counts actually changes, not on every poll.
            chart_sig: Dict[str, Any] = {"value": None}
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

                # Update error frequency chart with ALL responses (not filtered)
                # so the chart always reflects the full 48h picture.
                # Only rebuild when the error data has changed to avoid
                # unnecessary redraws on every poll tick.
                #
                # Also include the mtime of interarrival_stats.json so the
                # chart refreshes immediately when the kafka monitor rewrites
                # that file (e.g. after a restart with a longer lookback),
                # even if no new agent response has arrived yet.
                import os as _os_cs
                _ia_mtime = 0.0
                for _base in anomaly_base_dirs:
                    _ia_path = _Path(_base) / "interarrival_stats.json"
                    try:
                        _ia_mtime = _os_cs.path.getmtime(str(_ia_path))
                        break
                    except OSError:
                        pass
                new_chart_sig = tuple(
                    (r.get("error", ""), r.get("anomaly_time", ""))
                    for r in responses
                ) + (_ia_mtime,)
                if new_chart_sig != chart_sig["value"]:
                    chart_sig["value"] = new_chart_sig
                    _update_chart(responses)

                # Compute a lightweight signature of what's about to be rendered.
                # If the same set of cards (and their flash state, and the active
                # filter) is already on screen, do NOT clear the container — that
                # would collapse any drop-down the engineer has opened.
                # Include chart-relevant data so new errors force a re-render.
                sig = (
                    severity_filter.value,
                    since_restart_toggle.value,
                    tuple(r.get("request_id", "") for r in visible),
                    tuple(sorted(new_ids)),
                    tuple(r.get("error", "") for r in responses),
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
                clip_paths: List[str] = r.get("clip_paths") or ([clip_path] if clip_path else [])
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
                # Inline video URL — prefer the /api/clip-video endpoint which
                # auto-transcodes to H.264 (browser-native), falling back to
                # the existing anomaly-media URL for uploads-local clips.
                # clip_paths drives multi-camera display; inline_video_url is
                # still used for the right-rail "Watch Clip" button.
                inline_video_url = _clip_video_url(clip_path) or video_clip_url

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

                    # ── Inline video panes — up to 3 cameras side-by-side ──
                    _valid_clips = [
                        (_i, _cpath, _clip_video_url(_cpath))
                        for _i, _cpath in enumerate(clip_paths)
                        if _clip_video_url(_cpath)
                    ]
                    if _valid_clips:
                        _multi = len(_valid_clips) > 1
                        _panel_label = (
                            f"▶ {len(_valid_clips)} Camera Clips"
                            if _multi
                            else f"▶ Video Clip  —  {os.path.basename(_valid_clips[0][1])}"
                        )
                        with ui.expansion(_panel_label, icon=None, value=False).classes(
                            "w-full mt-2 border-t border-gray-100"
                        ):
                            # Rows of at most 3 cameras each
                            _COLS = 3
                            for _row_start in range(0, len(_valid_clips), _COLS):
                                _row_clips = _valid_clips[_row_start:_row_start + _COLS]
                                _col_pct = 100 // len(_row_clips)
                                with ui.row().classes("w-full no-wrap gap-2 mt-1"):
                                    for _i, _cpath, _curl in _row_clips:
                                        _cname = os.path.basename(_cpath)
                                        _esc = _curl.replace('"', '%22')
                                        with ui.column().classes("items-center gap-0").style(
                                            f"width:{_col_pct}%;min-width:0"
                                        ):
                                            if _multi:
                                                ui.label(f"Camera {_i + 1}  —  {_cname}").classes(
                                                    "text-xs font-semibold text-gray-600 truncate w-full"
                                                ).tooltip(_cpath)
                                            ui.html(
                                                f'<video controls preload="auto" '
                                                f'style="width:100%;border-radius:6px;" '
                                                f'src="{_esc}">'
                                                f'Your browser does not support HTML5 video.</video>'
                                            )
                                            if not _multi:
                                                ui.label(_cpath).classes(
                                                    "text-[10px] text-gray-400 font-mono"
                                                )
                    # Fallback: video_clip_url from uploads/ when no NFS clips exist
                    if not clip_paths and video_clip_url:
                        with ui.expansion(
                            f"▶ Video Clip  —  {clip_basename}", icon=None, value=False,
                        ).classes("w-full mt-2 border-t border-gray-100"):
                            with ui.column().classes("w-full items-center gap-1"):
                                _esc = video_clip_url.replace('"', '%22')
                                ui.html(
                                    f'<video controls preload="auto" '
                                    f'style="width:100%;max-width:900px;border-radius:6px;" '
                                    f'src="{_esc}">'
                                    f'Your browser does not support HTML5 video.</video>'
                                )

                    # ── Inline drop-down: Full Agent Summary ──────────────
                    if summary and sev["level"] != "agent_error":
                        with ui.expansion(
                            "📄 Full Summary", icon=None, value=False,
                        ).classes("w-full mt-1 border-t border-gray-100"):
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


