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
        # rerunExternalHost is the hostname the *browser* will use — must be
        # resolvable from the client machine. Falls back to rerunHost so
        # localhost/dev setups still work without adding the field.
        rerun_external_host = cp_config.get('rerunExternalHost', rerun_host)
        rerun_port = cp_config.get('rerunPort', 9090)
        # rerun's embedded gRPC server always listens on 9876 (the SDK
        # default). The web viewer needs to be told to auto-connect to
        # that stream via a ``?url=rerun+http://<host>:9876/proxy`` query
        # parameter – otherwise it just shows the "welcome" screen with
        # no data.
        rerun_grpc_port = cp_config.get('rerunGrpcPort', 9876)
        rerun_grpc_uri = f"rerun+http://{rerun_external_host}:{rerun_grpc_port}/proxy"
        viewer_base = f"http://{rerun_external_host}:{rerun_port}"
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

            # Rerun's tiny-http server sends X-Frame-Options headers that
            # prevent iframe embedding. Open the viewer in a new browser tab
            # instead, and auto-trigger the open on page load.
            import time as _time
            cache_buster = int(_time.time())
            viewer_url = f"{viewer_with_data}&_t={cache_buster}"
            ui.run_javascript(f"window.open('{viewer_url}', '_blank')")

            with ui.card().classes('w-full mt-4').style(
                'background:#f8fafc; border:1px solid #e2e8f0; border-radius:12px;'
                'padding:40px; text-align:center;'
            ):
                ui.icon('open_in_new', size='48px').classes('text-blue-400 mb-4')
                ui.label('Rerun Viewer opened in a new tab').classes(
                    'text-xl font-semibold text-gray-700 mb-2'
                )
                ui.label(
                    'The Rerun web viewer cannot be embedded due to browser security '
                    'restrictions (X-Frame-Options). It has been opened in a new tab automatically.'
                ).classes('text-sm text-gray-500 mb-6 max-w-lg mx-auto')
                ui.button(
                    'Open Rerun Viewer',
                    icon='open_in_new',
                    on_click=lambda: ui.run_javascript(f"window.open('{viewer_url}', '_blank')"),
                ).props('color=primary unelevated').classes('mb-3')
                ui.label(viewer_url).classes(
                    'text-xs font-mono text-gray-400 break-all max-w-lg mx-auto'
                )


