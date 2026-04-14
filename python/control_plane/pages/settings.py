from nicegui import ui
import yaml
import os
from theme import menu

# Go up 2 levels from pages/ (pages -> control_plane -> edge_server)
CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config.yaml')

DEFAULT_CP_CONFIG = {
    "port": 8081,
    "storageSecret": "allspark-secret",
    "rerunHost": "127.0.0.1",
    "rerunPort": 9090,
    "logPaths": {
        "anomalyLogs": "logs/anomalies/",
        "rigLogs": "logs/data/datacapture-rig"
    }
}

def load_config():
    needs_save = False
    full_config = {}
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r') as f:
                full_config = yaml.safe_load(f) or {}
    except Exception as e:
        ui.notify(f'Error loading config: {e}', type='negative')
        
    if 'control_plane' not in full_config:
        full_config['control_plane'] = DEFAULT_CP_CONFIG
        needs_save = True
        
    if needs_save:
        try:
            with open(CONFIG_PATH, 'w') as f:
                yaml.dump(full_config, f, default_flow_style=False, sort_keys=False)
        except Exception:
            pass
            
    return full_config

def save_config(config_data):
    try:
        with open(CONFIG_PATH, 'w') as f:
            yaml.dump(config_data, f, default_flow_style=False)
        ui.notify('Settings saved successfully! Restart edge server to apply some changes.', type='positive')
    except Exception as e:
        ui.notify(f'Error saving config: {e}', type='negative')

def create_page():
    @ui.page('/settings')
    def settings_page():
        # Ensure config lives on for bindings
        full_config = load_config()
        if 'mobile_client' not in full_config:
            full_config['mobile_client'] = {}
            
        config = full_config['mobile_client']
        cp_config = full_config['control_plane']

        def on_save():
            save_config(full_config)

        with menu('Global Settings'):
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label('Configuration (config.yaml)').classes('text-xl font-bold text-gray-800')
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
                    with ui.row().classes('gap-4 mt-2'):
                        ui.checkbox('Auto Upload Media').bind_value(config, 'autoUpload')
                        ui.checkbox('Enable QUIC Server').bind_value(config, 'enableQuic')

                # Security & Paths
                with ui.card().classes('w-full flex-1 min-w-[300px]'):
                    ui.label('Security Paths').classes('text-lg font-bold mb-2')
                    ui.input('Key File Path').bind_value(config, 'keyFile').classes('w-full')
                    ui.input('Cert File Path').bind_value(config, 'certFile').classes('w-full')
                    
                # Control Plane Settings
                if 'logPaths' not in cp_config:
                    cp_config['logPaths'] = {}
                with ui.card().classes('w-full flex-1 min-w-[300px]'):
                    ui.label('Control Plane Configuration').classes('text-lg font-bold mb-2')
                    ui.number('Port').bind_value(cp_config, 'port').classes('w-full')
                    ui.input('Storage Secret').bind_value(cp_config, 'storageSecret').props('type=password').classes('w-full')
                    ui.input('Rerun Host').bind_value(cp_config, 'rerunHost').classes('w-full')
                    ui.number('Rerun Port').bind_value(cp_config, 'rerunPort').classes('w-full')
                    ui.input('Anomaly Logs Path').bind_value(cp_config['logPaths'], 'anomalyLogs').classes('w-full')
                    ui.input('Rig Logs Path').bind_value(cp_config['logPaths'], 'rigLogs').classes('w-full')

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
