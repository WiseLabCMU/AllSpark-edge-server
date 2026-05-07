"""
test_chart_update.py
====================
Tests for the error-frequency chart update mechanism in
AllSpark-edge-server/python/control_plane/pages/agent.py.

Covers:
  1. _build_error_chart_data — basic chart data construction
  2. _load_interarrival_counts — JSON parsing + mtime cache
  3. chart_sig computation — detects new anomalies arriving
  4. chart_sig computation — detects interarrival_stats.json mtime change
  5. chart_sig is STABLE when nothing changes (no spurious redraws)
  6. End-to-end: simulate N anomalies arriving one by one, assert chart
     rebuilds on each new arrival and stays stable on repeated polls

Run:
    cd AllSpark-edge-server/python
    python -m pytest tests/test_chart_update.py -v
    # or directly:
    python tests/test_chart_update.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Load agent.py module under lightweight stubs (no NiceGUI / ADK required)
# ---------------------------------------------------------------------------
_PYTHON_DIR = Path(__file__).parent.parent          # .../AllSpark-edge-server/python
_AGENT_PY   = _PYTHON_DIR / 'control_plane' / 'pages' / 'agent.py'

sys.path.insert(0, str(_PYTHON_DIR))

_STUBS = [
    'nicegui', 'nicegui.ui', 'nicegui.app',
    'nicegui.elements', 'nicegui.elements.mixins',
    'google', 'google.adk', 'google.adk.agents',
    'fastapi', 'uvicorn', 'starlette', 'starlette.requests',
    'aiohttp',
    'theme', 'pages', 'pages.settings',
]
for _name in _STUBS:
    sys.modules.setdefault(_name, types.ModuleType(_name))

sys.modules['theme'].menu = lambda *a, **kw: None
sys.modules['pages.settings'].load_config = lambda: {}
sys.modules['pages.settings'].get_edge_base_url = lambda cfg=None: ''
sys.modules['pages'].settings = sys.modules['pages.settings']

_spec = importlib.util.spec_from_file_location('agent_page', str(_AGENT_PY))
_agent = importlib.util.module_from_spec(_spec)
sys.modules['agent_page'] = _agent  # required for @dataclass __module__ resolution
try:
    _spec.loader.exec_module(_agent)
    _AGENT_LOADED = True
except Exception as _e:
    _AGENT_LOADED = False
    _LOAD_ERR = str(_e)

_SKIP_MSG = f'agent.py could not be imported: {_LOAD_ERR if not _AGENT_LOADED else ""}'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(code: str, offset_secs: int = 0) -> dict:
    """Return a minimal response dict that _build_error_chart_data can parse."""
    ts = datetime(2026, 5, 7, 10, 0, 0 + offset_secs % 60, tzinfo=timezone.utc)
    return {
        'request_id':   f'req-{code}-{offset_secs}',
        'error':        f'code={code}(5)  text="simulated"  duration=3s  recorded_at={ts.isoformat()}',
        'anomaly_time': ts.isoformat(),
        'summary':      f'Simulated anomaly for {code}',
    }


def _write_stats(path: Path, summary: dict) -> None:
    data = {
        'start_time': '2026-05-07T08:00:00+00:00',
        'duration_seconds': 300,
        'mode': 'lookback',
        'lookback_errors': {
            'total': sum(summary.values()),
            'summary': summary,
            'descriptions': {},
            'records': [],
        },
    }
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')


def _compute_chart_sig(responses: list[dict], ia_json_path: Path | None = None) -> tuple:
    """
    Replicate the exact chart_sig computation from _render_responses so we
    can unit-test it without running NiceGUI.
    """
    ia_mtime = 0.0
    if ia_json_path is not None:
        try:
            ia_mtime = os.path.getmtime(str(ia_json_path))
        except OSError:
            pass
    return tuple(
        (r.get('error', ''), r.get('anomaly_time', ''))
        for r in responses
    ) + (ia_mtime,)


# ============================================================================
# Section 1 — _build_error_chart_data basics
# ============================================================================

@unittest.skipUnless(_AGENT_LOADED, _SKIP_MSG)
class TestBuildChartData(unittest.TestCase):

    def _build(self, responses, extra_counts=None):
        return _agent._build_error_chart_data(
            responses, hours=9999, top_n=5, extra_counts=extra_counts
        )

    def test_single_anomaly_appears(self):
        opts = self._build([_make_response('DG052')])
        self.assertIn('DG052', opts['_codes'])

    def test_count_increments_per_arrival(self):
        """Each new anomaly of the same code increments its bar value."""
        for n in (1, 3, 7):
            responses = [_make_response('DG052', i) for i in range(n)]
            opts = self._build(responses)
            idx = opts['_codes'].index('DG052')
            self.assertEqual(opts['_values'][idx], n,
                             f'Expected count {n} for {n} responses')

    def test_chart_options_contain_echart_keys(self):
        opts = self._build([_make_response('DG052')])
        for key in ('xAxis', 'yAxis', 'series', 'tooltip', 'grid'):
            self.assertIn(key, opts)

    def test_empty_returns_empty_dict(self):
        self.assertEqual(self._build([]), {})

    def test_no_error_field_skipped(self):
        resp = {'anomaly_time': '2026-05-07T10:00:00+00:00'}
        self.assertEqual(self._build([resp]), {})


# ============================================================================
# Section 2 — _load_interarrival_counts cache
# ============================================================================

@unittest.skipUnless(_AGENT_LOADED, _SKIP_MSG)
class TestLoadInterarrivalCounts(unittest.TestCase):

    def setUp(self):
        # Reset module-level cache between tests
        _agent._interarrival_cache.update({'path': '', 'mtime': -1.0, 'counts': {}})

    def test_reads_counts(self):
        with tempfile.TemporaryDirectory() as d:
            _write_stats(Path(d) / 'interarrival_stats.json', {'DG052': 5})
            result = _agent._load_interarrival_counts([d])
        self.assertEqual(result.get('DG052'), 5)

    def test_returns_empty_when_no_file(self):
        result = _agent._load_interarrival_counts(['/no/such/dir'])
        self.assertEqual(result, {})

    def test_cache_hit_same_mtime(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'interarrival_stats.json'
            _write_stats(p, {'DG052': 3})
            r1 = _agent._load_interarrival_counts([d])
            r2 = _agent._load_interarrival_counts([d])
        self.assertEqual(r1, r2)
        self.assertEqual(_agent._interarrival_cache['path'], str(p))

    def test_cache_miss_after_mtime_change(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'interarrival_stats.json'
            _write_stats(p, {'DG052': 3})
            _agent._load_interarrival_counts([d])
            time.sleep(0.05)
            _write_stats(p, {'DG052': 99})
            result = _agent._load_interarrival_counts([d])
        self.assertEqual(result.get('DG052'), 99)


# ============================================================================
# Section 3 — chart_sig detects new anomaly arrivals
# ============================================================================

@unittest.skipUnless(_AGENT_LOADED, _SKIP_MSG)
class TestChartSigNewAnomalies(unittest.TestCase):

    def test_sig_changes_when_anomaly_added(self):
        r1 = [_make_response('DG052', 0)]
        r2 = [_make_response('DG052', 0), _make_response('ONL135', 1)]
        self.assertNotEqual(
            _compute_chart_sig(r1),
            _compute_chart_sig(r2),
        )

    def test_sig_stable_on_repeated_poll_same_responses(self):
        responses = [_make_response('DG052', 0), _make_response('MP1269', 1)]
        sig1 = _compute_chart_sig(responses)
        sig2 = _compute_chart_sig(responses)
        self.assertEqual(sig1, sig2, 'Sig should be identical on repeated poll with same data')

    def test_sig_changes_for_different_error_code(self):
        r1 = [_make_response('DG052', 0)]
        r2 = [_make_response('ONL135', 0)]   # same timestamp, different code
        self.assertNotEqual(_compute_chart_sig(r1), _compute_chart_sig(r2))

    def test_sig_changes_for_different_anomaly_time(self):
        r1 = [_make_response('DG052', 0)]
        r2 = [_make_response('DG052', 10)]   # same code, different time offset
        self.assertNotEqual(_compute_chart_sig(r1), _compute_chart_sig(r2))

    def test_sig_changes_sequentially_for_n_arrivals(self):
        """Chart must rebuild on every individual new anomaly arrival."""
        sigs = set()
        responses = []
        for i in range(5):
            responses = responses + [_make_response(f'XX{i:03d}', i)]
            sigs.add(_compute_chart_sig(responses))
        self.assertEqual(len(sigs), 5, 'Each arrival must produce a distinct sig')


# ============================================================================
# Section 4 — chart_sig detects interarrival_stats.json mtime change
# ============================================================================

@unittest.skipUnless(_AGENT_LOADED, _SKIP_MSG)
class TestChartSigMtimeChange(unittest.TestCase):

    def test_sig_stable_when_file_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'interarrival_stats.json'
            _write_stats(p, {'DG052': 3})
            responses = [_make_response('DG052', 0)]
            sig1 = _compute_chart_sig(responses, p)
            sig2 = _compute_chart_sig(responses, p)
        self.assertEqual(sig1, sig2)

    def test_sig_changes_when_file_mtime_changes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'interarrival_stats.json'
            _write_stats(p, {'DG052': 3})
            responses = [_make_response('DG052', 0)]
            sig1 = _compute_chart_sig(responses, p)
            time.sleep(0.05)
            _write_stats(p, {'DG052': 99})   # new content → new mtime
            sig2 = _compute_chart_sig(responses, p)
        self.assertNotEqual(sig1, sig2,
            'sig must change when interarrival_stats.json is rewritten')

    def test_sig_changes_when_file_appears(self):
        """File did not exist on first poll; appears on second poll."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'interarrival_stats.json'
            responses = [_make_response('DG052', 0)]
            sig1 = _compute_chart_sig(responses, p)   # file absent
            _write_stats(p, {'DG052': 5})
            sig2 = _compute_chart_sig(responses, p)   # file now present
        self.assertNotEqual(sig1, sig2,
            'sig must change when interarrival_stats.json is created')

    def test_sig_changes_even_with_no_new_responses(self):
        """
        The critical scenario: kafka monitor restarts and rewrites
        interarrival_stats.json with a longer lookback; no new agent
        response has arrived, but the chart must still refresh.
        """
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'interarrival_stats.json'
            _write_stats(p, {'DG052': 3})
            responses = [_make_response('DG052', 0)]  # constant — no new responses
            sig1 = _compute_chart_sig(responses, p)
            time.sleep(0.05)
            _write_stats(p, {'DG052': 50, 'ONL135': 12})  # kafka monitor rewrote the file
            sig2 = _compute_chart_sig(responses, p)
        self.assertNotEqual(sig1, sig2,
            'Chart must rebuild when JSON is updated even with no new responses')


# ============================================================================
# Section 5 — end-to-end: simulate N anomalies arriving, assert chart rebuilds
# ============================================================================

@unittest.skipUnless(_AGENT_LOADED, _SKIP_MSG)
class TestEndToEndChartRebuild(unittest.TestCase):
    """
    Simulates the polling loop without NiceGUI:
      - A mutable `chart_sig` dict (matches the closure variable in agent.py)
      - A `rebuild_count` counter standing in for _update_chart()
      - Each 'poll tick' calls the same sig-comparison logic as _render_responses()

    Asserts:
      - rebuild fires on every new anomaly arrival
      - rebuild does NOT fire on repeated polls with identical data
      - rebuild fires when interarrival_stats.json mtime changes with no new responses
    """

    def _poll(self, responses, ia_path, chart_sig_state, rebuild_counter):
        new_sig = _compute_chart_sig(responses, ia_path)
        if new_sig != chart_sig_state['value']:
            chart_sig_state['value'] = new_sig
            rebuild_counter['n'] += 1

    def test_rebuild_once_per_new_anomaly(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'interarrival_stats.json'
            _write_stats(p, {})
            chart_sig  = {'value': None}
            rebuilds   = {'n': 0}
            responses  = []

            for i in range(5):
                responses = responses + [_make_response(f'DG{i:03d}', i)]
                self._poll(responses, p, chart_sig, rebuilds)

            self.assertEqual(rebuilds['n'], 5,
                'One rebuild per anomaly arrival + initial render')

    def test_no_rebuild_on_stable_polls(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'interarrival_stats.json'
            _write_stats(p, {'DG052': 3})
            chart_sig = {'value': None}
            rebuilds  = {'n': 0}
            responses = [_make_response('DG052', 0)]

            for _ in range(10):   # 10 identical polls
                self._poll(responses, p, chart_sig, rebuilds)

            self.assertEqual(rebuilds['n'], 1,
                'Should rebuild only once (initial), not on every stable poll')

    def test_rebuild_on_json_change_without_new_responses(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'interarrival_stats.json'
            _write_stats(p, {'DG052': 3})
            chart_sig = {'value': None}
            rebuilds  = {'n': 0}
            responses = [_make_response('DG052', 0)]

            # First poll — initial render
            self._poll(responses, p, chart_sig, rebuilds)
            # Several stable polls
            for _ in range(3):
                self._poll(responses, p, chart_sig, rebuilds)
            before = rebuilds['n']

            # kafka monitor rewrites the file
            time.sleep(0.05)
            _write_stats(p, {'DG052': 50, 'ONL135': 8})

            # Next poll must trigger a rebuild
            self._poll(responses, p, chart_sig, rebuilds)
            self.assertGreater(rebuilds['n'], before,
                'Rebuild must fire after interarrival_stats.json mtime changes')

    def test_combined_anomalies_plus_json_update(self):
        """Both sources of change arrive in the same session."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'interarrival_stats.json'
            _write_stats(p, {'DG052': 2})
            chart_sig = {'value': None}
            rebuilds  = {'n': 0}
            responses = []

            # 3 new anomalies
            for i in range(3):
                responses = responses + [_make_response(f'CODE{i:02d}', i)]
                self._poll(responses, p, chart_sig, rebuilds)

            # 5 stable polls
            for _ in range(5):
                self._poll(responses, p, chart_sig, rebuilds)

            mid = rebuilds['n']

            # JSON updated, no new responses
            time.sleep(0.05)
            _write_stats(p, {'DG052': 20, 'MP1179': 5})
            self._poll(responses, p, chart_sig, rebuilds)

            self.assertEqual(mid, 3, 'Should have 3 rebuilds from anomalies')
            self.assertEqual(rebuilds['n'], 4, 'One more rebuild after JSON update')


# ============================================================================
# Entry point
# ============================================================================

if __name__ == '__main__':
    loaded = 'YES' if _AGENT_LOADED else f'NO — {_LOAD_ERR}'
    print(f'agent.py loaded: {loaded}')
    print()
    unittest.main(verbosity=2)
