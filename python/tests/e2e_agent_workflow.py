#!/usr/bin/env python3
"""
E2E smoke-test: Agent Analyze Endpoint

Sends a POST to a *running* Edge Server and verifies the full flow:
  1. /api/agent/analyze returns 200 with success or a graceful error.
  2. /api/agent/responses returns the stored result immediately after.

Usage (with Edge Server running on localhost:8080):
    cd /Users/bos2pi/git/Bosch-Github/AllSpark-edge-server/python
    python tests/e2e_agent_workflow.py [--port 8080] [--clip-path /some/clip.mp4]

The script does NOT require the Agentic Framework to be reachable: an error
response from the agent is still treated as a "graceful failure" in the test.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime

BASE_URL = "http://127.0.0.1:{port}"


def _post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=400) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        print(f"  HTTP {exc.code}: {body}")
        raise
    except Exception as exc:
        raise RuntimeError(f"POST {url} failed: {exc}") from exc


def _get(url: str) -> dict:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        raise RuntimeError(f"GET {url} failed: {exc}") from exc


def run_e2e(port: int, clip_path: str, anomaly_time: str) -> bool:
    base = BASE_URL.format(port=port)
    all_passed = True

    # ------------------------------------------------------------------
    # 1. Health check
    # ------------------------------------------------------------------
    print("\n[1/4] Health check …")
    try:
        health = _get(f"{base}/api/health")
        assert health.get("status") == "ok", f"Unexpected health: {health}"
        print("  ✅ Edge server is healthy.")
    except Exception as exc:
        print(f"  ❌ Health check failed: {exc}")
        print("  Make sure the Edge Server is running before running e2e tests.")
        return False

    # ------------------------------------------------------------------
    # 2. Trigger agent analysis
    # ------------------------------------------------------------------
    print("\n[2/4] Triggering /api/agent/analyze …")
    payload = {
        "clip_path": clip_path,
        "log_path": "",
        "anomaly_time": anomaly_time,
        "clip_start_time": "",
        "error": "e2e-test-error",
        "expected_topic": "allspark/anomaly_detected",
        "device_name": "e2e_test_device",
        "extra_metadata": {"test_run": True},
    }
    try:
        result = _post(f"{base}/api/agent/analyze", payload)
        print(f"  Response: {json.dumps(result, indent=4)}")

        # Even if the agent framework is offline the endpoint should respond
        # gracefully (success=False, but HTTP 200)
        assert "success" in result, "Response missing 'success' key"
        assert "status" in result, "Response missing 'status' key"

        if result.get("success"):
            print("  ✅ Analysis completed successfully.")
            stored_at = result.get("stored_at", "")
        else:
            err = result.get("error_message", "")
            print(f"  ⚠️  Analysis returned error (may be expected if agent offline): {err}")
            stored_at = result.get("stored_at", "")

    except Exception as exc:
        print(f"  ❌ /api/agent/analyze request failed: {exc}")
        all_passed = False
        stored_at = ""

    # ------------------------------------------------------------------
    # 3. Retrieve response list
    # ------------------------------------------------------------------
    print("\n[3/4] Fetching /api/agent/responses …")
    try:
        responses_data = _get(f"{base}/api/agent/responses?device_name=e2e_test_device&limit=5")
        assert responses_data.get("success") is True
        count = responses_data.get("count", 0)
        print(f"  ✅ Found {count} stored response(s) for e2e_test_device.")
        if count > 0:
            first = responses_data["responses"][0]
            print(f"  Latest request_id: {first.get('request_id')}")
            print(f"  Status: {first.get('status')}")
    except Exception as exc:
        print(f"  ❌ /api/agent/responses failed: {exc}")
        all_passed = False

    # ------------------------------------------------------------------
    # 4. Retrieve all responses (no filter)
    # ------------------------------------------------------------------
    print("\n[4/4] Fetching /api/agent/responses (no filter) …")
    try:
        all_data = _get(f"{base}/api/agent/responses")
        assert all_data.get("success") is True
        print(f"  ✅ Total stored responses: {all_data.get('count', 0)}")
    except Exception as exc:
        print(f"  ❌ /api/agent/responses (no filter) failed: {exc}")
        all_passed = False

    return all_passed


def main():
    parser = argparse.ArgumentParser(description="E2E test for the Edge Server agent workflow")
    parser.add_argument("--port", type=int, default=8080, help="Edge Server port")
    parser.add_argument(
        "--clip-path",
        default="/tmp/test_anomaly_clip.mp4",
        help="Path to a test clip (can be a non-existent path for smoke testing)",
    )
    parser.add_argument(
        "--anomaly-time",
        default=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
        help="ISO-8601 anomaly timestamp",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("AllSpark Edge Server – Agent Workflow E2E Test")
    print("=" * 60)
    print(f"Target: http://127.0.0.1:{args.port}")
    print(f"Clip path: {args.clip_path}")
    print(f"Anomaly time: {args.anomaly_time}")

    passed = run_e2e(args.port, args.clip_path, args.anomaly_time)

    print("\n" + "=" * 60)
    if passed:
        print("✅ All E2E checks passed.")
        sys.exit(0)
    else:
        print("❌ Some E2E checks failed. See output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()

