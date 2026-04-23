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
def get_edge_base_url() -> str:
    full_config = {}
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r') as f:
                full_config = yaml.safe_load(f) or {}
    except Exception:
        pass
    
    mc_cfg = full_config.get('mobile_client', {})
    port = mc_cfg.get("port", 8080)
    host = mc_cfg.get("hostname", "127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    key_path = os.path.join(base_dir, mc_cfg.get("keyFile", "keys/test-private.key"))
    cert_path = os.path.join(base_dir, mc_cfg.get("certFile", "keys/test-public.crt"))
    
    if os.path.exists(key_path) and os.path.exists(cert_path):
        return f"https://{host}:{port}"
    return f"http://{host}:{port}"

def load_config():
    needs_save = False
    full_config = {}
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r') as f:
                full_config = yaml.safe_load(f) or {}
    except Exception as e:
        ui.notify(f'Error loading config: {e}', type='negative')

    import copy
    cp_config = copy.deepcopy(DEFAULT_CP_CONFIG)
    original_cp = full_config.get('control_plane', {})

    def _deep_update(d, u):
        for k, v in u.items():
            if isinstance(v, dict) and k in d and isinstance(d[k], dict):
                _deep_update(d[k], v)
            else:
                d[k] = v
        return d

    _deep_update(cp_config, original_cp)

    if original_cp != cp_config:
        import copy
        full_config['control_plane'] = copy.deepcopy(cp_config)
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
                    ui.input('Legacy Upload Path').bind_value(config, 'uploadPath').classes('w-full').props('hint="Superseded by Client Uploads Path"')
                    ui.input('Client Uploads Path').bind_value(config, 'clientUploadsPath').classes('w-full')
                    ui.input('Agent Response Path').bind_value(config, 'agentResponsePath').classes('w-full')
                    ui.number('Keep Alive Interval (ms)').bind_value(config, 'keepAliveIntervalMs').classes('w-full')
                    ui.checkbox('Auto Request Client Uploads').bind_value(config, 'autoUpload')

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
                    ui.input('Legacy Anomaly Logs Path').bind_value(cp_config['logPaths'], 'anomalyLogs').classes('w-full').props('hint="Superseded by Agent Response Path"')
                    ui.input('Rig Logs Path').bind_value(cp_config['logPaths'], 'rigLogs').classes('w-full')

            # Client Configs
            if 'clientConfig' not in config:
                config['clientConfig'] = {}
            if 'agentConfig' not in config:
                config['agentConfig'] = {}

            with ui.row().classes('w-full gap-6 mb-6'):
                with ui.card().classes('w-full flex-1 min-w-[300px]'):
                    ui.label('Agent Config').classes('text-lg font-bold mb-2 text-gray-700')
                    ui.input('Agent URL').bind_value(config['agentConfig'], 'agent_url').classes('w-full')
                    ui.input('Agent App Name').bind_value(config['agentConfig'], 'agent_app_name').classes('w-full')
                    ui.input('Agent User ID').bind_value(config['agentConfig'], 'agent_user_id').classes('w-full')
                    ui.input('Agent Session ID').bind_value(config['agentConfig'], 'agent_session_id').classes('w-full')
                    ui.number('Agent Timeout (s)').bind_value(config['agentConfig'], 'agent_timeout').classes('w-full')
                    ui.input('Agent Init Message').bind_value(config['agentConfig'], 'agent_init_message').classes('w-full')

            with ui.row().classes('w-full gap-6'):
                with ui.card().classes('w-full flex-1 min-w-[300px]'):
                    ui.label('Mobile Client Config').classes('text-lg font-bold mb-2 text-gray-700')
                    ui.input('Video Format').bind_value(config['clientConfig'], 'videoFormat').classes('w-full')
                    ui.input('Audio Format').bind_value(config['clientConfig'], 'audioFormat').classes('w-full')
                    ui.input('Depth Format').bind_value(config['clientConfig'], 'depthFormat').classes('w-full')
                    ui.input('Pose Format').bind_value(config['clientConfig'], 'poseFormat').classes('w-full')
                    ui.input('Timestamp Format').bind_value(config['clientConfig'], 'timestampFormat').classes('w-full')
                    ui.number('FPS').bind_value(config['clientConfig'], 'fps').classes('w-full')
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
