from nicegui import ui
from theme import menu

def create_page():
    @ui.page('/')
    def dashboard_page():
        with menu('Dashboard - MQTT Anomalies'):
            ui.label('Active System Anomalies').classes('text-xl font-semibold mb-2')
            
            with ui.list().classes('w-full border rounded'):
                with ui.expansion('Camera Rig A - High Latency (3 events)', icon='warning').classes('w-full'):
                    ui.label('Event 1: 2026-03-25 10:00:01 - Latency > 500ms')
                    ui.label('Event 2: 2026-03-25 10:05:12 - Latency > 500ms')
                    ui.label('Event 3: 2026-03-25 10:12:45 - Latency > 500ms')
                    ui.button('Investigate via Agent', on_click=lambda: ui.navigate.to('/agent')).classes('mt-2')
                
                with ui.expansion('Mobile Client B - Connection Dropped', icon='error').classes('w-full'):
                    ui.label('Event 1: 2026-03-25 09:45:00 - Disconnected unexpectedly.')
