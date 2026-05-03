"""
AnomalyResponseStore

Persists AgentResponse objects to disk under a structured directory layout:

    <base_path>/
        <Anomaly_YYYY-MM-DD>/
            <HHMMSS_<uuid>>/
                response.json          – full AgentResponse as JSON
                summary.txt            – extracted text summary (human-readable)
                request.json           – original AnomalyRequest as JSON
                session_info.txt       – human-readable ADK session lookup info
                video_clips/           – video clip(s) associated with the anomaly
                machine_anomaly_data/  – machine/sensor anomaly data files

The module also provides helper methods to list and retrieve stored responses,
making it straightforward to display them in the NiceGUI dashboard.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import AgentResponse, AnomalyRequest

logger = logging.getLogger(__name__)

# Default ADK web URL used in session_info.txt when no config is supplied
_DEFAULT_ADK_URL = "http://localhost:8000"
_DEFAULT_APP_NAME = "allspark_agent"
_DEFAULT_USER_ID = "user"


class AnomalyResponseStore:
    """
    Thread-safe file-system store for agent anomaly responses.

    Args:
        base_path: Root directory under which all responses are stored.
                   Created automatically if it does not exist.
    """

    _RESPONSE_FILE = "response.json"
    _SUMMARY_FILE = "summary.txt"
    _REQUEST_FILE = "request.json"
    _SESSION_INFO_FILE = "session_info.txt"
    _VIDEO_CLIPS_DIR = "video_clips"
    _MACHINE_ANOMALY_DATA_DIR = "machine_anomaly_data"

    def __init__(self, base_path: str, anomaly_event_dirs: Optional[List[str]] = None) -> None:
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)
        # Extra roots (e.g. NFS anomaly event dirs) whose Anomaly_*/agent_responses/
        # sub-trees are also scanned by list_responses().
        self._anomaly_event_dirs: List[Path] = [
            Path(d) for d in (anomaly_event_dirs or []) if d
        ]
        logger.info("AnomalyResponseStore initialised at %s", self._base)
        if self._anomaly_event_dirs:
            logger.info("Also scanning anomaly event dirs: %s", self._anomaly_event_dirs)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(
        self,
        response: AgentResponse,
        request: Optional[AnomalyRequest] = None,
        agent_config: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Persist an AgentResponse (and optionally the originating request) to
        disk.  Returns the absolute path to the created subfolder.

        Storage location logic:
          • If *request.anomaly_folder* is set (e.g.
            ``uploads/anomaly_2026-04-02T20-49-04``), responses are written to
            ``<anomaly_folder>/agent_responses/<HHMMSS_uuid>/``.
          • Otherwise the legacy layout is used:
            ``<base_path>/Anomaly_YYYY-MM-DD/<HHMMSS_uuid>/``.

        Args:
            response:     The AgentResponse to persist.
            request:      The originating AnomalyRequest (optional).
            agent_config: The agentConfig dict from config.yaml, used to
                          populate session_info.txt with real URLs/names.
                          Falls back to sensible defaults if None.
        """
        # Build a timestamp-based subfolder from the anomaly time or now
        ts = _parse_ts_for_folder(response.anomaly_time)
        time_str = ts.strftime("%H%M%S")
        unique_id = response.request_id.split("_")[-1] if response.request_id else "x"
        subfolder_name = f"{time_str}_{unique_id}"

        # Decide the parent directory for this response
        anomaly_folder = (request.anomaly_folder if request else "") or ""
        if anomaly_folder:
            # Store inside the anomaly's own folder
            parent = Path(anomaly_folder) / "agent_responses"
        else:
            # Legacy: global agent_responses directory grouped by date
            date_str = ts.strftime("%Y-%m-%d")
            parent = self._base / f"Anomaly_{date_str}"

        target_dir = parent / subfolder_name
        target_dir.mkdir(parents=True, exist_ok=True)

        # Create standard subdirectories for media/data artefacts
        (target_dir / self._VIDEO_CLIPS_DIR).mkdir(exist_ok=True)
        (target_dir / self._MACHINE_ANOMALY_DATA_DIR).mkdir(exist_ok=True)

        # Stamp the stored_at field before writing
        response.stored_at = str(target_dir)

        # Write response.json
        _write_json(target_dir / self._RESPONSE_FILE, response.to_dict())

        # Write summary.txt
        if response.summary:
            (target_dir / self._SUMMARY_FILE).write_text(
                response.summary, encoding="utf-8"
            )

        # Write request.json if provided
        if request is not None:
            _write_json(target_dir / self._REQUEST_FILE, request.to_dict())

        # Write session_info.txt – human-readable ADK lookup helper
        self._write_session_info(target_dir, response, agent_config)

        logger.info("AgentResponse saved to %s", target_dir)
        return str(target_dir)

    def _write_session_info(
        self,
        target_dir: Path,
        response: AgentResponse,
        agent_config: Optional[Dict[str, Any]],
    ) -> None:
        """Write a human-readable session_info.txt for ADK lookup."""
        cfg = agent_config or {}

        # Derive the ADK web URL from the agent_url (strip /run suffix)
        raw_url: str = cfg.get("agent_url", _DEFAULT_ADK_URL)
        adk_web_url = re.sub(r"/run$", "", raw_url.rstrip("/"))

        app_name = cfg.get("agent_app_name", _DEFAULT_APP_NAME)
        user_id = cfg.get("agent_user_id", _DEFAULT_USER_ID)
        session_id = response.session_id or "N/A"
        created_at = response.created_at or "N/A"

        lines = [
            "=" * 60,
            "  AllSpark Agent – ADK Session Lookup",
            "=" * 60,
            f"  ADK Session ID : {session_id}",
            f"  App Name       : {app_name}",
            f"  User ID        : {user_id}",
            f"  Status         : {response.status}",
            f"  Created At     : {created_at}",
            "",
            "  To inspect this session in the ADK web UI:",
            f"    1. Open  {adk_web_url}",
            f"    2. Go to the 'Sessions' tab",
            f"    3. Search for session ID: {session_id}",
            "",
            "  Direct API lookup:",
            f"    GET {adk_web_url}/apps/{app_name}/users/{user_id}/sessions/{session_id}",
            "=" * 60,
        ]

        (target_dir / self._SESSION_INFO_FILE).write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_responses(
        self,
        limit: int = 50,
    ) -> List[AgentResponse]:
        """
        Return up to *limit* AgentResponse objects, newest first.

        Searches both the global ``<base_path>/`` tree **and** any
        ``uploads/anomaly_*/agent_responses/`` folders so that responses
        stored inside per-anomaly directories are also discovered.

        Args:
            limit: Maximum number of results to return.
        """
        results: List[AgentResponse] = []

        # Collect response.json files from all known locations
        response_files: List[Path] = []

        # 1. Global agent_responses directory (legacy location)
        if self._base.exists():
            response_files.extend(self._base.rglob(self._RESPONSE_FILE))

        # 2. Per-anomaly folders: uploads/anomaly_*/agent_responses/
        #    The uploads root is the parent of self._base
        #    (e.g. self._base = uploads/agent_responses → uploads_root = uploads)
        uploads_root = self._base.parent
        if uploads_root.exists():
            for anomaly_dir in uploads_root.glob("anomaly_*"):
                agent_resp_dir = anomaly_dir / "agent_responses"
                if agent_resp_dir.is_dir():
                    response_files.extend(agent_resp_dir.rglob(self._RESPONSE_FILE))

        # 3. Extra NFS / anomaly-event roots (e.g. /net/htvvm662/fs0/anomaly_events)
        #    Scan Anomaly_*/agent_responses/ under each configured root.
        for root in self._anomaly_event_dirs:
            if root.exists():
                for anomaly_dir in root.glob("Anomaly_*"):
                    agent_resp_dir = anomaly_dir / "agent_responses"
                    if agent_resp_dir.is_dir():
                        response_files.extend(agent_resp_dir.rglob(self._RESPONSE_FILE))

        # Deduplicate (in case base_path overlaps with anomaly folders)
        seen: set = set()
        unique_files: List[Path] = []
        for rf in response_files:
            resolved = rf.resolve()
            if resolved not in seen:
                seen.add(resolved)
                unique_files.append(rf)

        # Sort by file modification time (newest first)
        unique_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        for rf in unique_files[:limit]:
            try:
                data = json.loads(rf.read_text(encoding="utf-8"))
                results.append(AgentResponse.from_dict(data))
            except Exception as exc:
                logger.warning("Could not load response from %s: %s", rf, exc)

        return results

    def list_response_dicts(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Like :meth:`list_responses`, but returns plain dicts that have the
        ``anomaly_folder`` field merged in from the sibling ``request.json``
        (when present). Used by the HTTP API so the UI can know which
        anomaly folder a response belongs to without a second round-trip.
        """
        out: List[Dict[str, Any]] = []
        for resp in self.list_responses(limit=limit):
            d = resp.to_dict()
            stored_at = d.get("stored_at", "")
            d["anomaly_folder"] = self._anomaly_folder_for(stored_at)

            # Enrich with triage fields from sibling request.json (additive)
            triage = self._triage_fields_for(stored_at)
            for k, v in triage.items():
                # Don't overwrite anything the response itself already provides
                d.setdefault(k, v)
            out.append(d)
        return out

    def _triage_fields_for(self, stored_at: str) -> Dict[str, Any]:
        """
        Pull factory-floor triage fields from the sibling request.json and
        the on-disk video_clips/ directory.

        Returned keys (all optional):
          - error            : error label that triggered the anomaly
          - expected_topic   : MQTT/Kafka topic that was expected but missed
          - clip_path        : original clip path from the request
          - video_clip_url   : browser URL to play the first stored video clip
                               via the /anomaly-media static mount, or "" if
                               no clip is present.
        """
        out: Dict[str, Any] = {}
        if not stored_at:
            return out

        # request.json fields
        req_path = Path(stored_at) / self._REQUEST_FILE
        if req_path.exists():
            try:
                req_data = json.loads(req_path.read_text(encoding="utf-8"))
                for key in ("error", "expected_topic", "clip_path", "data_source"):
                    val = req_data.get(key)
                    if val:
                        out[key] = val
            except Exception:
                pass

        # First file in video_clips/, exposed via /anomaly-media static mount
        video_dir = Path(stored_at) / self._VIDEO_CLIPS_DIR
        if video_dir.is_dir():
            try:
                clips = sorted(
                    p for p in video_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in {".mp4", ".webm", ".mov", ".m4v"}
                )
                if clips:
                    # Build a URL relative to the uploads root (parent of self._base)
                    uploads_root = self._base.parent.resolve()
                    try:
                        rel = clips[0].resolve().relative_to(uploads_root)
                        out["video_clip_url"] = "/anomaly-media/" + str(rel).replace("\\", "/")
                    except ValueError:
                        # Stored outside the uploads tree – cannot serve
                        pass
            except Exception:
                pass

        return out

    def _anomaly_folder_for(self, stored_at: str) -> str:
        """
        Determine the anomaly folder a response belongs to.

        Resolution order:
          1. ``request.json``'s ``anomaly_folder`` field (authoritative).
          2. Walk up the path: if any ancestor is a sibling of an
             ``agent_responses/`` directory and matches ``anomaly_*``,
             use that.
          3. Empty string (legacy responses stored under the global
             ``uploads/agent_responses/Anomaly_YYYY-MM-DD/`` layout).
        """
        if not stored_at:
            return ""
        # 1. Check request.json
        req = Path(stored_at) / self._REQUEST_FILE
        if req.exists():
            try:
                data = json.loads(req.read_text(encoding="utf-8"))
                folder = data.get("anomaly_folder", "")
                if folder:
                    return folder
            except Exception:
                pass
        # 2. Walk up looking for an ``anomaly_*`` ancestor that contains
        #    an ``agent_responses`` directory (the per-anomaly layout).
        for parent in Path(stored_at).parents:
            if parent.name.startswith("anomaly_") and (parent / "agent_responses").is_dir():
                return str(parent)
        return ""

    def get_response(self, stored_at: str) -> Optional[AgentResponse]:
        """
        Load a single AgentResponse from its storage directory path.
        """
        target = Path(stored_at) / self._RESPONSE_FILE
        if not target.exists():
            return None
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            return AgentResponse.from_dict(data)
        except Exception as exc:
            logger.error("Failed to load response from %s: %s", target, exc)
            return None

    def get_request(self, stored_at: str) -> Optional[AnomalyRequest]:
        """
        Load the AnomalyRequest that was stored alongside a response.
        """
        target = Path(stored_at) / self._REQUEST_FILE
        if not target.exists():
            return None
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            return AnomalyRequest.from_dict(data)
        except Exception as exc:
            logger.error("Failed to load request from %s: %s", target, exc)
            return None

    def get_session_info(self, stored_at: str) -> Optional[str]:
        """
        Return the raw text content of session_info.txt, or None if missing.
        """
        target = Path(stored_at) / self._SESSION_INFO_FILE
        if not target.exists():
            return None
        return target.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def base_path(self) -> str:
        return str(self._base)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _parse_ts_for_folder(ts_str: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(ts_str[:19], fmt[:len(fmt.split("T")[0]) + 9])
        except ValueError:
            continue
    # Strip timezone suffix if present and retry
    clean = ts_str[:19].replace("T", " ")
    try:
        return datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return datetime.now(tz=timezone.utc)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")






