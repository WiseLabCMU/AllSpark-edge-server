from nicegui import app, ui
import os

# Import all pages to register their routes
from pages import clients, agent, rerun_view, settings, debug

# Initialize pages
clients.create_page()
agent.create_page()
rerun_view.create_page()
settings.create_page()
debug.create_page()

@ui.page('/')
def index():
    ui.navigate.to('/agent')

if __name__ in {"__main__", "__mp_main__"}:
    # Read port from config.yaml and start NiceGUI on config['port'] + 1 (default 8081).
    full_config = settings.load_config()
    cp_config = full_config.get('control_plane', {})
    mc_config = full_config.get('mobile_client', {})

    edge_port = mc_config.get('port', 8080)
    sidecar_port = cp_config.get('port', edge_port + 1)

    # Mount the dynamic video storage directory for browser playback
    upload_path = mc_config.get('uploadPath', 'logs/data/mobile-client')
    abs_upload_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', upload_path))
    os.makedirs(abs_upload_path, exist_ok=True)
    app.add_media_files('/videos', abs_upload_path)

    # Run the control plane
    storage_secret = cp_config.get('storageSecret', 'allspark-secret')
    ui.run(title='AllSpark Control Plane', port=sidecar_port, storage_secret=storage_secret)
