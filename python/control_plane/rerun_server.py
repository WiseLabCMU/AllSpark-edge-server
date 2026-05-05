"""
Rerun Server – AllSpark Edge Server
====================================

Starts a Rerun web viewer and loads either:
  • ALL stored anomaly responses (default mode), OR
  • A single anomaly folder's contents (per-anomaly triage mode) when
    ``--anomaly-folder`` is supplied.

In per-anomaly mode the viewer shows every video in ``video_logs/``, every
Kafka log row in ``kafka_logs/`` as a scalar time series, the error row as
a big red cross, and every agent response as a Markdown panel — all on one
shared epoch-nanosecond timeline. The cursor is pre-positioned at the
error timestamp so a factory floor manager opens the page already looking
at the failure moment and can scrub backward / forward in time.

Usage:
    python rerun_server.py                                  # all anomalies
    python rerun_server.py --port 9090                      # custom port
    python rerun_server.py --anomaly-folder /path/to/folder # single anomaly
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import sys
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Tuple, Dict

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).parent
_PYTHON_DIR = _THIS_DIR.parent
_PROJECT_ROOT = _PYTHON_DIR.parent  # repo root (AllSpark-edge-server/)
sys.path.insert(0, str(_PYTHON_DIR))


def _load_config() -> dict:
    """Load the edge-server config.yaml."""
    import yaml
    config_path = _PYTHON_DIR / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f).get("mobile_client", {})
    return {}


# Rerun's AssetVideo only supports these codecs. Anything else must be
# transcoded before we feed it into the viewer.
_RERUN_SUPPORTED_CODECS = {"h264", "hevc", "h265", "av1", "vp9"}


def _probe_duration_seconds(video_path: Path) -> Optional[float]:
    """Return the video duration in seconds via ffprobe, or None on failure."""
    import shutil
    import subprocess
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nokey=1:noprint_wrappers=1",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=5,
        )
        s = out.stdout.strip()
        return float(s) if s else None
    except Exception as exc:
        logger.debug("ffprobe duration failed for %s: %s", video_path, exc)
        return None


def _probe_codec(video_path: Path) -> Optional[str]:
    """Return the primary video stream codec (lowercase) or None."""
    import shutil
    import subprocess
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=nokey=1:noprint_wrappers=1",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=5,
        )
        codec = out.stdout.strip().lower()
        return codec or None
    except Exception as exc:
        logger.debug("ffprobe failed for %s: %s", video_path, exc)
        return None


def _ensure_rerun_compatible_video(video_path: Path) -> Path:
    """
    Return a path to a video Rerun can decode. If *video_path* already uses
    a supported codec it is returned unchanged. Otherwise the file is
    transcoded to H.264 once and the result is cached next to the original
    as ``<stem>.h264.mp4``. Subsequent calls reuse the cached version.

    Falls back to the original path (and lets the caller fail loudly) if
    ``ffmpeg`` is unavailable or transcoding fails.
    """
    codec = _probe_codec(video_path)
    if codec is not None and codec in _RERUN_SUPPORTED_CODECS:
        return video_path

    # codec is None (ffprobe unavailable) or codec is unsupported — transcode
    cached = video_path.with_suffix(".h264.mp4")
    # If the cache already exists and is newer than the source, reuse it.
    if cached.exists() and cached.stat().st_mtime >= video_path.stat().st_mtime:
        logger.info("Using cached H.264 transcode: %s", cached.name)
        return cached

    import shutil
    import subprocess
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning(
            "Video %s uses unsupported codec '%s' but ffmpeg is not "
            "available – cannot transcode.",
            video_path.name, codec,
        )
        return video_path

    logger.info(
        "Transcoding %s (%s → h264) → %s",
        video_path.name, codec, cached.name,
    )
    try:
        proc = subprocess.run(
            [
                ffmpeg, "-y", "-v", "error",
                "-i", str(video_path),
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(cached),
            ],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            logger.warning(
                "ffmpeg transcode failed for %s: %s",
                video_path.name, proc.stderr.strip() or proc.stdout.strip(),
            )
            if cached.exists():
                cached.unlink(missing_ok=True)
            return video_path
    except Exception as exc:
        logger.warning("ffmpeg transcode errored for %s: %s", video_path.name, exc)
        return video_path

    logger.info("Transcode complete: %s", cached.name)
    return cached


def _free_port(port: int, label: str = "") -> None:
    """
    Terminate any process currently listening on *port* so we can bind it.

    Uses ``lsof`` (available on macOS and most Linux installs). Safe no-op
    if the port is already free or ``lsof`` is unavailable. We only send
    SIGTERM – if the peer is not ours this is still safe to attempt
    because the user gets a clear log line either way.
    """
    import shutil
    import signal
    import subprocess

    lsof = shutil.which("lsof")
    if not lsof:
        logger.debug("lsof not found – skipping port %d cleanup", port)
        return

    try:
        out = subprocess.run(
            [lsof, "-iTCP:%d" % port, "-sTCP:LISTEN", "-t", "-P"],
            capture_output=True, text=True, timeout=2,
        )
    except Exception as exc:
        logger.debug("lsof probe failed for port %d: %s", port, exc)
        return

    pids = [int(p) for p in out.stdout.split() if p.strip().isdigit()]
    if not pids:
        return

    own_pid = os.getpid()
    for pid in pids:
        if pid == own_pid:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info(
                "Freed %s port %d: terminated pid %d (previous instance)",
                label or "rerun", port, pid,
            )
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.warning(
                "Cannot terminate pid %d on port %d (permission denied) — "
                "please kill it manually.", pid, port,
            )

    # Give the OS a moment to release the socket
    time.sleep(0.4)


class RerunAnomalyViewer:
    """
    Initialises a Rerun recording, loads stored anomaly data (videos,
    kafka logs, agent summaries), and serves the web viewer.

    Two modes:
      • Default: walks the global agent_responses/ tree and loads every
        stored response (legacy behaviour).
      • Per-anomaly: when ``anomaly_folder`` is set, loads ONLY that
        folder's videos + kafka logs + agent_responses sub-tree, and
        pre-positions the timeline cursor at the detected error.
    """

    def __init__(
        self,
        web_port: int = 9090,
        responses_path: Optional[str] = None,
        anomaly_folder: Optional[str] = None,
    ):
        self._web_port = web_port
        config = _load_config()
        self._responses_root = Path(
            responses_path
            or os.path.join(
                str(_PROJECT_ROOT),
                config.get("agentResponsePath", "uploads/agent_responses"),
            )
        )
        self._agent_data_folder = self._resolve_agent_video_folder()
        self._anomaly_folder: Optional[Path] = (
            Path(anomaly_folder) if anomaly_folder else None
        )

        # Track Y-axis assignment for kafka topics (built lazily during load)
        self._topic_y: Dict[str, int] = {}
        self._error_y: int = 1  # overwritten when topics are discovered

        logger.info("Rerun viewer – responses root: %s", self._responses_root)
        if self._anomaly_folder:
            logger.info(
                "Rerun viewer – per-anomaly mode for: %s", self._anomaly_folder
            )

    # ------------------------------------------------------------------
    # Agent data folder resolution (same logic as submit script)
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_agent_video_folder() -> Optional[Path]:
        """Try to find the agentic framework's active video data folder."""
        framework_root = _PROJECT_ROOT.parent / "allspark-agentic-framework"
        if not framework_root.exists():
            return None
        try:
            import yaml
            cfg_path = framework_root / "allspark_agent" / "config" / "config.yaml"
            if not cfg_path.exists():
                return None
            with open(cfg_path) as f:
                main_cfg = yaml.safe_load(f)
            profile = main_cfg.get("active_profile", "")
            if not profile:
                return None
            profile_path = cfg_path.parent / profile
            if not profile_path.exists():
                return None
            with open(profile_path) as f:
                pcfg = yaml.safe_load(f)
            root_data = pcfg.get("root_data_folder", "")
            video_sub = (pcfg.get("data_paths") or {}).get("video", "camera-video")
            resolved = framework_root / root_data / video_sub
            return resolved if resolved.exists() else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Initialise Rerun, load data, and serve the web viewer."""
        import rerun as rr
        import rerun.blueprint as rrb

        # Pre-flight: free the ports we need. Rerun's embedded gRPC server
        # always binds 9876, so we must clear both that and our web port
        # before re-initialising, otherwise the call raises
        # "Address already in use".
        _free_port(self._web_port, label="web-viewer")
        _free_port(9876, label="rerun-grpc")

        app_id = (
            f"allspark_anomaly_{self._anomaly_folder.name}"
            if self._anomaly_folder
            else "allspark_anomaly_viewer"
        )
        rr.init(app_id)

        # Start gRPC server + web viewer
        server_uri = rr.serve_grpc()
        logger.info("Rerun gRPC server at: %s", server_uri)
        rr.serve_web_viewer(
            connect_to=server_uri,
            open_browser=False,
            web_port=self._web_port,
            bind="0.0.0.0",
        )
        logger.info("Rerun web viewer serving on 0.0.0.0:%d", self._web_port)

        # Load data first so we know how many topics there are, THEN send the
        # blueprint with a properly-scaled Y axis. (Per-anomaly mode only.)
        if self._anomaly_folder:
            self._load_single_anomaly(rr, self._anomaly_folder)
            self._send_per_anomaly_blueprint(rr, rrb)
        else:
            self._send_blueprint(rr, rrb)
            self._load_all_responses(rr)

        logger.info("Rerun viewer ready – open http://0.0.0.0:%d  (or use rerunExternalHost from config)", self._web_port)

    # ------------------------------------------------------------------
    # Blueprints
    # ------------------------------------------------------------------

    @staticmethod
    def _send_blueprint(rr, rrb) -> None:
        """Default (all-anomalies) blueprint – unchanged from original."""
        blueprint = rrb.Blueprint(
            rrb.Vertical(
                contents=[
                    rrb.Horizontal(
                        contents=[
                            rrb.Spatial2DView(origin="/video_anomalies"),
                            rrb.TextDocumentView(origin="/agent_response"),
                        ]
                    ),
                ],
                row_shares=[1],
            ),
            rrb.BlueprintPanel(state="collapsed"),
            rrb.SelectionPanel(state="collapsed"),
            rrb.TimePanel(state="expanded"),
        )
        rr.send_blueprint(blueprint)

    def _send_per_anomaly_blueprint(self, rr, rrb) -> None:
        """
        Per-anomaly triage layout: top row holds all videos side-by-side
        plus the agent markdown; bottom row is the kafka time series with
        a cursor-relative ±60s window.
        """
        # Build a Spatial2DView per discovered video entity (error + ref).
        view_entries = self._topic_y.get("__channels__", []) or []
        video_views = []
        for entry in view_entries:
            # Each entry is (display_label, entity_path)
            try:
                label, origin = entry  # type: ignore[misc]
            except (TypeError, ValueError):
                # Backwards-compat: legacy plain-string channel name
                label = str(entry)
                origin = f"/video_anomalies/{label}"
            video_views.append(rrb.Spatial2DView(origin=origin, name=label))
        if not video_views:
            video_views = [rrb.Spatial2DView(origin="/video_anomalies", name="Video")]

        top_row = rrb.Horizontal(
            contents=[
                *video_views,
                rrb.TextDocumentView(origin="/agent_response", name="Agent Analysis"),
            ]
        )

        # Y axis range: leave headroom above the highest-numbered topic
        y_max = max(self._error_y + 1, 2)

        time_series = rrb.TimeSeriesView(
            origin="/kafka_traffic",
            name="Kafka / Process Log",
            axis_y=rrb.ScalarAxis(range=(0.0, float(y_max)), zoom_lock=True),
            plot_legend=rrb.PlotLegend(visible=True),
            time_ranges=rrb.VisibleTimeRanges(
                timeline="timeline",
                start=rrb.TimeRangeBoundary.cursor_relative(seconds=-60),
                end=rrb.TimeRangeBoundary.cursor_relative(seconds=60),
            ),
        )

        blueprint = rrb.Blueprint(
            rrb.Vertical(contents=[top_row, time_series], row_shares=[2, 1]),
            rrb.BlueprintPanel(state="collapsed"),
            rrb.SelectionPanel(state="collapsed"),
            rrb.TimePanel(state="expanded"),
        )
        rr.send_blueprint(blueprint)

    # ------------------------------------------------------------------
    # Data loading – legacy "all responses" mode (unchanged)
    # ------------------------------------------------------------------

    def _load_all_responses(self, rr) -> None:
        """Walk the response store and load each anomaly."""
        if not self._responses_root.exists():
            logger.warning("Responses root not found: %s", self._responses_root)
            rr.log(
                "agent_response",
                rr.TextDocument(
                    "# No anomaly responses found\n\n"
                    f"Expected at `{self._responses_root}`",
                    media_type=rr.MediaType.MARKDOWN,
                ),
            )
            return

        response_files = sorted(
            self._responses_root.rglob("response.json"),
            key=lambda p: p.stat().st_mtime,
        )

        if not response_files:
            rr.log(
                "agent_response",
                rr.TextDocument(
                    "# No anomaly responses found\n\n"
                    "Submit an anomaly via the Debug page to get started.",
                    media_type=rr.MediaType.MARKDOWN,
                ),
            )
            return

        loaded = 0
        for rf in response_files:
            try:
                self._load_single_response(rr, rf)
                loaded += 1
            except Exception as exc:
                logger.warning("Failed to load %s: %s", rf, exc)

        logger.info(
            "Loaded %d / %d anomaly responses into Rerun", loaded, len(response_files)
        )

    def _load_single_response(self, rr, response_file: Path) -> None:
        """Load one anomaly's video + summary into Rerun (legacy mode)."""
        data = json.loads(response_file.read_text(encoding="utf-8"))
        anomaly_time_str = data.get("anomaly_time", "")
        clip_path_raw = data.get("clip_path", "")
        summary = data.get("summary", "")
        session_id = data.get("session_id", "")
        status = data.get("status", "unknown")

        # Determine a timeline timestamp from anomaly_time
        ts_epoch = self._parse_epoch(anomaly_time_str)
        if ts_epoch is None:
            ts_epoch = response_file.stat().st_mtime

        ts_ns = np.datetime64(int(ts_epoch * 1_000) * 1_000_000, "ns")
        rr.set_time("timeline", timestamp=ts_ns)

        clip_basename = os.path.basename(clip_path_raw) if clip_path_raw else "N/A"
        md = (
            f"# Anomaly Analysis – {anomaly_time_str}\n\n"
            f"**Clip:** `{clip_basename}`  \n"
            f"**Status:** {status}  \n"
            f"**Session:** `{session_id}`\n\n"
            f"---\n\n"
            f"{summary}"
        )
        rr.log("agent_response", rr.TextDocument(md, media_type=rr.MediaType.MARKDOWN))

        video_path = self._find_video(clip_path_raw, response_file.parent)
        if video_path and video_path.exists():
            self._log_video(rr, video_path, ts_epoch, entity="video_anomalies")
        else:
            logger.info(
                "Video not found for %s (tried: %s)", clip_basename, video_path
            )

    def _find_video(self, clip_path_raw: str, response_dir: Path) -> Optional[Path]:
        """Resolve the video file path, checking multiple locations."""
        if not clip_path_raw:
            return None
        basename = os.path.basename(clip_path_raw)

        local = response_dir / "video_clips" / basename
        if local.exists():
            return local

        if self._agent_data_folder:
            agent_path = self._agent_data_folder / basename
            if agent_path.exists():
                return agent_path

        abs_path = Path(clip_path_raw)
        if abs_path.is_absolute() and abs_path.exists():
            return abs_path

        datacapture_root = _PYTHON_DIR.parent.parent / "allspark-datacapture"
        if datacapture_root.exists():
            for match in datacapture_root.rglob(basename):
                return match

        return None

    # ------------------------------------------------------------------
    # Data loading – per-anomaly mode (NEW)
    # ------------------------------------------------------------------

    def _load_single_anomaly(self, rr, folder: Path) -> None:
        """
        Load one anomaly folder's full contents into Rerun:
          - All videos in video_logs/  (each as its own /video_anomalies/<ch>)
          - All kafka logs in kafka_logs/  (rows → /kafka_traffic/<topic>)
          - Error rows → /kafka_traffic/error  (red cross at top of Y axis)
          - All agent_responses/<id>/response.json → /agent_response markdown
          - Cursor pre-positioned at the detected error timestamp.
        """
        if not folder.exists() or not folder.is_dir():
            self._log_warning_doc(
                rr, f"Anomaly folder not found:\n`{folder}`"
            )
            return

        # 1. Discover artefacts
        # Support both naming conventions:
        #   video_logs/        — legacy/manual layout
        #   video_anomaly_data/ — created by kafka_error_event_monitor.py
        videos: List[Path] = []
        _video_dir = next(
            (folder / d for d in ("video_logs", "video_anomaly_data")
             if (folder / d).exists()),
            None,
        )
        if _video_dir is not None:
            for vid in sorted(_video_dir.glob("*.mp4")):
                # Skip cached H.264 transcodes (created by a previous run);
                # _ensure_rerun_compatible_video() will reuse them implicitly.
                if vid.stem.endswith(".h264"):
                    continue
                videos.append(vid)
        kafka_files: List[Path] = []
        # Support both naming conventions:
        #   kafka_logs/        — legacy/manual layout
        #   kafka_anomaly_data/ — created by kafka_error_event_monitor.py
        _kafka_dir = next(
            (folder / d for d in ("kafka_logs", "kafka_anomaly_data")
             if (folder / d).exists()),
            None,
        )
        if _kafka_dir is not None:
            kafka_files = sorted(
                list(_kafka_dir.glob("*.csv"))
                + list(_kafka_dir.glob("*.txt"))
                + list(_kafka_dir.glob("*.log"))
            )
        response_files = sorted(
            (folder / "agent_responses").rglob("response.json")
        ) if (folder / "agent_responses").exists() else []

        logger.info(
            "Per-anomaly mode: %d video(s), %d kafka log(s), %d response(s)",
            len(videos), len(kafka_files), len(response_files),
        )

        # 2. Parse all kafka logs first so we know the error timestamp + topics
        all_rows: List[dict] = []
        error_epoch: Optional[float] = None
        for kf in kafka_files:
            try:
                rows, err_ts = self._parse_kafka_log(kf)
                all_rows.extend(rows)
                if err_ts is not None and (error_epoch is None or err_ts < error_epoch):
                    error_epoch = err_ts
            except Exception as exc:
                logger.warning("Could not parse kafka log %s: %s", kf, exc)

        # Fallback: derive error time from the folder name
        # (e.g. "anomaly_2026-04-02T20-49-04" -> 2026-04-02T20:49:04)
        if error_epoch is None:
            error_epoch = self._parse_epoch_from_folder_name(folder.name)
            if error_epoch is not None:
                logger.info(
                    "No 'error' row found in kafka logs; using folder-name "
                    "timestamp as anomaly anchor."
                )

        # 3. Assign Y-axis values to each topic and pre-declare series styles
        unique_topics = sorted({r["topic"] for r in all_rows if r.get("topic") and not r.get("is_error")})
        for i, topic in enumerate(unique_topics, start=1):
            self._topic_y[topic] = i
            rr.log(
                f"kafka_traffic/{topic}",
                rr.SeriesPoints(
                    names=topic, markers="circle", marker_sizes=5
                ),
                static=True,
            )
        self._error_y = max(len(unique_topics) + 1, 2)
        rr.log(
            "kafka_traffic/error",
            rr.SeriesPoints(
                colors=[255, 0, 0], names="ANOMALY",
                markers="cross", marker_sizes=15,
            ),
            static=True,
        )

        # 4. Plot every kafka row at its real timestamp
        for row in all_rows:
            ts_ns = np.datetime64(int(row["epoch"] * 1_000) * 1_000_000, "ns")
            rr.set_time("timeline", timestamp=ts_ns)
            if row.get("is_error"):
                rr.log("kafka_traffic/error", rr.Scalars(self._error_y))
            else:
                topic = row["topic"]
                y = self._topic_y.get(topic)
                if y is not None:
                    rr.log(f"kafka_traffic/{topic}", rr.Scalars(y))

        # 5. Render videos. Anchor each video's clip-start so its frames line
        # up on the wall-clock timeline. Strategy:
        #   (a) prefer request.json's clip_start_timestamp if present
        #   (b) else assume the video ENDS at the error timestamp
        #       (so clip_start = error - duration)
        # Track channels for blueprint generation.
        channels: List[str] = []
        request_clip_start = self._find_clip_start_in_requests(response_files)

        for vid in videos:
            channel = self._extract_channel(vid.name)
            channels.append(channel)
            entity = f"video_anomalies/{channel}_error"
            anchor = self._compute_video_anchor(
                vid, request_clip_start, error_epoch
            )
            self._log_video(rr, vid, anchor, entity=entity)

            # Also load the matching reference clip (a "what good looks
            # like" recording) so the operator can compare the two side-by
            # side at any point on the timeline. The reference video is
            # anchored to the SAME wall-clock window so frames align.
            ref_path = self._find_reference_video(channel)
            if ref_path is not None:
                ref_entity = f"video_anomalies/{channel}_reference"
                self._log_video(rr, ref_path, anchor, entity=ref_entity)

        # Stash the entity names for the blueprint method to consume.
        # Each tuple is (display_label, entity_path).
        view_entries: List[Tuple[str, str]] = []
        for ch in channels:
            view_entries.append((f"{ch} (error)", f"/video_anomalies/{ch}_error"))
            if self._find_reference_video(ch) is not None:
                view_entries.append(
                    (f"{ch} (reference)", f"/video_anomalies/{ch}_reference")
                )
        self._topic_y["__channels__"] = view_entries  # type: ignore[assignment]

        # 6. Render agent responses as Markdown anchored at error time
        if response_files:
            # Log a placeholder a bit BEFORE the error so the panel isn't
            # blank while the operator scrubs through pre-anomaly footage.
            # The agent response itself is logged AT the error timestamp,
            # so rerun's "latest-before-cursor" semantics naturally swap it
            # in as the cursor crosses the error.
            if error_epoch is not None:
                pre_md = (
                    "# 🟢 No anomaly detected yet\n\n"
                    "_Scrub the timeline forward to the red cross to see "
                    "the agent's analysis of the failure._"
                )
                pre_ts = np.datetime64(
                    int((error_epoch - 60) * 1_000) * 1_000_000, "ns"
                )
                rr.set_time("timeline", timestamp=pre_ts)
                rr.log(
                    "agent_response",
                    rr.TextDocument(pre_md, media_type=rr.MediaType.MARKDOWN),
                )

            for i, rf in enumerate(response_files):
                try:
                    self._log_agent_response_md(
                        rr, rf, error_epoch, anchor_index=i
                    )
                except Exception as exc:
                    logger.warning("Failed to render response %s: %s", rf, exc)
        else:
            # Show a helpful placeholder so the panel isn't empty
            md = (
                f"# {folder.name}\n\n"
                "_No agent analysis has been run for this anomaly yet._\n\n"
                "Use the **Debug** page or `submit_anomaly_to_edge.py` "
                "with `--anomaly-folder` to trigger an analysis."
            )
            if error_epoch is not None:
                ts_ns = np.datetime64(
                    int(error_epoch * 1_000) * 1_000_000, "ns"
                )
                rr.set_time("timeline", timestamp=ts_ns)
            rr.log(
                "agent_response",
                rr.TextDocument(md, media_type=rr.MediaType.MARKDOWN),
            )

        # 7. Pre-position the cursor at the error so the viewer opens there
        if error_epoch is not None:
            ts_ns = np.datetime64(int(error_epoch * 1_000) * 1_000_000, "ns")
            rr.set_time("timeline", timestamp=ts_ns)
            logger.info(
                "Cursor pre-positioned at anomaly time: %s",
                datetime.fromtimestamp(error_epoch, tz=timezone.utc).isoformat(),
            )

    # ------------------------------------------------------------------
    # Per-anomaly helpers (NEW)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_kafka_log(path: Path) -> Tuple[List[dict], Optional[float]]:
        """
        Parse a Kafka/process-log file.  Handles two formats:

        1. CSV (comma or semicolon delimited) with header row
        2. kafka_payload_snapshot.log format: ``topic=X key=Y value=Z``
           written by kafka_logger.write_message().  Each line carries a
           timestamp embedded in the JSON value field.

        Returns a tuple of (rows, error_epoch):
          rows: list of {epoch, topic, raw, is_error}
          error_epoch: epoch seconds of the FIRST error row, or None.
        """
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            return [], None

        # ---- Detect kafka_payload_snapshot format: first non-empty line
        # starts with "topic=" (no CSV header) ----
        first_line = next((l for l in text.splitlines() if l.strip()), "")
        if first_line.startswith("topic="):
            return RerunAnomalyViewer._parse_kv_snapshot(text)

        # Auto-detect delimiter from the header line
        header_line = text.splitlines()[0]
        delimiter = ";" if header_line.count(";") > header_line.count(",") else ","
        # Tabs sneak in via .txt exports
        if "\t" in header_line and header_line.count("\t") > header_line.count(delimiter):
            delimiter = "\t"

        rows: List[dict] = []
        error_epoch: Optional[float] = None

        reader = csv.reader(text.splitlines(), delimiter=delimiter)
        try:
            header = next(reader)
        except StopIteration:
            return [], None

        # Best-effort column index lookup
        def col_idx(*names: str) -> Optional[int]:
            for n in names:
                for i, h in enumerate(header):
                    if h.strip().lower() == n.lower():
                        return i
            return None

        ts_idx = col_idx("timestamp", "time") or 0
        topic_idx = col_idx("topic", "process") or 1
        result_state_idx = col_idx("resultState", "result_state", "state")
        result_code_idx = col_idx("resultCode", "result_code", "code")

        for raw_row in reader:
            if not raw_row or not any(c.strip() for c in raw_row):
                continue
            try:
                ts_str = raw_row[ts_idx].strip()
                if not ts_str:
                    continue
                epoch = RerunAnomalyViewer._parse_epoch(ts_str)
                if epoch is None:
                    continue

                topic = raw_row[topic_idx].strip() if topic_idx < len(raw_row) else ""
                result_state = (
                    raw_row[result_state_idx].strip()
                    if result_state_idx is not None and result_state_idx < len(raw_row)
                    else ""
                )
                result_code = (
                    raw_row[result_code_idx].strip()
                    if result_code_idx is not None and result_code_idx < len(raw_row)
                    else ""
                )

                is_error = (
                    topic.lower() == "error"
                    or (result_state and result_state.lower() not in ("ok", ""))
                    or bool(result_code)
                )

                rows.append(
                    {
                        "epoch": epoch,
                        "topic": topic if not is_error else "error",
                        "raw": raw_row,
                        "is_error": is_error,
                    }
                )
                if is_error and error_epoch is None:
                    error_epoch = epoch
            except Exception as exc:
                logger.debug("Skipping malformed row in %s: %s", path.name, exc)
                continue

        return rows, error_epoch

    @staticmethod
    def _parse_epoch_from_folder_name(name: str) -> Optional[float]:
        """
        Extract an epoch from folder names.  Handles two formats:
          • ``anomaly_2026-04-02T20-49-04``  (dashes between parts)
          • ``Anomaly_20260429T043922Z``      (compact, as written by kafka_error_event_monitor)
        Returns None if no timestamp pattern is found.
        """
        # Format 1: YYYY-MM-DDTHH-MM-SS
        m = re.search(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})", name)
        if m:
            try:
                iso = f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}"
                return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S").replace(
                    tzinfo=timezone.utc
                ).timestamp()
            except ValueError:
                pass
        # Format 2: YYYYMMDDTHHMMSS[Z]
        m = re.search(r"(\d{8})T(\d{6})Z?", name)
        if m:
            try:
                return datetime.strptime(
                    m.group(1) + m.group(2), "%Y%m%d%H%M%S"
                ).replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                pass
        return None

    @staticmethod
    def _parse_kv_snapshot(text: str) -> Tuple[List[dict], Optional[float]]:
        """
        Parse ``kafka_payload_snapshot.log`` lines of the form::

            topic=errorevent key=None value={"ts_ms": 1234567890, ...}

        Extracts the Kafka message timestamp from the JSON value field
        (key ``ts_ms`` in epoch-milliseconds, or ``timestamp`` / ``ts``
        in epoch-seconds or ISO-8601).  Error rows are those on the
        ``errorevent`` topic or those whose value contains an error code.
        """
        rows: List[dict] = []
        error_epoch: Optional[float] = None
        _kv_re = re.compile(r"^topic=(\S+)\s+key=\S+\s+value=(.+)$")

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _kv_re.match(line)
            if not m:
                continue
            topic = m.group(1)
            raw_value = m.group(2)

            # Extract timestamp from JSON value
            epoch: Optional[float] = None
            try:
                val = json.loads(raw_value)
                if isinstance(val, dict):
                    ts_ms = val.get("ts_ms") or val.get("kafka_ts_ms")
                    if ts_ms:
                        epoch = float(ts_ms) / 1000.0
                    elif "timestamp" in val:
                        ts = val["timestamp"]
                        if isinstance(ts, (int, float)) and ts > 1e10:
                            epoch = float(ts) / 1000.0
                        elif isinstance(ts, (int, float)):
                            epoch = float(ts)
                        elif isinstance(ts, str):
                            epoch = RerunAnomalyViewer._parse_epoch(ts)
                    elif "ts" in val:
                        ts = val["ts"]
                        epoch = float(ts) / 1000.0 if float(ts) > 1e10 else float(ts)
                    # TFM machine messages: timestamp is nested under MessageHeader
                    if epoch is None:
                        mh_ts = (val.get("MessageHeader") or {}).get("TimeStamp")
                        if mh_ts:
                            epoch = RerunAnomalyViewer._parse_epoch(str(mh_ts))
            except Exception:
                pass

            if epoch is None:
                continue

            is_error = topic.lower() in ("errorevent", "error")
            rows.append({"epoch": epoch, "topic": topic, "raw": line, "is_error": is_error})
            if is_error and error_epoch is None:
                error_epoch = epoch

        return rows, error_epoch

    @staticmethod
    def _extract_channel(filename: str) -> str:
        """``ch3_pellet_feeder_error.mp4`` / ``clip_ch2_2026_...mp4`` → ``ch2``; fallback: filename stem."""
        # Matches ch<N> anywhere in the filename (handles both "ch3_foo.mp4" and "clip_ch2_ts.mp4")
        m = re.search(r"(ch\d+)", filename, re.IGNORECASE)
        if m:
            return m.group(1).lower()
        return Path(filename).stem.split("_")[0]

    def _find_reference_video(self, channel: str) -> Optional[Path]:
        """
        Find a reference ("nominal / good cycle") video for *channel*.

        Searches several known locations in priority order:

          1. The configured agentic-framework data folder (resolved via
             ``_resolve_agent_video_folder``) for files named
             ``<channel>_*reference*.mp4``.
          2. ``allspark-agentic-framework/allspark_agent/sample_data/**``
             rooted at the framework repo, for the same pattern.

        Returns ``None`` if no match is found. The first match wins.
        """
        candidates: List[Path] = []
        roots: List[Path] = []
        if self._agent_data_folder is not None:
            roots.append(self._agent_data_folder)
        framework_root = _PROJECT_ROOT.parent / "allspark-agentic-framework"
        if framework_root.exists():
            roots.append(framework_root / "allspark_agent" / "sample_data")

        pattern_re = re.compile(
            rf"^{re.escape(channel)}_.*reference.*\.mp4$",
            re.IGNORECASE,
        )
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*.mp4"):
                if pattern_re.match(path.name):
                    candidates.append(path)

        if not candidates:
            logger.debug("No reference video found for channel %s", channel)
            return None
        # Prefer the shortest path (less nested = more canonical)
        candidates.sort(key=lambda p: len(p.parts))
        chosen = candidates[0]
        logger.info("Reference video for %s: %s", channel, chosen)
        return chosen

    def _find_clip_start_in_requests(
        self, response_files: List[Path]
    ) -> Optional[float]:
        """
        Look at sibling ``request.json`` files to find a usable
        clip_start_timestamp (epoch seconds). Returns None if not found.
        """
        for rf in response_files:
            req = rf.parent / "request.json"
            if not req.exists():
                continue
            try:
                data = json.loads(req.read_text(encoding="utf-8"))
                cst = data.get("clip_start_timestamp", "")
                if cst:
                    # may be epoch ms (int/str) or ISO string
                    try:
                        return float(cst) / 1000.0  # epoch ms
                    except (TypeError, ValueError):
                        epoch = self._parse_epoch(str(cst))
                        if epoch:
                            return epoch
                # Fallback: ISO clip_start_time
                cst_iso = data.get("clip_start_time", "")
                if cst_iso:
                    epoch = self._parse_epoch(cst_iso)
                    if epoch:
                        return epoch
            except Exception:
                continue
        return None

    @staticmethod
    def _compute_video_anchor(
        video_path: Path,
        request_clip_start: Optional[float],
        error_epoch: Optional[float],
    ) -> float:
        """
        Decide where the video's first frame should sit on the timeline.
        Priority:
          1. request.json's clip_start_timestamp (if available)
          2. error_epoch - video_duration  (clip "ends at the error")
          3. video file mtime (last resort)
        """
        if request_clip_start is not None:
            return request_clip_start

        if error_epoch is not None:
            duration_s = _probe_duration_seconds(video_path)
            if duration_s is None:
                # Fall back to a transcoded sidecar if it exists, since
                # rerun's AssetVideo can't decode some legacy codecs.
                cached = video_path.with_suffix(".h264.mp4")
                if cached.exists():
                    duration_s = _probe_duration_seconds(cached)
            if duration_s is not None and duration_s > 0:
                return error_epoch - duration_s
            logger.warning(
                "Could not determine duration for %s; falling back to file mtime "
                "(video may not align on the timeline).",
                video_path.name,
            )

        return video_path.stat().st_mtime

    def _log_agent_response_md(
        self,
        rr,
        response_file: Path,
        anchor_epoch: Optional[float],
        anchor_index: int,
    ) -> None:
        """Render a stored response.json as Markdown anchored at the error time."""
        data = json.loads(response_file.read_text(encoding="utf-8"))
        anomaly_time_str = data.get("anomaly_time", "")
        clip_path_raw = data.get("clip_path", "")
        summary = data.get("summary", "") or "_(empty agent summary)_"
        session_id = data.get("session_id", "")
        status = data.get("status", "unknown")
        clip_basename = os.path.basename(clip_path_raw) if clip_path_raw else "N/A"

        header = (
            f"# 🚨 Anomaly Analysis\n\n"
            f"**When:** {anomaly_time_str}  \n"
            f"**Clip:** `{clip_basename}`  \n"
            f"**Status:** `{status}`  \n"
            f"**Session:** `{session_id}`\n\n"
            f"---\n\n"
        )
        md = header + summary

        if anchor_epoch is not None:
            ts_ns = np.datetime64(int(anchor_epoch * 1_000) * 1_000_000, "ns")
            rr.set_time("timeline", timestamp=ts_ns)

        # Use a stable single entity so the panel always shows the latest /
        # selected response (Rerun keeps history; cursor selects which is shown).
        rr.log("agent_response", rr.TextDocument(md, media_type=rr.MediaType.MARKDOWN))

    def _log_warning_doc(self, rr, message: str) -> None:
        rr.log(
            "agent_response",
            rr.TextDocument(
                f"# ⚠️ {message}", media_type=rr.MediaType.MARKDOWN
            ),
        )

    # ------------------------------------------------------------------
    # Shared video logger (used by both modes)
    # ------------------------------------------------------------------

    @staticmethod
    def _log_video(
        rr,
        video_path: Path,
        clip_start_epoch: float,
        entity: str = "video_anomalies",
    ) -> None:
        """Log a video asset into Rerun with real-time timestamps."""
        try:
            # Rerun only supports H.264/H.265/AV1/VP9. If this clip uses an
            # older codec (e.g. MPEG-4 Part 2), transcode it once to a cached
            # H.264 sidecar and log that instead.
            usable_path = _ensure_rerun_compatible_video(video_path)

            video_asset = rr.AssetVideo(path=str(usable_path))
            rr.log(entity, video_asset, static=True)

            # ``read_frame_timestamps_nanos()`` returns int64 nanoseconds
            # *relative to the start of the video*. To place each frame on
            # the wall-clock timeline we add the clip-start datetime64 to
            # those offsets. NumPy requires the offsets be ``timedelta64``
            # (not raw int64) for the addition to produce ``datetime64``;
            # otherwise you get garbage values that render as 1970 in the
            # rerun viewer.
            frame_offsets_ns = video_asset.read_frame_timestamps_nanos()
            frame_offsets_td = np.asarray(frame_offsets_ns, dtype="int64").astype(
                "timedelta64[ns]"
            )
            clip_start_ns = np.datetime64(
                int(clip_start_epoch * 1_000) * 1_000_000, "ns"
            )
            rt_timestamps = clip_start_ns + frame_offsets_td

            rr.send_columns(
                entity,
                indexes=[rr.TimeColumn("timeline", timestamp=rt_timestamps)],
                columns=rr.VideoFrameReference.columns_nanos(frame_offsets_ns),
            )
            logger.info(
                "Loaded video %s into %s (%d frames, anchored at %s)",
                usable_path.name,
                entity,
                len(frame_offsets_ns),
                datetime.fromtimestamp(clip_start_epoch, tz=timezone.utc).isoformat(),
            )
        except Exception as exc:
            logger.warning("Failed to load video %s: %s", video_path, exc)

    @staticmethod
    def _parse_epoch(ts_str: str) -> Optional[float]:
        """Parse an ISO-8601 timestamp to epoch seconds (UTC-aware)."""
        if not ts_str:
            return None
        s = ts_str.strip()
        # Drop trailing Z and treat as UTC
        if s.endswith("Z"):
            s = s[:-1]
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                dt = datetime.strptime(s[: len(fmt) + 6 if "%f" in fmt else len(fmt)], fmt)
                return dt.replace(tzinfo=timezone.utc).timestamp()
            except (ValueError, IndexError):
                continue
        # Last resort: fromisoformat (handles offsets)
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return dt.timestamp()
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Start the Rerun anomaly viewer (all anomalies or single)."
    )
    parser.add_argument(
        "--port", type=int, default=9090,
        help="Web viewer port (default: 9090, must match control_plane config).",
    )
    parser.add_argument(
        "--responses-path", default=None,
        help="Override path to the global agent_responses folder.",
    )
    parser.add_argument(
        "--anomaly-folder", default=None,
        help=(
            "Path to a single anomaly folder (e.g. "
            "uploads/anomaly_2026-04-02T20-49-04). When set, the viewer "
            "loads ONLY that anomaly's videos + kafka logs + agent_responses "
            "and pre-positions the timeline cursor at the detected error."
        ),
    )
    args = parser.parse_args()

    viewer = RerunAnomalyViewer(
        web_port=args.port,
        responses_path=args.responses_path,
        anomaly_folder=args.anomaly_folder,
    )
    viewer.start()

    print(f"\n  Rerun viewer running at http://127.0.0.1:{args.port}")
    if args.anomaly_folder:
        print(f"  Mode: per-anomaly  →  {args.anomaly_folder}")
    print("  Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nRerun viewer stopped.")


if __name__ == "__main__":
    main()





