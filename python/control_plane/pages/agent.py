"""
Agent Page – AllSpark Control Plane

Provides:
1. A form to trigger an on-demand anomaly analysis via the Edge Server
   POST /api/agent/analyze endpoint.
2. A live-updating response feed that polls GET /api/agent/responses and
   renders each result with its summary, metadata, and status badge.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import aiohttp
from nicegui import ui

from theme import menu
from pages.settings import load_config


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DEVICE = "default"
_POLL_INTERVAL_SEC = 10.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _status_badge_color(status: str) -> str:
    return "green" if status == "success" else "red"


# ---------------------------------------------------------------------------
# Page definition
# ---------------------------------------------------------------------------

def create_page() -> None:

    @ui.page('/agent')
    async def agent_page():
        config = load_config()
        edge_port = config.get('port', 8080)
        base_url = f"http://127.0.0.1:{edge_port}"

        with menu('Agentic Analysis'):

            # ----------------------------------------------------------------
            # Section 1: On-demand analysis form
            # ----------------------------------------------------------------
            with ui.card().classes('w-full mb-6 p-4'):
                ui.label('Trigger Anomaly Analysis').classes('text-xl font-bold mb-4')

                with ui.row().classes('w-full gap-4 flex-wrap'):
                    clip_path_input = ui.input(
                        'Anomaly Clip Path',
                        placeholder='/path/to/anomaly_clip_20260413_120000.mp4',
                    ).classes('flex-1 min-w-[300px]')

                    log_path_input = ui.input(
                        'Log Path (optional)',
                        placeholder='/path/to/mqtt_trace.log',
                    ).classes('flex-1 min-w-[300px]')

                with ui.row().classes('w-full gap-4 flex-wrap'):
                    anomaly_time_input = ui.input(
                        'Anomaly Time (ISO-8601)',
                        placeholder='2026-04-13T12:00:00',
                    ).classes('flex-1 min-w-[200px]')

                    clip_start_input = ui.input(
                        'Clip Start Time (ISO-8601, optional)',
                        placeholder='2026-04-13T11:59:30',
                    ).classes('flex-1 min-w-[200px]')

                    device_name_input = ui.input(
                        'Device Name',
                        value=_DEFAULT_DEVICE,
                    ).classes('flex-1 min-w-[160px]')

                with ui.row().classes('w-full gap-4 flex-wrap'):
                    error_input = ui.input(
                        'Error / Label',
                        value='missed expected message',
                    ).classes('flex-1 min-w-[200px]')

                    expected_topic_input = ui.input(
                        'Expected MQTT Topic',
                        placeholder='allspark/anomaly_detected',
                    ).classes('flex-1 min-w-[200px]')

                video_storage_input = ui.input(
                    'Video Storage Path (optional)',
                    placeholder='/path/to/video/chunks/',
                ).classes('w-full')

                submit_btn = ui.button(
                    'Dispatch to Agentic Framework',
                    icon='science',
                ).classes('mt-4 bg-blue-600 text-white')

                status_label = ui.label('').classes('mt-2 text-sm text-gray-600')

                async def on_submit():
                    submit_btn.disable()
                    status_label.set_text('Sending request to agent framework…')

                    payload: Dict[str, Any] = {
                        "clip_path": clip_path_input.value.strip(),
                        "log_path": log_path_input.value.strip(),
                        "anomaly_time": anomaly_time_input.value.strip(),
                        "clip_start_time": clip_start_input.value.strip(),
                        "error": error_input.value.strip(),
                        "expected_topic": expected_topic_input.value.strip(),
                        "video_storage_path": video_storage_input.value.strip(),
                        "device_name": device_name_input.value.strip() or _DEFAULT_DEVICE,
                    }

                    if not payload["clip_path"] or not payload["anomaly_time"]:
                        ui.notify(
                            'clip_path and anomaly_time are required.',
                            type='warning',
                        )
                        status_label.set_text('')
                        submit_btn.enable()
                        return

                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.post(
                                f'{base_url}/api/agent/analyze',
                                json=payload,
                                timeout=aiohttp.ClientTimeout(total=360),
                            ) as resp:
                                result = await resp.json(content_type=None)

                        if result.get('success'):
                            ui.notify(
                                f"Analysis complete. Request ID: {result.get('request_id', 'N/A')}",
                                type='positive',
                            )
                            status_label.set_text(
                                f"✅ Stored at: {result.get('stored_at', '')}"
                            )
                            await _refresh_responses()
                        else:
                            err = result.get('error_message') or result.get('error', 'Unknown error')
                            ui.notify(f'Analysis failed: {err}', type='negative')
                            status_label.set_text(f'❌ Error: {err}')

                    except Exception as exc:
                        ui.notify(f'Request failed: {exc}', type='negative')
                        status_label.set_text(f'❌ {exc}')
                    finally:
                        submit_btn.enable()

                submit_btn.on_click(on_submit)

            # ----------------------------------------------------------------
            # Section 2: Stored responses feed
            # ----------------------------------------------------------------
            with ui.row().classes('w-full justify-between items-center mb-2'):
                ui.label('Agent Analysis Responses').classes('text-xl font-bold')
                refresh_btn = ui.button(
                    'Refresh', icon='refresh'
                ).props('flat dense').classes('text-blue-600')

            device_filter = ui.input(
                'Filter by Device Name (leave blank for all)',
                placeholder=_DEFAULT_DEVICE,
            ).classes('w-64 mb-4')

            responses_container = ui.column().classes('w-full gap-4')

            async def _refresh_responses():
                device: Optional[str] = device_filter.value.strip() or None
                url = f'{base_url}/api/agent/responses'
                params: Dict[str, str] = {'limit': '30'}
                if device:
                    params['device_name'] = device

                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            url,
                            params=params,
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as resp:
                            if resp.status != 200:
                                return
                            data = await resp.json(content_type=None)
                except Exception:
                    return

                _render_responses(data.get('responses', []))

            def _render_responses(responses: List[Dict[str, Any]]) -> None:
                responses_container.clear()
                with responses_container:
                    if not responses:
                        ui.label(
                            'No agent responses found yet. '
                            'Trigger an analysis above or wait for results.'
                        ).classes('text-gray-500 italic p-4')
                        return

                    for r in responses:
                        _render_response_card(r)

            def _render_response_card(r: Dict[str, Any]) -> None:
                status = r.get('status', 'unknown')
                color = _status_badge_color(status)
                anomaly_time = r.get('anomaly_time', 'N/A')
                clip_path = r.get('clip_path', 'N/A')
                session_id = r.get('session_id', 'N/A')
                summary = r.get('summary', '')
                created_at = r.get('created_at', '')
                stored_at = r.get('stored_at', '')
                error_msg = r.get('error_message', '')
                request_id = r.get('request_id', 'N/A')

                with ui.card().classes('w-full shadow-md'):
                    with ui.row().classes('w-full justify-between items-start'):
                        with ui.column().classes('gap-1'):
                            with ui.row().classes('items-center gap-2'):
                                ui.badge(status.upper(), color=color).classes('text-xs')
                                ui.label(f'Request: {request_id}').classes('font-bold text-gray-800')
                            ui.label(f'Anomaly Time: {anomaly_time}').classes('text-sm text-gray-600')
                            ui.label(f'Clip: {clip_path}').classes(
                                'text-xs text-gray-500 font-mono truncate max-w-xl'
                            ).tooltip(clip_path)
                            ui.label(f'Session: {session_id}').classes('text-xs text-gray-400')
                        ui.label(created_at[:19] if created_at else '').classes(
                            'text-xs text-gray-400 mt-1'
                        )

                    ui.separator().classes('my-2')

                    if status == 'success' and summary:
                        with ui.expansion(
                            'Agent Summary', icon='psychology', value=True
                        ).classes('w-full'):
                            ui.markdown(summary).classes('text-sm')
                    elif status == 'error':
                        ui.label(f'Error: {error_msg}').classes('text-red-600 text-sm')
                    else:
                        ui.label('No summary available.').classes(
                            'text-gray-400 text-sm italic'
                        )

                    if stored_at:
                        ui.label(f'📁 {stored_at}').classes(
                            'text-xs text-gray-400 font-mono mt-2 truncate'
                        ).tooltip(stored_at)

            # Wire up controls
            refresh_btn.on_click(_refresh_responses)
            device_filter.on('blur', lambda: ui.timer(0.1, _refresh_responses, once=True))
            ui.timer(_POLL_INTERVAL_SEC, _refresh_responses)
            ui.timer(0.1, _refresh_responses, once=True)

