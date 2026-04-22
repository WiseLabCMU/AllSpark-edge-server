from nicegui import ui
from theme import menu
from pages.settings import load_config

def create_page():
    @ui.page('/rerun')
    def rerun_page():
        config = load_config()
        cp_config = config.get('control_plane', {})
        rerun_host = cp_config.get('rerunHost', '127.0.0.1')
        rerun_port = cp_config.get('rerunPort', 9090)
        rerun_url = f"http://{rerun_host}:{rerun_port}"

        with menu('Data Plane: Rerun.io', full_width=True, hide_title=True):
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label('Data Plane: Rerun.io').classes('text-2xl font-bold text-gray-800')
                with ui.row().classes('items-center gap-2'):
                    ui.button('Open in New Window', icon='open_in_browser',
                              on_click=lambda: ui.run_javascript(
                                  f"window.open('{rerun_url}', '_blank')"
                              )).props('flat')
                    ui.button('Back', icon='arrow_back',
                              on_click=lambda: ui.navigate.to('/agent')).props('flat')

            ui.label(f'Connecting to Rerun at {rerun_url}').classes(
                'text-xs font-mono text-gray-400 mb-2'
            )

            # The iframe embedding rerun.io
            ui.html(f'''
                <iframe src="{rerun_url}" class="w-full"
                        style="height: calc(100vh - 240px); border: 1px solid #ccc; border-radius: 8px;"
                        onerror="this.style.display='none'"
                        onload="document.getElementById('rerun-fallback').style.display='none'">
                    Your browser does not support iframes, or the Rerun server is offline.
                </iframe>
                <div id="rerun-fallback" style="text-align:center; padding:40px; color:#888;">
                    <p>If the Rerun viewer does not load, ensure the Rerun server is running:</p>
                    <code style="background:#f0f0f0; padding:8px 16px; border-radius:4px; display:inline-block; margin-top:8px;">
                        rerun --serve-web --web-viewer-port {rerun_port}
                    </code>
                </div>
            ''').classes('w-full')


