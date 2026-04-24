#!/usr/bin/env python3
"""
submit_anomaly_to_edge.py
=========================
Submit a real anomaly video clip to the Edge Server for agent analysis.

Analogous to `test_anomaly_publisher.py` (which publishes to MQTT) and
`anomaly_agent_sender.py` (which calls the agent directly), this script
goes via the Edge Server's REST API so the full production path is exercised:

    this script
        → POST /api/agent/analyze  (Edge Server, port 8080)
            → AgentApiClient.analyze_anomaly()
                → adk web             (Agentic Framework, port 8000)
            → AnomalyResponseStore.save()
                → uploads/agent_responses/<Anomaly_YYYY-MM-DD>/<HHMMSS_uuid>/
                    response.json
                    request.json
                    summary.txt
                    session_info.txt   ← ADK session lookup helper
                    video_clips/       ← video clip(s) for this anomaly
                    machine_anomaly_data/ ← machine/sensor data

Usage examples
--------------
# Minimal – timestamp auto-derived from filename
python tests/submit_anomaly_to_edge.py \\
    --clip-path /Users/bos2pi/git/Bosch-Github/allspark-datacapture/logs/2026/cesar/anomaly_clip_20250917_143650.mp4

# Full specification
python tests/submit_anomaly_to_edge.py \\
    --clip-path /Users/.../anomaly_clip_20250917_143650.mp4 \\
    --anomaly-time 2025-09-17T14:36:50 \\
    --clip-start-time 2025-09-17T14:36:20 \\
    --log-path /Users/.../mqtt_trace_20250917.log \\
    --error "missed expected message" \\
    --expected-topic allspark/anomaly_detected \\
    --mqtt-messages '[{"topic":"rng120/status","payload":"bolt_tightened"}]' \\
    --edge-host 127.0.0.1 \\
    --edge-port 8080 \\
    --adk-url http://localhost:8000 \\
    --timeout 300

# Load MQTT messages from a JSON file
python tests/submit_anomaly_to_edge.py \\
    --clip-path /path/to/clip.mp4 \\
    --mqtt-messages /path/to/captured_messages.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path bootstrap – allow running from any working directory
# ---------------------------------------------------------------------------
_PYTHON_DIR = Path(__file__).parent.parent

# Absolute path to the agentic framework repo root.
_AGENT_FRAMEWORK_ROOT = (
    Path(__file__).parent.parent.parent.parent / "allspark-agentic-framework"
)


def _resolve_agent_video_data_folder() -> str:
    """
    Read the agentic framework's active config to determine the correct
    video data folder.  Falls back to cesar/camera-video if the config
    cannot be parsed.
    """
    fallback = str(_AGENT_FRAMEWORK_ROOT / "allspark_agent" / "sample_data" / "cesar" / "camera-video")

    try:
        import yaml  # PyYAML
    except ImportError:
        # If PyYAML not available, try a simple text parse
        pass

    config_path = _AGENT_FRAMEWORK_ROOT / "allspark_agent" / "config" / "config.yaml"
    if not config_path.exists():
        return fallback

    try:
        import yaml
        with open(config_path) as f:
            main_cfg = yaml.safe_load(f)
        profile_name = main_cfg.get("active_profile", "")
        if not profile_name:
            return fallback

        profile_path = config_path.parent / profile_name
        if not profile_path.exists():
            return fallback

        with open(profile_path) as f:
            profile_cfg = yaml.safe_load(f)

        root_data = profile_cfg.get("root_data_folder", "")
        video_subdir = (profile_cfg.get("data_paths") or {}).get("video", "camera-video")
        if root_data:
            resolved = _AGENT_FRAMEWORK_ROOT / root_data / video_subdir
            if resolved.exists():
                return str(resolved)
    except Exception:
        pass

    return fallback


_AGENT_VIDEO_DATA_FOLDER = _resolve_agent_video_data_folder()

# Default segments to use when auto-generating a _segments.json for a new clip.

# CESAR bolt assembly operation cycle (Steps 2-5, ~12s cycle).
_CESAR_SEGMENTS = {
    "segments": [
        {"id": "Steps 2 and 3", "times": [0, 7]},
        {"id": "Step 4",        "times": [7, 9]},
        {"id": "Step 5",        "times": [9, 12]},
    ]
}

# Hatvan PM6 degating / ch5 operation cycle (Steps 1-7, ~50s cycle).
_HATVAN_CH5_SEGMENTS = {
    "segments": [
        {"id": "Step 1", "times": [0, 9]},
        {"id": "Step 2", "times": [9, 12]},
        {"id": "Step 3", "times": [12, 21]},
        {"id": "Step 4", "times": [21, 28]},
        {"id": "Step 5", "times": [28, 40]},
        {"id": "Step 6", "times": [40, 43]},
        {"id": "Step 7", "times": [43, 50]},
    ]
}

# Hatvan PM6 pellet feeder / ch3 operation cycle (Steps 1-4, ~35s cycle).
_HATVAN_CH3_SEGMENTS = {
    "segments": [
        {"id": "Step 1", "times": [0, 6]},
        {"id": "Step 2", "times": [6, 17]},
        {"id": "Step 3", "times": [17, 38]},
        {"id": "Step 4", "times": [29, 35]},
    ]
}


def _select_default_segments(clip_filename: str) -> dict:
    """Pick the correct default segments based on the clip filename."""
    lower = clip_filename.lower()
    if lower.startswith("ch5_") or "degating" in lower:
        return _HATVAN_CH5_SEGMENTS
    if lower.startswith("ch3_") or "pellet" in lower:
        return _HATVAN_CH3_SEGMENTS
    # Any other chN_ prefix → use ch5 as a generic Hatvan fallback
    if lower.startswith("ch") and len(lower) > 3 and lower[2:].split("_", 1)[0].isdigit():
        return _HATVAN_CH5_SEGMENTS
    return _CESAR_SEGMENTS

sys.path.insert(0, str(_PYTHON_DIR))


# ===========================================================================
# AnomalySubmitter class
# ===========================================================================

class AnomalySubmitter:
    """
    Submits a real anomaly clip to the Edge Server and displays the full
    agent analysis result, including the ADK session ID for inspection.

    Args:
        edge_host:       Hostname/IP of the running Edge Server.
        edge_port:       Port of the Edge Server (default 8080).
        adk_url:         Base URL of the Agentic Framework web UI (display only).
        timeout:         HTTP timeout in seconds for the analyze call.
        clip_path:       Absolute path to the anomaly video clip.
        anomaly_time:    ISO-8601 timestamp of the anomaly (auto-derived if None).
        clip_start_time: ISO-8601 timestamp of clip start (optional).
        log_path:        Path to associated MQTT/log file (optional).
        error:           Error label that triggered the anomaly.
        expected_topic:  MQTT topic that was expected but missed.
        mqtt_messages:   List of MQTT message dicts captured around the anomaly.
        extra_metadata:  Additional key/value metadata forwarded to the agent.
    """

    _ANALYZE_PATH = "/api/agent/analyze"
    _RESPONSES_PATH = "/api/agent/responses"

    def __init__(
        self,
        *,
        edge_host: str = "127.0.0.1",
        edge_port: int = 8080,
        adk_url: str = "http://localhost:8000",
        timeout: int = 1000,
        clip_path: str,
        anomaly_time: Optional[str] = None,
        clip_start_time: str = "",
        log_path: str = "",
        error: str = "N/A",
        expected_topic: str = "N/A",
        mqtt_messages: Optional[List[Dict[str, Any]]] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
        data_source: str = "mqtt",
        anomaly_folder: str = "",
    ) -> None:
        self._base_url = f"http://{edge_host}:{edge_port}"
        self._adk_url = adk_url.rstrip("/")
        self._timeout = timeout

        self.clip_path = clip_path
        self.log_path = log_path
        self.error = error
        self.expected_topic = expected_topic
        self.mqtt_messages: List[Dict[str, Any]] = mqtt_messages or []
        self.extra_metadata: Dict[str, Any] = extra_metadata or {}
        self.clip_start_time = clip_start_time
        self.data_source = data_source
        self.anomaly_folder = anomaly_folder

        # Resolve anomaly_time – auto-derive from filename if not supplied
        if anomaly_time:
            self.anomaly_time = anomaly_time
        else:
            derived = self.parse_timestamp_from_filename(clip_path)
            if derived is None:
                print(
                    f"\n[ERROR] Cannot derive timestamp from filename: {Path(clip_path).name}\n"
                    "  Expected pattern: anomaly_clip_YYYYMMDD_HHMMSS.mp4\n"
                    "  Pass --anomaly-time explicitly, e.g.:\n"
                    "    --anomaly-time 2025-09-17T14:36:50\n",
                    file=sys.stderr,
                )
                sys.exit(1)
            self.anomaly_time = derived
            print(f"  [info] Auto-derived anomaly_time from filename: {self.anomaly_time}")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def submit(self) -> bool:
        """
        Execute the full submission workflow.
        Returns True on success (HTTP 200 received), False otherwise.
        """
        self._print_header()

        # 0. Pre-flight: copy the clip into the agent's data folder so the tool can find it
        staged_basename = self._stage_clip_for_agent()
        if staged_basename is None:
            return False

        # 1. Health check
        if not self._health_check():
            return False

        # 2. Submit to /api/agent/analyze — send only the basename as clip_path
        result = self._call_analyze(clip_path_override=staged_basename)
        if result is None:
            return False

        # 3. Print full result
        self._print_result(result)

        # 4. Print ADK session box (the key new output)
        session_id = result.get("session_id", "")
        stored_at = result.get("stored_at", "")
        self._print_adk_session_box(session_id, stored_at)

        # 5. Show session_info.txt from disk if it was written
        if stored_at:
            self._show_session_info_file(stored_at)

        # 6. Response count
        self._print_response_count()

        return True

    # ------------------------------------------------------------------
    # Agent data-folder staging
    # ------------------------------------------------------------------

    def _stage_clip_for_agent(self) -> Optional[str]:
        """
        Copy the clip (and auto-generate a companion _segments.json if needed)
        into the agent's video data folder so VideoDataLoader.get_data() can
        find it via the endswith(basename) match.

        Returns the basename of the staged clip, or None on failure.
        """
        src = Path(self.clip_path)
        if not src.exists():
            print(
                f"\n[ERROR] Clip not found at: {src}\n"
                "  Check --clip-path and make sure the file exists.",
                file=sys.stderr,
            )
            return None

        data_folder = Path(_AGENT_VIDEO_DATA_FOLDER)
        print(f"  [stage] Agent video data folder: {data_folder}")
        if not data_folder.exists():
            print(
                f"\n[ERROR] Agent video data folder not found:\n  {data_folder}\n"
                "  Is the allspark-agentic-framework repo at the expected path?",
                file=sys.stderr,
            )
            return None

        basename = src.name
        dest = data_folder / basename

        # Copy the clip if not already there
        if dest.exists():
            print(f"  [stage] Clip already in data folder: {dest}")
        else:
            print(f"  [stage] Copying clip → {dest}")
            shutil.copy2(str(src), str(dest))
            print(f"  [stage] ✅ Copied.")

        # Auto-generate _segments.json if missing
        segments_dest = data_folder / f"{src.stem}_segments.json"
        if segments_dest.exists():
            print(f"  [stage] Segments file already exists: {segments_dest}")
        else:
            selected_segments = _select_default_segments(src.name)
            print(f"  [stage] Generating default segments → {segments_dest}")
            segments_dest.write_text(
                json.dumps(selected_segments, indent=2), encoding="utf-8"
            )
            seg_times = [s["times"] for s in selected_segments["segments"]]
            print(f"  [stage] ✅ Segments written ({len(seg_times)} segments, times: {seg_times}).")
            print(
                f"  [stage] NOTE: Default segments were auto-selected based on filename.\n"
                f"  [stage]       Edit {segments_dest.name} to match your clip's actual operation cycle."
            )

        print()
        return basename

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def parse_timestamp_from_filename(clip_path: str) -> Optional[str]:
        """
        Extract an ISO-8601 timestamp from a clip filename.

        Supported patterns:
          anomaly_clip_20250917_143650.mp4   →  2025-09-17T14:36:50
          clip_2025-09-17_14-36-50.mp4       →  2025-09-17T14:36:50
          anything_20250917_143650_suffix.mp4 → 2025-09-17T14:36:50

        Returns None if no timestamp is found.
        """
        name = Path(clip_path).stem  # strip directory and extension

        # Pattern 1: YYYYMMDD_HHMMSS  (compact, no separators)
        m = re.search(r"(\d{8})_(\d{6})", name)
        if m:
            try:
                dt = datetime.strptime(f"{m.group(1)}_{m.group(2)}", "%Y%m%d_%H%M%S")
                return dt.strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                pass

        # Pattern 2: YYYY-MM-DD_HH-MM-SS  (with separators)
        m2 = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})", name)
        if m2:
            try:
                dt = datetime.strptime(
                    f"{m2.group(1)} {m2.group(2).replace('-', ':')}", "%Y-%m-%d %H:%M:%S"
                )
                return dt.strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                pass

        return None

    @staticmethod
    def load_mqtt_messages(path_or_json: str) -> List[Dict[str, Any]]:
        """
        Load MQTT messages from either a JSON file path or an inline JSON string.
        Returns an empty list on any error.
        """
        if not path_or_json:
            return []

        # Try as file path first
        p = Path(path_or_json)
        if p.exists() and p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
                print(f"  [warn] {p} does not contain a JSON array – ignoring")
                return []
            except Exception as exc:
                print(f"  [warn] Could not read MQTT messages from {p}: {exc}")
                return []

        # Try as inline JSON
        try:
            data = json.loads(path_or_json)
            if isinstance(data, list):
                return data
            print("  [warn] --mqtt-messages inline JSON is not an array – ignoring")
            return []
        except json.JSONDecodeError as exc:
            print(f"  [warn] Could not parse --mqtt-messages as JSON: {exc}")
            return []

    # ------------------------------------------------------------------
    # HTTP helpers (stdlib urllib only – no extra dependencies)
    # ------------------------------------------------------------------

    def _http_post(self, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = self._base_url + path
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            print(f"\n[ERROR] HTTP {exc.code} from {url}:\n{body}", file=sys.stderr)
            return None
        except urllib.error.URLError as exc:
            print(f"\n[ERROR] Cannot reach {url}: {exc.reason}", file=sys.stderr)
            return None
        except Exception as exc:
            print(f"\n[ERROR] POST {url} failed: {exc}", file=sys.stderr)
            return None

    def _http_get(self, path: str, params: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
        url = self._base_url + path
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            print(f"\n[warn] GET {url} failed: {exc}")
            return None

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _health_check(self) -> bool:
        print("[1/4] Checking Edge Server health …")
        data = self._http_get("/api/health")
        if data is None:
            print(
                f"  [ERROR] Edge Server not reachable at {self._base_url}\n"
                "  Make sure it is running: python server.py",
                file=sys.stderr,
            )
            return False
        if data.get("status") != "ok":
            print(f"  [warn] Unexpected health response: {data}")
        print(f"  ✅ Edge Server healthy at {self._base_url}")
        return True

    def _call_analyze(self, clip_path_override: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        POST to /api/agent/analyze.

        clip_path_override: if given, send this value as clip_path instead of
        self.clip_path. This is used to pass the plain filename (basename) so
        the agent tool's endswith() match succeeds against the data folder.
        """
        effective_clip = clip_path_override if clip_path_override else self.clip_path
        print(f"\n[2/4] Submitting anomaly to {self._base_url}{self._ANALYZE_PATH} …")
        print(f"  clip_path (sent to agent) : {effective_clip}")
        print(f"  original clip_path        : {self.clip_path}")
        print(f"  anomaly_time              : {self.anomaly_time}")
        print(f"  error                     : {self.error}")
        if self.mqtt_messages:
            print(f"  mqtt_messages             : {len(self.mqtt_messages)} message(s)")
        print(f"  timeout                   : {self._timeout}s  (waiting for agent response…)\n")

        payload: Dict[str, Any] = {
            "clip_path": effective_clip,   # ← basename only so tool endswith() matches
            "log_path": self.log_path,
            "anomaly_time": self.anomaly_time,
            "clip_start_time": self.clip_start_time,
            "error": self.error,
            "expected_topic": self.expected_topic,
            "mqtt_clip_messages": self.mqtt_messages,
            "data_source": self.data_source,
            "anomaly_folder": self.anomaly_folder,
            "extra_metadata": {
                **self.extra_metadata,
                "original_clip_path": self.clip_path,  # keep full path in metadata
            },
        }

        result = self._http_post(self._ANALYZE_PATH, payload)
        if result is None:
            print("[ERROR] /api/agent/analyze call failed – aborting.", file=sys.stderr)
        return result

    def _print_result(self, result: Dict[str, Any]) -> None:
        print("\n[3/4] Agent Response")
        print("─" * 60)
        status = result.get("status", "unknown")
        success = result.get("success", False)
        icon = "✅" if success else "⚠️ "
        print(f"  {icon} Status      : {status}")
        print(f"  Request ID  : {result.get('request_id', 'N/A')}")
        print(f"  Session ID  : {result.get('session_id', 'N/A')}")
        print(f"  Stored at   : {result.get('stored_at', 'N/A')}")

        summary = result.get("summary", "")
        if summary:
            print("\n  Agent Summary (first 800 chars):")
            print("  " + "-" * 56)
            # Indent each line
            for line in summary[:800].splitlines():
                print(f"  {line}")
            if len(summary) > 800:
                print(f"  … ({len(summary)} chars total – see summary.txt for full text)")
        elif result.get("error_message"):
            print(f"\n  Error: {result['error_message']}")

        print("─" * 60)

    def _print_adk_session_box(self, session_id: str, stored_at: str) -> None:
        if not session_id:
            print("\n[warn] No session_id in response – agent may have errored before session creation.")
            return

        width = 62
        border = "╔" + "═" * width + "╗"
        footer = "╚" + "═" * width + "╝"

        def row(text: str) -> str:
            padded = f"  {text}"
            return "║" + padded.ljust(width) + "║"

        print(f"\n{border}")
        print(row("AllSpark Agent – ADK Session Lookup"))
        print("║" + " " * width + "║")
        print(row(f"Session ID  : {session_id}"))
        print("║" + " " * width + "║")
        print(row(f"Open ADK UI : {self._adk_url}"))
        print(row( "Navigate to : Sessions tab"))
        print(row(f"Search for  : {session_id}"))
        print("║" + " " * width + "║")
        if stored_at:
            print(row(f"Files       : {stored_at}"))
            print(row( "              └─ session_info.txt (full lookup details)"))
        print(f"{footer}\n")

    def _show_session_info_file(self, stored_at: str) -> None:
        """Read and echo session_info.txt written by the server, if present."""
        p = Path(stored_at) / "session_info.txt"
        if p.exists():
            print("  [session_info.txt contents]")
            print(p.read_text(encoding="utf-8"))

    def _print_response_count(self) -> None:
        print("[4/4] Total response count …")
        data = self._http_get(
            self._RESPONSES_PATH,
            params={"limit": "1"},
        )
        if data and "count" in data:
            print(f"  Total stored responses: {data['count']}")
        else:
            print("  [warn] Could not retrieve response count.")

    # ------------------------------------------------------------------
    # Private formatting
    # ------------------------------------------------------------------

    def _print_header(self) -> None:
        print("=" * 64)
        print("  AllSpark Edge Server – Anomaly Analysis Submitter")
        print("=" * 64)
        print(f"  Edge Server : {self._base_url}")
        print(f"  ADK Web UI  : {self._adk_url}")
        print(f"  Clip        : {self.clip_path}")
        print(f"  Timestamp   : {self.anomaly_time}")
        print("=" * 64)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Submit a real anomaly clip to the Edge Server for agent analysis.\n"
            "The timestamp is auto-derived from the filename if not supplied."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Required
    p.add_argument(
        "--clip-path",
        required=True,
        metavar="PATH",
        help="Absolute path to the anomaly video clip, e.g. "
             "/Users/.../anomaly_clip_20250917_143650.mp4",
    )

    # Timestamp – optional (auto-derived from filename)
    p.add_argument(
        "--anomaly-time",
        default=None,
        metavar="ISO8601",
        help="Anomaly timestamp in ISO-8601 format, e.g. 2025-09-17T14:36:50. "
             "Auto-derived from the clip filename if omitted.",
    )
    p.add_argument(
        "--clip-start-time",
        default="",
        metavar="ISO8601",
        help="Clip start timestamp in ISO-8601 format (optional).",
    )

    # Optional paths
    p.add_argument(
        "--log-path",
        default="",
        metavar="PATH",
        help="Path to an associated MQTT/log file (optional).",
    )

    # Device / context
    p.add_argument(
        "--error",
        default="N/A",
        metavar="TEXT",
        help="Error label that triggered the anomaly (default: N/A).",
    )
    p.add_argument(
        "--expected-topic",
        default="N/A",
        metavar="TOPIC",
        help="MQTT topic that was expected but missed (default: N/A).",
    )
    p.add_argument(
        "--mqtt-messages",
        default="",
        metavar="JSON_OR_FILE",
        help="MQTT messages around the anomaly. Either an inline JSON array "
             "('[{\"topic\":\"...\"}]') or a path to a .json file.",
    )

    # Connection
    p.add_argument(
        "--edge-host",
        default="127.0.0.1",
        metavar="HOST",
        help="Hostname/IP of the Edge Server (default: 127.0.0.1).",
    )
    p.add_argument(
        "--edge-port",
        type=int,
        default=8080,
        metavar="PORT",
        help="Port of the Edge Server (default: 8080).",
    )
    p.add_argument(
        "--adk-url",
        default="http://localhost:8000",
        metavar="URL",
        help="Base URL of the ADK web UI, used for display (default: http://localhost:8000).",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=1000,
        metavar="SECONDS",
        help="HTTP timeout for the agent call in seconds (default: 1000, "
             "must exceed the Edge Server's agent_timeout in config.yaml).",
    )
    p.add_argument(
        "--data-source",
        default="mqtt",
        choices=["mqtt", "kafka"],
        metavar="SOURCE",
        help="Source of anomaly data messages: 'mqtt' or 'kafka' (default: mqtt).",
    )
    p.add_argument(
        "--anomaly-folder",
        default="",
        metavar="PATH",
        help="Path to an existing anomaly folder (e.g. uploads/anomaly_2026-04-02T20-49-04). "
             "When set, agent responses are stored under <folder>/agent_responses/ "
             "alongside the video and log files.",
    )

    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    mqtt_messages = AnomalySubmitter.load_mqtt_messages(args.mqtt_messages)

    submitter = AnomalySubmitter(
        edge_host=args.edge_host,
        edge_port=args.edge_port,
        adk_url=args.adk_url,
        timeout=args.timeout,
        clip_path=args.clip_path,
        anomaly_time=args.anomaly_time,
        clip_start_time=args.clip_start_time,
        log_path=args.log_path,
        error=args.error,
        expected_topic=args.expected_topic,
        mqtt_messages=mqtt_messages,
        data_source=args.data_source,
        anomaly_folder=args.anomaly_folder,
    )

    success = submitter.submit()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()








