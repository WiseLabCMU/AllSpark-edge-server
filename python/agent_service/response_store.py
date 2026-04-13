"""
AnomalyResponseStore

Persists AgentResponse objects to disk under a structured directory layout:

    <base_path>/
        <device_name>/
            <YYYY-MM-DD>/
                <HHMMSS_<uuid>>/
                    response.json      – full AgentResponse as JSON
                    summary.txt        – extracted text summary (human-readable)
                    request.json       – original AnomalyRequest as JSON
                    session_info.txt   – human-readable ADK session lookup info

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
_DEFAULT_USER_ID = "edge_server_user"


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

    def __init__(self, base_path: str) -> None:
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)
        logger.info("AnomalyResponseStore initialised at %s", self._base)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(
        self,
        response: AgentResponse,
        request: Optional[AnomalyRequest] = None,
        device_name: str = "default",
        agent_config: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Persist an AgentResponse (and optionally the originating request) to
        disk.  Returns the absolute path to the created subfolder.

        Args:
            response:     The AgentResponse to persist.
            request:      The originating AnomalyRequest (optional).
            device_name:  Organises responses by device under the base path.
            agent_config: The agentConfig dict from config.json, used to
                          populate session_info.txt with real URLs/names.
                          Falls back to sensible defaults if None.
        """
        # Build a timestamp-based subfolder from the anomaly time or now
        ts = _parse_ts_for_folder(response.anomaly_time)
        date_str = ts.strftime("%Y-%m-%d")
        time_str = ts.strftime("%H%M%S")
        unique_id = response.request_id.split("_")[-1] if response.request_id else "x"
        subfolder_name = f"{time_str}_{unique_id}"

        safe_device = _sanitise(device_name)
        target_dir = self._base / safe_device / date_str / subfolder_name
        target_dir.mkdir(parents=True, exist_ok=True)

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
        device_name: Optional[str] = None,
        limit: int = 50,
    ) -> List[AgentResponse]:
        """
        Return up to *limit* AgentResponse objects, newest first.

        Args:
            device_name: If given, restrict to that device; otherwise all devices.
            limit: Maximum number of results to return.
        """
        results: List[AgentResponse] = []

        search_root = self._base / _sanitise(device_name) if device_name else self._base

        if not search_root.exists():
            return results

        # Walk the folder structure collecting response.json files
        response_files = sorted(
            search_root.rglob(self._RESPONSE_FILE), reverse=True
        )

        for rf in response_files[:limit]:
            try:
                data = json.loads(rf.read_text(encoding="utf-8"))
                results.append(AgentResponse.from_dict(data))
            except Exception as exc:
                logger.warning("Could not load response from %s: %s", rf, exc)

        return results

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

def _sanitise(name: Optional[str]) -> str:
    if not name:
        return "default"
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name)


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






