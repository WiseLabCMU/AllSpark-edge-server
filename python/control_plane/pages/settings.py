from nicegui import ui
import json
import os
from theme import menu

# Go up 3 levels from pages/ (pages -> control_plane -> python -> root)
CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'config.json')

def load_config():
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        ui.notify(f'Error loading config: {e}', type='negative')
        return {}

def save_config(config_data):
    try:
        with open(CONFIG_PATH, 'w') as f:
            json.dump(config_data, f, indent=2)
        ui.notify('Settings saved successfully! Restart edge server to apply some changes.', type='positive')
    except Exception as e:
        ui.notify(f'Error saving config: {e}', type='negative')

def create_page():
    @ui.page('/settings')
    def settings_page():
        # Ensure config lives on for bindings
        config = load_config()

        def on_save():
            save_config(config)

        with menu('Global Settings'):
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label('Configuration (config.json)').classes('text-xl font-bold text-gray-800')
                ui.button('Save Changes', icon='save', on_click=on_save).classes('bg-green-600 text-white font-bold')

            with ui.row().classes('w-full gap-6 mb-6'):
                # General Server Settings
                with ui.card().classes('w-full flex-1 min-w-[300px]'):
                    ui.label('General Server Configuration').classes('text-lg font-bold mb-2')
                    ui.input('Service Name').bind_value(config, 'serviceName').classes('w-full')
                    ui.input('Hostname').bind_value(config, 'hostname').classes('w-full')
                    ui.number('Port').bind_value(config, 'port').classes('w-full')
                    ui.input('Upload Path (Videos/Logs)').bind_value(config, 'uploadPath').classes('w-full')
                    ui.number('Keep Alive Interval (ms)').bind_value(config, 'keepAliveIntervalMs').classes('w-full')

                # Security & Paths
                with ui.card().classes('w-full flex-1 min-w-[300px]'):
                    ui.label('Security Paths').classes('text-lg font-bold mb-2')
                    ui.input('Key File Path').bind_value(config, 'keyFile').classes('w-full')
                    ui.input('Cert File Path').bind_value(config, 'certFile').classes('w-full')

            # Client Configs
            if 'clientConfig' not in config:
                config['clientConfig'] = {}

            with ui.row().classes('w-full gap-6'):
                with ui.card().classes('w-full flex-1 min-w-[300px]'):
                    ui.label('Mobile Client Config').classes('text-lg font-bold mb-2 text-gray-700')
                    ui.input('Video Format').bind_value(config['clientConfig'], 'videoFormat').classes('w-full')
                    ui.number('Video Chunk Duration (ms)').bind_value(config['clientConfig'], 'videoChunkDurationMs').classes('w-full')
                    ui.number('Video Buffer Max (MB)').bind_value(config['clientConfig'], 'videoBufferMaxMB').classes('w-full')

                # Comms Policy
                if 'communicationsPolicy' not in config['clientConfig']:
                    config['clientConfig']['communicationsPolicy'] = {}
                
                with ui.card().classes('w-full flex-1 min-w-[300px]'):
                    ui.label('Communications Policy').classes('text-lg font-bold mb-4 text-gray-700')
                    with ui.row().classes('gap-6 flex-wrap w-full'):
                        for key in ['wifi', 'cellular', 'ethernet', 'usb', 'bluetooth', 'airdrop', 'nfc', 'uwb', 'satellite']:
                            # Using lambda in a loop requires default arg to capture the key
                            ui.checkbox(key.capitalize()).bind_value(config['clientConfig']['communicationsPolicy'], key)
