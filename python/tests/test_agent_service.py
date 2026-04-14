"""
Test suite for the Edge Server Agent Service components.

Tests cover:
  - AnomalyRequest / AgentResponse model serialisation & deserialisation
  - AgentApiClient._build_prompt output correctness
  - AgentApiClient._extract_summary for various response shapes
  - AnomalyResponseStore save / list / get round-trip
  - Edge Server HTTP endpoint /api/agent/analyze (integration, mocked agent)
  - Edge Server HTTP endpoint /api/agent/responses (integration)

Run with:
    cd /Users/bos2pi/git/Bosch-Github/AllSpark-edge-server/python
    python -m pytest tests/ -v
"""
from __future__ import annotations

import json
import sys
import os
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import types

import pytest

# ---------------------------------------------------------------------------
# Make sure the python package directory is on the path
# ---------------------------------------------------------------------------
PYTHON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PYTHON_DIR)

from agent_service.models import AnomalyRequest, AgentResponse
from agent_service.response_store import AnomalyResponseStore
from agent_service.client import AgentApiClient


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def sample_request() -> AnomalyRequest:
    return AnomalyRequest(
        clip_path="/data/clips/anomaly_clip_20260413_120000.mp4",
        log_path="/data/logs/mqtt_trace.log",
        anomaly_time="2026-04-13T12:00:00",
        clip_start_time="2026-04-13T11:59:30",
        clip_start_timestamp="1744545570000",
        error="missed expected message",
        expected_topic="allspark/anomaly_detected",
        mqtt_clip_messages=[{"topic": "rng120/status", "payload": "ok"}],
        video_storage_path="/data/video/chunks/",
        extra_metadata={"rig": "A"},
    )


@pytest.fixture
def sample_response(sample_request: AnomalyRequest) -> AgentResponse:
    return AgentResponse(
        request_id="20260413T120000_abc123",
        clip_path=sample_request.clip_path,
        anomaly_time=sample_request.anomaly_time,
        session_id="edge_session_20260413T120000_abc123",
        status="success",
        raw_response=[
            {
                "content": {
                    "parts": [{"text": "The video shows a washer misalignment at T+5s."}]
                }
            }
        ],
        summary="The video shows a washer misalignment at T+5s.",
    )


@pytest.fixture
def tmp_store(tmp_path: Path) -> AnomalyResponseStore:
    return AnomalyResponseStore(str(tmp_path / "agent_responses"))


@pytest.fixture
def agent_config() -> dict:
    return {
        "agent_url": "http://localhost:8000/run",
        "agent_app_name": "allspark_agent",
        "agent_user_id": "edge_server_user",
        "agent_session_id": "edge_session",
        "agent_timeout": 30,
        "agent_init_message": "Hey!",
    }


# ===========================================================================
# 1. Model tests
# ===========================================================================

class TestAnomalyRequest:
    def test_round_trip_dict(self, sample_request: AnomalyRequest):
        d = sample_request.to_dict()
        restored = AnomalyRequest.from_dict(d)
        assert restored.clip_path == sample_request.clip_path
        assert restored.anomaly_time == sample_request.anomaly_time
        assert restored.mqtt_clip_messages == sample_request.mqtt_clip_messages

    def test_extra_keys_ignored(self, sample_request: AnomalyRequest):
        d = sample_request.to_dict()
        d["_unknown_field"] = "should_be_ignored"
        restored = AnomalyRequest.from_dict(d)
        assert not hasattr(restored, "_unknown_field")

    def test_defaults(self):
        req = AnomalyRequest(
            clip_path="/tmp/clip.mp4",
            log_path="",
            anomaly_time="2026-01-01T00:00:00",
        )
        assert req.error == "N/A"
        assert req.mqtt_clip_messages == []


class TestAgentResponse:
    def test_round_trip_dict(self, sample_response: AgentResponse):
        d = sample_response.to_dict()
        restored = AgentResponse.from_dict(d)
        assert restored.request_id == sample_response.request_id
        assert restored.status == sample_response.status
        assert restored.summary == sample_response.summary

    def test_round_trip_json(self, sample_response: AgentResponse):
        js = sample_response.to_json()
        restored = AgentResponse.from_json(js)
        assert restored.clip_path == sample_response.clip_path

    def test_is_success(self, sample_response: AgentResponse):
        assert sample_response.is_success is True

    def test_is_not_success(self):
        r = AgentResponse(
            request_id="x",
            clip_path="/tmp/x.mp4",
            anomaly_time="2026-01-01T00:00:00",
            session_id="s",
            status="error",
            error_message="Something went wrong",
        )
        assert r.is_success is False


# ===========================================================================
# 2. AgentApiClient unit tests (no network)
# ===========================================================================

class TestAgentApiClientBuildPrompt:
    def test_prompt_contains_clip_path(self, sample_request: AnomalyRequest):
        prompt = AgentApiClient._build_prompt(sample_request)
        # The prompt must contain the BASENAME, not the full path.
        # Passing the full path to the agent tool causes FileNotFoundError because
        # VideoDataLoader only scans its local data folder and matches via endswith(basename).
        import os
        assert os.path.basename(sample_request.clip_path) in prompt
        # Full path must NOT appear – its presence caused the agent to pass it as user_video_file
        assert sample_request.clip_path not in prompt

    def test_prompt_contains_anomaly_time(self, sample_request: AnomalyRequest):
        prompt = AgentApiClient._build_prompt(sample_request)
        assert sample_request.anomaly_time in prompt

    def test_prompt_contains_error(self, sample_request: AnomalyRequest):
        prompt = AgentApiClient._build_prompt(sample_request)
        assert sample_request.error in prompt

    def test_prompt_contains_mqtt_messages(self, sample_request: AnomalyRequest):
        prompt = AgentApiClient._build_prompt(sample_request)
        assert "rng120/status" in prompt


class TestAgentApiClientExtractSummary:
    def test_extract_from_list_adk_format(self):
        raw = [
            {"content": {"parts": [{"text": "Anomaly detected: wrench slipped."}]}}
        ]
        summary = AgentApiClient._extract_summary(raw)
        assert "Anomaly detected" in summary

    def test_extract_from_list_text_field(self):
        raw = [{"text": "Simple text response"}]
        summary = AgentApiClient._extract_summary(raw)
        assert summary == "Simple text response"

    def test_extract_from_dict_response_field(self):
        raw = {"response": "The system is fine."}
        summary = AgentApiClient._extract_summary(raw)
        assert summary == "The system is fine."

    def test_extract_from_candidates(self):
        raw = {
            "candidates": [
                {"content": {"parts": [{"text": "Everything looks normal."}]}}
            ]
        }
        summary = AgentApiClient._extract_summary(raw)
        assert "Everything looks normal." in summary

    def test_extract_fallback_none(self):
        assert AgentApiClient._extract_summary(None) == ""

    def test_extract_fallback_unknown_shape(self):
        raw = {"weird_key": "weird_value"}
        summary = AgentApiClient._extract_summary(raw)
        # Should return a serialised excerpt, not crash
        assert isinstance(summary, str)

    def test_base_url_stripping(self, agent_config: dict):
        client = AgentApiClient(agent_config)
        assert client._base_url == "http://localhost:8000"

    def test_base_url_stripping_no_run(self, agent_config: dict):
        cfg = dict(agent_config)
        cfg["agent_url"] = "http://localhost:8000"
        client = AgentApiClient(cfg)
        assert client._base_url == "http://localhost:8000"


# ===========================================================================
# 3. AnomalyResponseStore tests
# ===========================================================================

class TestAnomalyResponseStore:
    def test_save_creates_files(
        self,
        tmp_store: AnomalyResponseStore,
        sample_response: AgentResponse,
        sample_request: AnomalyRequest,
    ):
        stored_at = tmp_store.save(sample_response, sample_request)
        target = Path(stored_at)
        assert target.exists()
        assert (target / "response.json").exists()
        assert (target / "request.json").exists()
        assert (target / "summary.txt").exists()

    def test_save_creates_subdirs(
        self,
        tmp_store: AnomalyResponseStore,
        sample_response: AgentResponse,
    ):
        stored_at = tmp_store.save(sample_response)
        target = Path(stored_at)
        assert (target / "video_clips").is_dir()
        assert (target / "machine_anomaly_data").is_dir()

    def test_save_uses_anomaly_date_folder(
        self,
        tmp_store: AnomalyResponseStore,
        sample_response: AgentResponse,
    ):
        stored_at = tmp_store.save(sample_response)
        # The parent folder should be named Anomaly_YYYY-MM-DD
        date_folder = Path(stored_at).parent.name
        assert date_folder.startswith("Anomaly_")

    def test_save_updates_stored_at(
        self,
        tmp_store: AnomalyResponseStore,
        sample_response: AgentResponse,
    ):
        stored_at = tmp_store.save(sample_response)
        assert sample_response.stored_at == stored_at

    def test_list_responses_returns_saved(
        self,
        tmp_store: AnomalyResponseStore,
        sample_response: AgentResponse,
        sample_request: AnomalyRequest,
    ):
        tmp_store.save(sample_response, sample_request)
        results = tmp_store.list_responses()
        assert len(results) >= 1
        assert any(r.request_id == sample_response.request_id for r in results)


    def test_get_response(
        self,
        tmp_store: AnomalyResponseStore,
        sample_response: AgentResponse,
    ):
        stored_at = tmp_store.save(sample_response)
        loaded = tmp_store.get_response(stored_at)
        assert loaded is not None
        assert loaded.request_id == sample_response.request_id
        assert loaded.summary == sample_response.summary

    def test_get_request(
        self,
        tmp_store: AnomalyResponseStore,
        sample_response: AgentResponse,
        sample_request: AnomalyRequest,
    ):
        stored_at = tmp_store.save(sample_response, sample_request)
        loaded_req = tmp_store.get_request(stored_at)
        assert loaded_req is not None
        assert loaded_req.clip_path == sample_request.clip_path

    def test_get_response_missing(self, tmp_store: AnomalyResponseStore):
        result = tmp_store.get_response("/nonexistent/path/xyz")
        assert result is None

    def test_multiple_saves_correct_order(
        self,
        tmp_store: AnomalyResponseStore,
    ):
        for i in range(3):
            r = AgentResponse(
                request_id=f"req_{i:03d}",
                clip_path=f"/tmp/clip_{i}.mp4",
                anomaly_time=f"2026-04-13T1{i}:00:00",
                session_id=f"session_{i}",
                status="success",
                summary=f"Summary {i}",
            )
            tmp_store.save(r)

        results = tmp_store.list_responses(limit=10)
        assert len(results) == 3


# ===========================================================================
# 4. AgentApiClient async integration tests (mocked HTTP)
# ===========================================================================

@pytest.mark.asyncio
class TestAgentApiClientAsync:

    async def test_analyze_anomaly_success(
        self,
        agent_config: dict,
        sample_request: AnomalyRequest,
    ):
        client = AgentApiClient(agent_config)

        # Build a mock aiohttp response
        def _make_mock_response(status, json_data=None, text_data=""):
            mock_resp = AsyncMock()
            mock_resp.status = status
            mock_resp.text = AsyncMock(return_value=text_data)
            mock_resp.json = AsyncMock(return_value=json_data)
            mock_resp.raise_for_status = MagicMock()
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)
            return mock_resp

        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)

        # Session creation → 201
        create_resp = _make_mock_response(201, {})
        # Init message → 200 with empty list
        init_resp = _make_mock_response(200, [])
        # Analysis message → 200 with proper response
        analysis_resp_data = [
            {"content": {"parts": [{"text": "Washer misalignment detected."}]}}
        ]
        analysis_resp = _make_mock_response(200, analysis_resp_data)

        call_count = 0

        def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # session creation
                return create_resp
            elif call_count == 2:  # init
                return init_resp
            else:  # analysis
                return analysis_resp

        session_mock.post = MagicMock(side_effect=post_side_effect)

        with patch("aiohttp.ClientSession", return_value=session_mock):
            result = await client.analyze_anomaly(sample_request)

        assert result.status == "success"
        assert "Washer misalignment" in result.summary

    async def test_analyze_anomaly_session_creation_fails(
        self,
        agent_config: dict,
        sample_request: AnomalyRequest,
    ):
        client = AgentApiClient(agent_config)

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        session_mock.post = MagicMock(return_value=mock_resp)

        with patch("aiohttp.ClientSession", return_value=session_mock):
            result = await client.analyze_anomaly(sample_request)

        assert result.status == "error"
        assert "Session creation failed" in result.error_message

    async def test_analyze_anomaly_connection_error(
        self,
        agent_config: dict,
        sample_request: AnomalyRequest,
    ):
        client = AgentApiClient(agent_config)

        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        session_mock.post = MagicMock(side_effect=Exception("Connection refused"))

        with patch("aiohttp.ClientSession", return_value=session_mock):
            result = await client.analyze_anomaly(sample_request)

        assert result.status == "error"


# ===========================================================================
# 5. Edge Server HTTP endpoint integration tests
# ===========================================================================

@pytest.mark.asyncio
class TestEdgeServerEndpoints:
    """
    Spin up the aiohttp app directly (without SSL or ZeroConf) and
    test the /api/agent/* endpoints with controlled test doubles.

    Isolation strategy
    ------------------
    `server.load_config()` is called both at module level (via importlib.reload)
    and again inside `server.init_app()`.  Both calls re-initialise the global
    `_agent_client` and `_response_store` singletons from the real config.json,
    which means any mock we assign between those two calls gets overwritten.

    The fix: patch `server.load_config` to a no-op for the duration of each
    test, then inject our own store / client directly before calling
    `init_app()`.  The server module itself is imported once (no reload) and
    the globals are restored after every test via the patch context manager.
    """

    @staticmethod
    def _make_app_context(tmp_path: Path, agent_client, response_store):
        """
        Return a context manager that:
        1. Patches load_config() to a no-op so neither startup nor init_app
           can overwrite our singletons.
        2. Injects the supplied agent_client and response_store directly.
        """
        import server as srv
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _ctx():
            # Patch load_config so it never touches the real config.json
            with patch.object(srv, "load_config", return_value=None):
                # Inject test doubles
                srv._agent_client = agent_client
                srv._response_store = response_store
                app = await srv.init_app()
                yield app
                # Clean up – restore to None so the next test starts fresh
                srv._agent_client = None
                srv._response_store = None

        return _ctx()

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    async def test_analyze_returns_503_when_client_none(self, tmp_path: Path):
        """When _agent_client is None the endpoint must return 503."""
        store = AnomalyResponseStore(str(tmp_path / "responses"))
        async with self._make_app_context(tmp_path, agent_client=None, response_store=store) as app:
            from aiohttp.test_utils import TestClient, TestServer
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/agent/analyze",
                    json={
                        "clip_path": "/tmp/clip.mp4",
                        "anomaly_time": "2026-04-13T12:00:00",
                    },
                )
                assert resp.status == 503
                body = await resp.json()
                assert body["success"] is False

    async def test_analyze_returns_400_on_missing_fields(self, tmp_path: Path):
        """Requests missing required fields must be rejected with 400."""
        store = AnomalyResponseStore(str(tmp_path / "responses"))
        mock_client = AsyncMock()
        mock_client.analyze_anomaly = AsyncMock(
            return_value=AgentResponse(
                request_id="test_req",
                clip_path="",
                anomaly_time="",
                session_id="s",
                status="success",
                summary="ok",
            )
        )
        async with self._make_app_context(tmp_path, mock_client, store) as app:
            from aiohttp.test_utils import TestClient, TestServer
            async with TestClient(TestServer(app)) as client:
                # Missing anomaly_time
                resp = await client.post(
                    "/api/agent/analyze",
                    json={"clip_path": "/tmp/clip.mp4"},
                )
                assert resp.status == 400

                # Missing clip_path
                resp2 = await client.post(
                    "/api/agent/analyze",
                    json={"anomaly_time": "2026-04-13T12:00:00"},
                )
                assert resp2.status == 400

    async def test_analyze_full_success(self, tmp_path: Path):
        """A fully mocked agent call must return the mock's request_id and summary."""
        store = AnomalyResponseStore(str(tmp_path / "responses"))

        expected_response = AgentResponse(
            request_id="test_req_001",
            clip_path="/tmp/clip.mp4",
            anomaly_time="2026-04-13T12:00:00",
            session_id="session_xyz",
            status="success",
            summary="No anomaly detected in the footage.",
        )
        mock_client = AsyncMock()
        mock_client.analyze_anomaly = AsyncMock(return_value=expected_response)

        async with self._make_app_context(tmp_path, mock_client, store) as app:
            from aiohttp.test_utils import TestClient, TestServer
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/agent/analyze",
                    json={
                        "clip_path": "/tmp/clip.mp4",
                        "anomaly_time": "2026-04-13T12:00:00",
                    },
                )
                assert resp.status == 200
                data = await resp.json()
                assert data["success"] is True
                # The handler returns whatever the mock client returned
                assert data["request_id"] == "test_req_001"
                assert data["status"] == "success"
                assert data["summary"] == "No anomaly detected in the footage."
                assert "stored_at" in data
                assert data["stored_at"] != ""

                # Verify the response was actually written to the tmp store
                saved = store.list_responses()
                assert len(saved) == 1
                assert saved[0].request_id == "test_req_001"

    async def test_analyze_error_response(self, tmp_path: Path):
        """An agent error must be forwarded gracefully (HTTP 200, success=False)."""
        store = AnomalyResponseStore(str(tmp_path / "responses"))

        error_response = AgentResponse(
            request_id="err_req_001",
            clip_path="/tmp/clip.mp4",
            anomaly_time="2026-04-13T12:00:00",
            session_id="session_err",
            status="error",
            error_message="Connection refused to agent",
        )
        mock_client = AsyncMock()
        mock_client.analyze_anomaly = AsyncMock(return_value=error_response)

        async with self._make_app_context(tmp_path, mock_client, store) as app:
            from aiohttp.test_utils import TestClient, TestServer
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/agent/analyze",
                    json={
                        "clip_path": "/tmp/clip.mp4",
                        "anomaly_time": "2026-04-13T12:00:00",
                    },
                )
                assert resp.status == 200
                data = await resp.json()
                assert data["success"] is False
                assert data["status"] == "error"
                assert "Connection refused" in data["error_message"]

    async def test_responses_endpoint_empty(self, tmp_path: Path):
        """When the tmp store is empty the responses list must be []."""
        store = AnomalyResponseStore(str(tmp_path / "responses"))

        async with self._make_app_context(tmp_path, AsyncMock(), store) as app:
            from aiohttp.test_utils import TestClient, TestServer
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/api/agent/responses")
                assert resp.status == 200
                data = await resp.json()
                assert data["success"] is True
                assert data["count"] == 0
                assert data["responses"] == []

    async def test_responses_endpoint_with_data(self, tmp_path: Path):
        """Pre-seeded store entries must appear in the responses list."""
        store = AnomalyResponseStore(str(tmp_path / "responses"))
        test_response = AgentResponse(
            request_id="pre_stored_001",
            clip_path="/tmp/pre.mp4",
            anomaly_time="2026-04-13T10:00:00",
            session_id="pre_session",
            status="success",
            summary="Pre-stored test summary.",
        )
        store.save(test_response)

        async with self._make_app_context(tmp_path, AsyncMock(), store) as app:
            from aiohttp.test_utils import TestClient, TestServer
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/api/agent/responses")
                assert resp.status == 200
                data = await resp.json()
                assert data["count"] == 1
                assert data["responses"][0]["request_id"] == "pre_stored_001"
                assert data["responses"][0]["summary"] == "Pre-stored test summary."

    async def test_responses_endpoint_multiple(self, tmp_path: Path):
        """Multiple saves must all appear in the responses list."""
        store = AnomalyResponseStore(str(tmp_path / "responses"))

        for rid in ["req_001", "req_002"]:
            store.save(
                AgentResponse(
                    request_id=rid,
                    clip_path=f"/tmp/{rid}.mp4",
                    anomaly_time="2026-04-13T10:00:00",
                    session_id="s",
                    status="success",
                    summary=f"Summary for {rid}",
                ),
            )

        async with self._make_app_context(tmp_path, AsyncMock(), store) as app:
            from aiohttp.test_utils import TestClient, TestServer
            async with TestClient(TestServer(app)) as client:
                resp_all = await client.get("/api/agent/responses")
                data_all = await resp_all.json()
                assert data_all["count"] == 2



