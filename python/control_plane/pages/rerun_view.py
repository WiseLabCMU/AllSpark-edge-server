from nicegui import ui
from theme import menu
from pages.settings import load_config
from urllib.parse import quote

def create_page():
    @ui.page('/rerun')
    def rerun_page(anomaly: str = ''):
        """
        Data Plane / Rerun.io viewer.

        Query params:
            anomaly: optional anomaly folder name (e.g.
                ``anomaly_2026-04-02T20-49-04``) indicating which anomaly
                the currently-running rerun_server.py was launched for.
                Used purely for display – the iframe always points at the
                rerun web viewer port.
        """
        config = load_config()
        cp_config = config.get('control_plane', {})
        rerun_host = cp_config.get('rerunHost', '127.0.0.1')
        rerun_port = cp_config.get('rerunPort', 9090)
        # rerun's embedded gRPC server always listens on 9876 (the SDK
        # default). The web viewer needs to be told to auto-connect to
        # that stream via a ``?url=rerun+http://<host>:9876/proxy`` query
        # parameter – otherwise it just shows the "welcome" screen with
        # no data.
        rerun_grpc_port = cp_config.get('rerunGrpcPort', 9876)
        rerun_grpc_uri = f"rerun+http://{rerun_host}:{rerun_grpc_port}/proxy"
        viewer_base = f"http://{rerun_host}:{rerun_port}"
        viewer_with_data = f"{viewer_base}/?url={quote(rerun_grpc_uri, safe='')}"

        with menu('Data Plane: Rerun.io', full_width=True, hide_title=True):
            with ui.row().classes('w-full justify-between items-center mb-2'):
                if anomaly:
                    with ui.column().classes('gap-0'):
                        ui.label('Data Plane: Rerun.io').classes(
                            'text-2xl font-bold text-gray-800'
                        )
                        ui.label(f'🚨 Viewing anomaly: {anomaly}').classes(
                            'text-sm text-amber-700 font-mono'
                        )
                else:
                    ui.label('Data Plane: Rerun.io').classes(
                        'text-2xl font-bold text-gray-800'
                    )
                with ui.row().classes('items-center gap-2'):
                    ui.button('Open in New Window', icon='open_in_browser',
                              on_click=lambda: ui.run_javascript(
                                  f"window.open('{viewer_with_data}', '_blank')"
                              )).props('flat')
                    ui.button('Back', icon='arrow_back',
                              on_click=lambda: ui.navigate.to('/agent')).props('flat')

            ui.label(f'Connected to data stream at {rerun_grpc_uri}').classes(
                'text-xs font-mono text-gray-400 mb-2'
            )

            # The iframe embeds the rerun web viewer with an explicit
            # ``?url=`` query param so it auto-connects to our gRPC proxy.
            # A cache-buster ``_t`` is also appended so that when a
            # per-anomaly viewer is relaunched on the same port, the
            # browser actually re-requests the page (rerun's SPA caches
            # session state aggressively otherwise).
            import time as _time
            cache_buster = int(_time.time())
            iframe_src = f"{viewer_with_data}&_t={cache_buster}"
            ui.html(f'''
                <iframe src="{iframe_src}" class="w-full"
                        style="height: calc(100vh - 240px); border: 1px solid #ccc; border-radius: 8px;"
                        onerror="this.style.display='none'"
                        onload="document.getElementById('rerun-fallback').style.display='none'">
                    Your browser does not support iframes, or the Rerun server is offline.
                </iframe>
                <div id="rerun-fallback" style="text-align:center; padding:40px; color:#888;">
                    <p>If the Rerun viewer does not load, ensure the Rerun server is running:</p>
                    <code style="background:#f0f0f0; padding:8px 16px; border-radius:4px; display:inline-block; margin-top:8px;">
                        python control_plane/rerun_server.py --port {rerun_port}
                    </code>
                </div>
            ''').classes('w-full')


