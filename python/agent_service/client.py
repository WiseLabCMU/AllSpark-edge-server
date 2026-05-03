"""
AgentApiClient

HTTP client that talks to the AllSpark Agentic Framework.

Design notes:
  - Mirrors the session lifecycle implemented in anomaly_agent_sender.py (create
    session → initialise → run), but is adapted for synchronous aiohttp usage
    within an async aiohttp/NiceGUI server.
  - Pure I/O class; no state is persisted here – see AnomalyResponseStore.
  - Thread-safe: a single asyncio ClientSession is created per call so this
    class can be safely instantiated once at startup and shared across handlers.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, Tuple

import aiohttp

from .models import AnomalyRequest, AgentResponse

logger = logging.getLogger(__name__)


class AgentApiClient:
    """
    Async HTTP client for the AllSpark Agentic Framework REST API.

    Configuration dict schema (matches allspark_agent_config.json):
    {
        "agent_url":        "http://host:port/run",
        "agent_app_name":   "allspark_agent",
        "agent_user_id":    "user",
        "agent_session_id": "edge_session_001",   # used as a base; unique id appended per request
        "agent_timeout":    900,
        "agent_init_message": "Hey, can you help me do some analysis?"
    }
    """

    # Session creation URL: POST /apps/{app}/users/{uid}/sessions
    _SESSION_PATH_TPL = "/apps/{app}/users/{uid}/sessions"
    # Per-ID endpoint for session lookup
    _SESSION_ID_PATH_TPL = "/apps/{app}/users/{uid}/sessions/{sid}"

    def __init__(self, config: Dict[str, Any]) -> None:
        self._agent_url: str = config.get("agent_url", "http://localhost:8000/run")
        self._app_name: str = config.get("agent_app_name", "allspark_agent")
        self._user_id: str = config.get("agent_user_id", "user")
        self._base_session_id: str = config.get("agent_session_id", "edge_session")
        self._timeout: int = int(config.get("agent_timeout", 900))
        self._init_message: str = config.get(
            "agent_init_message", "Hey, can you help me do some analysis?"
        )
        # Derive base URL (strip /run suffix if present)
        self._base_url: str = re.sub(r"/run$", "", self._agent_url.rstrip("/"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze_anomaly(self, request: AnomalyRequest) -> AgentResponse:
        """
        Create a dedicated session, initialise it, then send the anomaly
        analysis request.  Returns an AgentResponse populated with the result.
        """
        safe_ts = re.sub(r"[^a-zA-Z0-9_\-]", "_", request.anomaly_time)[:32]
        request_id = f"{safe_ts}_{uuid.uuid4().hex[:6]}"

        timeout = aiohttp.ClientTimeout(total=self._timeout)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 1. Create session – let ADK generate the session ID
            session_id, err = await self._create_session(session)
            if not session_id:
                return AgentResponse(
                    request_id=request_id,
                    clip_path=request.clip_path,
                    anomaly_time=request.anomaly_time,
                    session_id="",
                    status="error",
                    error_message=f"Session creation failed: {err}",
                )

            # 2. Initialise session with a greeting
            await self._initialise_session(session, session_id)

            # 3. Send the actual anomaly analysis prompt
            prompt = self._build_prompt(request)
            ok, raw, err = await self._send_message(session, session_id, prompt)

        if not ok:
            return AgentResponse(
                request_id=request_id,
                clip_path=request.clip_path,
                anomaly_time=request.anomaly_time,
                session_id=session_id,
                status="error",
                error_message=err,
            )

        summary = self._extract_summary(raw)
        return AgentResponse(
            request_id=request_id,
            clip_path=request.clip_path,
            anomaly_time=request.anomaly_time,
            session_id=session_id,
            status="success",
            raw_response=raw,
            summary=summary,
        )

    async def continue_session(
        self,
        session_id: str,
        prompt: str,
        clip_path: str,
        anomaly_time: str,
    ) -> AgentResponse:
        """
        Send a follow-up message to an *existing* ADK session (identified by
        *session_id*) without creating a new one.  This implements the
        "investigate further" flow from the dashboard's Investigate tab.

        Args:
            session_id:   The ADK session ID that was created during the
                          original analysis (stored in AgentResponse.session_id).
            prompt:       The follow-up question / instruction from the operator.
            clip_path:    Clip path carried forward for attribution only.
            anomaly_time: Original anomaly timestamp carried forward.

        Returns:
            AgentResponse with status "success" or "error".
        """
        request_id = f"followup_{re.sub(r'[^a-zA-Z0-9_]', '_', anomaly_time)[:20]}_{uuid.uuid4().hex[:6]}"
        timeout = aiohttp.ClientTimeout(total=self._timeout)

        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            # Verify the session still exists
            exists, err = await self._verify_session_exists(http_session, session_id)
            if not exists:
                return AgentResponse(
                    request_id=request_id,
                    clip_path=clip_path,
                    anomaly_time=anomaly_time,
                    session_id=session_id,
                    status="error",
                    error_message=f"Could not reach session {session_id}: {err}",
                )

            ok, raw, err = await self._send_message(http_session, session_id, prompt)

        if not ok:
            return AgentResponse(
                request_id=request_id,
                clip_path=clip_path,
                anomaly_time=anomaly_time,
                session_id=session_id,
                status="error",
                error_message=err,
            )

        summary = self._extract_summary(raw)
        return AgentResponse(
            request_id=request_id,
            clip_path=clip_path,
            anomaly_time=anomaly_time,
            session_id=session_id,
            status="success",
            raw_response=raw,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _create_session(
        self, session: aiohttp.ClientSession
    ) -> Tuple[str, str]:
        """Create a new ADK session, letting the server assign the ID.

        Returns:
            (session_id, error_message) – session_id is empty on failure.
        """
        url = (
            self._base_url
            + self._SESSION_PATH_TPL.format(
                app=self._app_name, uid=self._user_id
            )
        )
        try:
            async with session.post(
                url,
                headers={"Content-Type": "application/json"},
                json={},
            ) as resp:
                if resp.status in (200, 201):
                    data = await resp.json(content_type=None)
                    sid = data.get("id", "")
                    logger.info("Agent session created (ADK-assigned): %s", sid)
                    return sid, ""
                body = await resp.text()
                msg = f"HTTP {resp.status}: {body}"
                logger.error("Failed to create agent session: %s", msg)
                return "", msg
        except Exception as exc:
            logger.error("Exception creating agent session: %s", exc)
            return "", str(exc)

    async def _verify_session_exists(
        self, session: aiohttp.ClientSession, session_id: str
    ) -> Tuple[bool, str]:
        """Check that an existing session is reachable via GET."""
        url = (
            self._base_url
            + self._SESSION_ID_PATH_TPL.format(
                app=self._app_name, uid=self._user_id, sid=session_id
            )
        )
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return True, ""
                body = await resp.text()
                return False, f"HTTP {resp.status}: {body}"
        except Exception as exc:
            return False, str(exc)

    async def _initialise_session(
        self, session: aiohttp.ClientSession, session_id: str
    ) -> None:
        payload = self._build_payload(session_id, self._init_message)
        try:
            async with session.post(
                self._agent_url,
                headers={"Content-Type": "application/json"},
                json=payload,
            ) as resp:
                if resp.status == 200:
                    logger.info("Agent session initialised: %s", session_id)
                else:
                    logger.warning(
                        "Session init returned HTTP %s for session %s",
                        resp.status,
                        session_id,
                    )
        except Exception as exc:
            logger.warning("Exception initialising agent session: %s", exc)

    async def _send_message(
        self,
        session: aiohttp.ClientSession,
        session_id: str,
        message: str,
    ) -> Tuple[bool, Any, str]:
        payload = self._build_payload(session_id, message)
        try:
            async with session.post(
                self._agent_url,
                headers={"Content-Type": "application/json"},
                json=payload,
            ) as resp:
                resp.raise_for_status()
                raw = await resp.json(content_type=None)
                logger.info(
                    "Agent responded with HTTP %s for session %s",
                    resp.status,
                    session_id,
                )
                return True, raw, ""
        except aiohttp.ClientResponseError as exc:
            msg = f"HTTP error {exc.status}: {exc.message}"
            logger.error("Agent API error: %s", msg)
            return False, None, msg
        except Exception as exc:
            logger.error("Exception calling agent API: %s", exc)
            return False, None, str(exc)

    def _build_payload(self, session_id: str, message: str) -> Dict[str, Any]:
        """Payload for POST /run (ADK REST API — snake_case keys)."""
        return {
            "app_name": self._app_name,
            "user_id": self._user_id,
            "session_id": session_id,
            "new_message": {"role": "user", "parts": [{"text": message}]},
        }

    @staticmethod
    def _build_prompt(request: AnomalyRequest) -> str:
        """
        Build the natural-language prompt sent to the orchestrator agent.

        The prompt is cell-agnostic: it infers the system context from the video
        filename (e.g., ``ch5_*`` → Hatvan PM6 degating, ``anomaly_clip_*`` → CESAR)
        and adapts the data-source label based on ``request.data_source``.

        CRITICAL – how analyze_video_frames / describe_video_scene resolve the clip
        -----------------------------------------------------------------------------
        Both video tools call VideoDataLoader.get_data(), which scans only the
        configured data folder (allspark_agent/sample_data/<profile>/camera-video/).
        It returns a list of FULL ABSOLUTE paths, e.g.:
            /Users/.../camera-video/anomaly_clip_20250917_143650.mp4

        It then matches using:   video_file_path.endswith(user_video_file)

        Therefore user_video_file MUST be the plain filename only — no directory
        component — so that endswith() matches the absolute path in the data folder.

        The clip_path in the request is expected to be just the filename after
        AnomalySubmitter has copied the clip into the data folder.
        """
        import os
        # clip_path is the basename only (enforced by submit_anomaly_to_edge.py)
        clip_basename = os.path.basename(request.clip_path)

        # --- Infer system context from filename ---
        _HATVAN_PREFIXES = ("ch0_", "ch1_", "ch2_", "ch3_", "ch4_", "ch5_", "ch6_")
        lower_name = clip_basename.lower()
        if any(lower_name.startswith(p) for p in _HATVAN_PREFIXES) or "htvp" in lower_name:
            system_label = "the PM6 production line at the Hatvan plant"
        else:
            system_label = "the CESAR cell bolt screw and washer assembly operation"

        # --- Data-source label ---
        data_source = getattr(request, "data_source", "mqtt") or "mqtt"
        if data_source.lower() == "kafka":
            messages_label = "Kafka anomaly messages"
            messages_section_title = "Kafka Messages captured around the anomaly"
        else:
            messages_label = "MQTT messages"
            messages_section_title = "MQTT Messages captured around the anomaly"

        messages_str = json.dumps(request.mqtt_clip_messages, indent=2)

        return (
            f"An anomaly was detected from monitoring {messages_label} on {system_label}.\n"
            f"A video clip of the system operation was recorded during this anomaly.\n\n"
            f"## Anomaly Details\n"
            f"- Anomaly Time        : {request.anomaly_time}\n"
            f"- Clip Start Time     : {request.clip_start_time}\n"
            f"- Clip Start Timestamp: {request.clip_start_timestamp}\n"
            f"- Error Detected      : {request.error}\n"
            f"- Expected Topic      : {request.expected_topic}\n"
            f"- Log Path            : {request.log_path}\n\n"
            f"## {messages_section_title}\n"
            f"{messages_str}\n\n"
            f"## Task\n"
            f"Call `analyze_video_frames` with `user_video_file=\"{clip_basename}\"`.\n"
            f"That is the exact filename to pass — do not modify it, do not prepend "
            f"any directory path. The tool locates the file by matching the filename "
            f"against its configured video data folder.\n\n"
            f"After analysis:\n"
            f"1. Describe what is happening in the video during the anomaly period.\n"
            f"2. Cross-reference the {messages_label} to understand the operational context.\n"
            f"3. Provide insights on what likely caused the anomaly.\n"
        )

    @staticmethod
    def _extract_summary(raw: Any) -> str:
        """
        Best-effort extraction of a readable summary from the agent response.
        The agentic framework may return different response shapes; we try the
        most common patterns and fall back to a serialised excerpt.
        """
        if raw is None:
            return ""

        # Pattern 1: list of events/messages
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    # ADK streaming format
                    content = item.get("content", {})
                    if isinstance(content, dict):
                        for part in content.get("parts", []):
                            if isinstance(part, dict) and "text" in part:
                                return part["text"]
                    # Simple text field
                    if "text" in item:
                        return item["text"]

        # Pattern 2: dict with candidates/messages
        if isinstance(raw, dict):
            for key in ("response", "text", "output", "message", "answer"):
                if key in raw and isinstance(raw[key], str):
                    return raw[key]
            # Nested candidates
            candidates = raw.get("candidates", [])
            if candidates:
                try:
                    return candidates[0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError, TypeError):
                    pass

        return json.dumps(raw)[:500]

