from nicegui import app, ui
import os
import time
from pathlib import Path
from theme import menu
from pages.settings import load_config

def get_anomaly_path():
    full_config = load_config()
    cp_config = full_config.get('control_plane', {})
    anomaly_path = cp_config.get('logPaths', {}).get('anomalyLogs', 'logs/anomalies/')
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', anomaly_path))

def fetch_files(base_path):
    files_data = []
    if not os.path.exists(base_path):
        return files_data
        
    for p in Path(base_path).rglob('*'):
        if p.is_file():
            stat = p.stat()
            files_data.append({
                'path': str(p),
                'rel_path': str(p.relative_to(base_path)),
                'name': p.name,
                'ext': p.suffix.lower(),
                'size': stat.st_size,
                'mtime': stat.st_mtime,
                'mtime_str': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))
            })
    return sorted(files_data, key=lambda x: x['mtime'], reverse=True)

def render_file_content(f):
    ext = f['ext']
    path = f['path']
    rel_path = f['rel_path']
    
    # Text-based
    if ext in ['.txt', '.json', '.yaml', '.yml', '.csv', '.md', '.log']:
        try:
            with open(path, 'r', encoding='utf-8') as file:
                content = file.read()
                # Truncate if too huge
                if len(content) > 50000:
                    content = content[:50000] + "\n...[truncated]"
                ui.textarea(value=content).props('readonly').classes('w-full h-64 font-mono text-sm bg-gray-50 border p-2')
        except Exception as e:
            ui.label(f"Error reading text file: {e}").classes('text-red-500')
            
    # Media: Video
    elif ext in ['.mp4', '.webm', '.ogg']:
        # To serve this media, it needs an app.add_media_files route.
        # We can dynamically add a route for the anomaly dir if not already added.
        ui.video(f'/anomaly_media/{rel_path}').classes('w-full max-w-2xl bg-black rounded')
        
    # Media: Image
    elif ext in ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp']:
        ui.image(f'/anomaly_media/{rel_path}').classes('w-full max-w-2xl rounded')
        
    # Audio
    elif ext in ['.mp3', '.wav']:
        ui.audio(f'/anomaly_media/{rel_path}').classes('w-full')
        
    else:
        ui.label(f"Binary or unsupported format. File size: {f['size']} bytes.").classes('text-gray-500 italic')
        ui.button('Download', on_click=lambda p=path: ui.download(p)).classes('mt-2')

def create_page():
    # Ensure media router is set up once
    base_path = get_anomaly_path()
    os.makedirs(base_path, exist_ok=True)
    app.add_media_files('/anomaly_media', base_path)

    @ui.page('/anomalies')
    def anomalies_page():
        # Store state locally to this page connection
        page_state = {'last_hash': None}
        
        with menu('System Anomalies'):
            ui.label('Detected Anomaly Files').classes('text-xl font-semibold mb-4 text-gray-800')
            
            container = ui.column().classes('w-full gap-2')
            
            def update_ui():
                current_files = fetch_files(base_path)
                
                # Simple hash/check to avoid re-rendering if no changes
                state_hash = hash(str([(f['path'], f['mtime'], f['size']) for f in current_files]))
                if page_state['last_hash'] == state_hash:
                    return
                page_state['last_hash'] = state_hash
                
                container.clear()
                with container:
                    if not current_files:
                        ui.label(f'No anomalies detected. Watching: {base_path}').classes('text-gray-500 italic p-4')
                        return
                        
                    for f in current_files:
                        with ui.expansion(f"{f['name']} ({f['mtime_str']})", icon='warning').classes('w-full bg-white border rounded shadow-sm'):
                            ui.label(f"Path: {f['rel_path']} | Size: {f['size']/1024:.1f} KB").classes('text-xs text-gray-400 mb-2')
                            render_file_content(f)
            
            # Initial render
            update_ui()
            
            # Poll every 3 seconds for file changes
            ui.timer(3.0, update_ui)
