from nicegui import app, ui
import os

# Import all pages to register their routes
from pages import dashboard, clients, capture, agent, rerun_view, settings

# Initialize pages
dashboard.create_page()
clients.create_page()
capture.create_page()
agent.create_page()
rerun_view.create_page()
settings.create_page()

if __name__ in {"__main__", "__mp_main__"}:
    # Read port from config.json and start NiceGUI on config['port'] + 1 (default 8081).
    config = settings.load_config()
    edge_port = config.get('port', 8080)
    sidecar_port = edge_port + 1
    
    # Mount the dynamic video storage directory for browser playback
    upload_path = config.get('uploadPath', 'uploads/orgs/default')
    abs_upload_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', upload_path))
    os.makedirs(abs_upload_path, exist_ok=True)
    app.add_media_files('/videos', abs_upload_path)

    # Run the control plane
    ui.run(title='AllSpark Control Plane', port=sidecar_port, storage_secret='allspark-secret')
