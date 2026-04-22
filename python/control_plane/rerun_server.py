"""
Rerun Server – AllSpark Edge Server
====================================

Starts a Rerun web viewer and loads anomaly videos + agent responses
from the edge server's response store. Modeled after the datacapture
``utilities/dashboard.py`` but simplified to work with stored data
rather than live MQTT streams.

Usage:
    python rerun_server.py                  # uses defaults from config.yaml
    python rerun_server.py --port 9090      # custom web-viewer port
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import argparse
from pathlib import Path
from typing import Optional

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


class RerunAnomalyViewer:
    """
    Initialises a Rerun recording, loads all stored anomaly responses
    (videos + agent summaries), and serves the web viewer.
    """

    def __init__(self, web_port: int = 9090, responses_path: Optional[str] = None):
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
        logger.info("Rerun viewer – responses root: %s", self._responses_root)

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

        rr.init("allspark_anomaly_viewer")

        # Start gRPC server + web viewer
        server_uri = rr.serve_grpc()
        logger.info("Rerun gRPC server at: %s", server_uri)
        rr.serve_web_viewer(
            connect_to=server_uri,
            open_browser=False,
            web_port=self._web_port,
        )
        logger.info("Rerun web viewer serving on port %d", self._web_port)

        # Set up the blueprint
        self._send_blueprint(rr, rrb)

        # Load all stored anomaly data
        self._load_all_responses(rr)

        logger.info("Rerun viewer ready – open http://127.0.0.1:%d", self._web_port)

    # ------------------------------------------------------------------
    # Blueprint
    # ------------------------------------------------------------------

    @staticmethod
    def _send_blueprint(rr, rrb) -> None:
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

    # ------------------------------------------------------------------
    # Data loading
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

        logger.info("Loaded %d / %d anomaly responses into Rerun", loaded, len(response_files))

    def _load_single_response(self, rr, response_file: Path) -> None:
        """Load one anomaly's video + summary into Rerun."""
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

        # Log the agent response as markdown
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

        # Try to load the video
        video_path = self._find_video(clip_path_raw, response_file.parent)
        if video_path and video_path.exists():
            self._log_video(rr, video_path, ts_epoch)
        else:
            logger.info("Video not found for %s (tried: %s)", clip_basename, video_path)

    def _find_video(self, clip_path_raw: str, response_dir: Path) -> Optional[Path]:
        """Resolve the video file path, checking multiple locations."""
        if not clip_path_raw:
            return None

        basename = os.path.basename(clip_path_raw)

        # 1. Check response's video_clips/ subfolder
        local = response_dir / "video_clips" / basename
        if local.exists():
            return local

        # 2. Check agent data folder
        if self._agent_data_folder:
            agent_path = self._agent_data_folder / basename
            if agent_path.exists():
                return agent_path

        # 3. Check if clip_path_raw is an absolute path
        abs_path = Path(clip_path_raw)
        if abs_path.is_absolute() and abs_path.exists():
            return abs_path

        # 4. Check the datacapture logs folder
        datacapture_root = _PYTHON_DIR.parent.parent / "allspark-datacapture"
        if datacapture_root.exists():
            for match in datacapture_root.rglob(basename):
                return match

        return None

    @staticmethod
    def _log_video(rr, video_path: Path, clip_start_epoch: float) -> None:
        """Log a video asset into Rerun with real-time timestamps."""
        try:
            video_asset = rr.AssetVideo(path=str(video_path))
            rr.log("video_anomalies", video_asset, static=True)

            frame_timestamps_ns = video_asset.read_frame_timestamps_nanos()
            clip_start_ns = np.datetime64(
                int(clip_start_epoch * 1_000) * 1_000_000, "ns"
            )
            rt_timestamps = frame_timestamps_ns + clip_start_ns

            rr.send_columns(
                "video_anomalies",
                indexes=[rr.TimeColumn("timeline", timestamp=rt_timestamps)],
                columns=rr.VideoFrameReference.columns_nanos(frame_timestamps_ns),
            )
            logger.info("Loaded video: %s (%d frames)", video_path.name, len(frame_timestamps_ns))
        except Exception as exc:
            logger.warning("Failed to load video %s: %s", video_path, exc)

    @staticmethod
    def _parse_epoch(ts_str: str) -> Optional[float]:
        """Parse an ISO-8601 timestamp to epoch seconds."""
        if not ts_str:
            return None
        from datetime import datetime
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(ts_str[:19], fmt[:len(ts_str[:19])])
                return dt.timestamp()
            except ValueError:
                continue
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
        description="Start the Rerun anomaly viewer with stored Edge Server responses."
    )
    parser.add_argument(
        "--port", type=int, default=9090,
        help="Web viewer port (default: 9090, must match control_plane config).",
    )
    parser.add_argument(
        "--responses-path", default=None,
        help="Override path to agent_responses folder.",
    )
    args = parser.parse_args()

    viewer = RerunAnomalyViewer(
        web_port=args.port,
        responses_path=args.responses_path,
    )
    viewer.start()

    print(f"\n  Rerun viewer running at http://127.0.0.1:{args.port}")
    print("  Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nRerun viewer stopped.")


if __name__ == "__main__":
    main()





