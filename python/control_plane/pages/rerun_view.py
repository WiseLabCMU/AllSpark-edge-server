from nicegui import ui
from theme import menu

def create_page():
    @ui.page('/rerun')
    def rerun_page():
        with menu('Data Plane: Rerun.io'):
            # Provide an informative panel above the iframe
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label('Agentic Rerun Viewer').classes('text-xl font-bold text-gray-800')
                ui.button('Open in New Window', icon='open_in_browser', on_click=lambda: ui.run_javascript("window.open('http://127.0.0.1:9090', '_blank')")).props('flat')
                
            # The iframe embedding rerun.io
            ui.html('''
                <iframe src="http://127.0.0.1:9090" class="w-full" style="height: 70vh; border: 1px solid #ccc; border-radius: 8px;">
                    Your browser does not support iframes, or the Rerun server is offline.
                </iframe>
            ''').classes('w-full')
