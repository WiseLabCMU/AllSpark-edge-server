"""
Data models for the Agent Service.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List


@dataclass
class AnomalyRequest:
    """
    Represents an anomaly analysis request sent from the Edge Server to the
    AllSpark Agentic Framework.

    Attributes:
        clip_path: Absolute path to the anomaly video clip on disk.
        log_path: Absolute path to the associated log file (MQTT trace, etc.).
        anomaly_time: ISO-8601 timestamp string of when the anomaly occurred.
        clip_start_time: ISO-8601 timestamp string of when the clip started.
        clip_start_timestamp: Epoch-millisecond timestamp of clip start.
        error: Error message or label that triggered the anomaly.
        expected_topic: MQTT topic that was expected but missed.
        mqtt_clip_messages: MQTT messages captured around the anomaly window.
        video_storage_path: Root path under which video chunks live.
        extra_metadata: Any additional key/value pairs callers want forwarded.
    """

    clip_path: str
    log_path: str
    anomaly_time: str
    clip_start_time: str = ""
    clip_start_timestamp: str = ""
    error: str = "N/A"
    expected_topic: str = "N/A"
    mqtt_clip_messages: List[Dict[str, Any]] = field(default_factory=list)
    video_storage_path: str = ""
    extra_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnomalyRequest":
        known = {k for k in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


@dataclass
class AgentResponse:
    """
    Represents the result of an agent analysis, including both the raw API
    response and enriched storage metadata.

    Attributes:
        request_id: Unique identifier (typically the anomaly timestamp).
        clip_path: Path to the analysed video clip.
        anomaly_time: ISO-8601 string of the anomaly timestamp.
        session_id: Agent session identifier used for the request.
        status: "success" | "error"
        raw_response: The full JSON response body returned by the agent API.
        summary: Best-effort extracted text from the agent response.
        stored_at: Path where the JSON response file was saved on disk.
        created_at: ISO-8601 timestamp of when this response object was created.
        error_message: Human-readable error if status == "error".
    """

    request_id: str
    clip_path: str
    anomaly_time: str
    session_id: str
    status: str
    raw_response: Any = None
    summary: str = ""
    stored_at: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())
    error_message: str = ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentResponse":
        known = {k for k in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    @classmethod
    def from_json(cls, json_str: str) -> "AgentResponse":
        return cls.from_dict(json.loads(json_str))

    @property
    def is_success(self) -> bool:
        return self.status == "success"




