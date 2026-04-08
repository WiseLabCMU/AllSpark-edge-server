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
            # Provide an informative panel above the iframe
            with ui.row().classes('w-full justify-between items-baseline mb-4'):
                ui.label('Data Plane: Rerun.io').classes('text-2xl font-bold text-gray-800')
                ui.button('Open in New Window', icon='open_in_browser', on_click=lambda: ui.run_javascript(f"window.open('{rerun_url}', '_blank')")).props('flat')
                
            # The iframe embedding rerun.io
            ui.html(f'''
                <iframe src="{rerun_url}" class="w-full" style="height: calc(100vh - 240px); border: 1px solid #ccc; border-radius: 8px;">
                    Your browser does not support iframes, or the Rerun server is offline.
                </iframe>
            ''').classes('w-full')
