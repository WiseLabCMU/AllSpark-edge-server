from nicegui import ui, app
import os
import time
from pathlib import Path
from theme import menu
from pages.settings import load_config

def get_log_paths():
    full_config = load_config()
    cp_config = full_config.get('control_plane', {})
    mc_config = full_config.get('mobile_client', {})
    
    # rigLogs default
    rig_logs_path = cp_config.get('logPaths', {}).get('rigLogs', 'logs/data/datacapture-rig/')
    
    # mobile client uploadPath default
    upload_path = mc_config.get('uploadPath', 'logs/data/mobile-client')
    
    base_dir = os.path.dirname(__file__)
    abs_rig_logs = os.path.abspath(os.path.join(base_dir, '..', '..', rig_logs_path))
    abs_upload_logs = os.path.abspath(os.path.join(base_dir, '..', '..', upload_path))
    
    return [abs_rig_logs, abs_upload_logs]

def fetch_log_files(paths):
    files_data = []
    
    for base_path in paths:
        if not os.path.exists(base_path):
            continue
            
        for p in Path(base_path).rglob('*'):
            if p.is_file():
                try:
                    stat = p.stat()
                    # Relative to the edge_server root for cleaner UI
                    # e.g. logs/data/datacapture-rig/foo.mp4
                    try:
                        edge_server_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
                        rel_path = str(p.relative_to(edge_server_root))
                    except ValueError:
                        rel_path = str(p)
                        
                    files_data.append({
                        'path': rel_path,
                        'abs_path': str(p), # Used for local references if needed
                        'ext': p.suffix.lower() if p.suffix else 'unknown',
                        'size': round(stat.st_size / 1024, 2), # KB
                        'mtime': stat.st_mtime,
                        'date': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))
                    })
                except Exception:
                    pass
    return files_data

def create_page():
    @ui.page('/logs')
    def logs_page():
        paths = get_log_paths()
        page_state = {'last_hash': None, 'search_val': ''}
        
        # We must add media folder routes to serve the files
        for p in paths:
            os.makedirs(p, exist_ok=True)
            # Use safe unique names for routes
            safe_name = os.path.basename(p.strip('/')) or 'logs'
            app.add_media_files(f'/log_media_{safe_name}', p)
            
        with menu('Logs & Data Capture'):
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label('Aggregate System Records').classes('text-xl font-bold text-gray-800')
                search = ui.input('Search Path/Type').classes('w-64').props('dense outlined clearable')

            with ui.row().classes('w-full gap-4 items-center mb-4'):
                ui.label('Filter Source:')
                filter_rig = ui.checkbox('DataCapture', value=True)
                filter_mobile = ui.checkbox('Mobile Client', value=True)
                
                ui.label('Type:').classes('ml-4')
                filter_video = ui.checkbox('Video', value=True)
                filter_image = ui.checkbox('Image', value=True)
                filter_text = ui.checkbox('Text', value=True)
                filter_audio = ui.checkbox('Audio', value=True)
                filter_depth = ui.checkbox('Depth', value=True)
                filter_hidden = ui.checkbox('Hide Hidden (.*)', value=True).classes('ml-4')
            
            columns = [
                {'name': 'action', 'label': 'Review', 'field': 'action', 'align': 'center'},
                {'name': 'path', 'label': 'File Path', 'field': 'path', 'sortable': True, 'align': 'left', 'style': 'max-width: 40vw; white-space: normal; word-break: break-all;'},
                {'name': 'ext', 'label': 'Type', 'field': 'ext', 'sortable': True, 'align': 'left'},
                {'name': 'size', 'label': 'Size (KB)', 'field': 'size', 'sortable': True},
                {'name': 'date', 'label': 'Date Modified', 'field': 'date', 'sortable': True, 'align': 'left'}
            ]
            
            # Create table with pagination
            table = ui.table(columns=columns, rows=[], row_key='path', pagination=10).classes('w-full')
            
            # Add action button slot
            table.add_slot('body-cell-action', '''
                <q-td :props="props">
                    <q-btn icon="visibility" @click="$parent.$emit('review', props.row)" flat dense color="primary" />
                </q-td>
            ''')
            
            def open_review_dialog(row):
                ext = row.get('ext', '')
                abs_path = row.get('abs_path', '')
                
                with ui.dialog() as dialog, ui.card().classes('w-full max-w-5xl h-[85vh] flex-col overflow-hidden'):
                    with ui.row().classes('w-full justify-between items-center mb-2 shrink-0'):
                        ui.label(os.path.basename(row['path'])).classes('font-bold text-lg')
                        ui.button(icon='close', on_click=dialog.close).props('flat round dense')
                    
                    if ext in ['.mp4', '.webm']:
                        # Calculate routed media URL
                        for p in paths:
                            if abs_path.startswith(p):
                                rel_to_media = os.path.relpath(abs_path, p)
                                safe_name = os.path.basename(p.strip('/')) or 'logs'
                                ui.video(f'/log_media_{safe_name}/{rel_to_media}').classes('w-full bg-black').style('max-height: 75vh; object-fit: contain;')
                                break
                    elif ext in ['.jpg', '.png', '.jpeg']:
                        for p in paths:
                            if abs_path.startswith(p):
                                rel_to_media = os.path.relpath(abs_path, p)
                                safe_name = os.path.basename(p.strip('/')) or 'logs'
                                ui.image(f'/log_media_{safe_name}/{rel_to_media}').classes('w-full bg-black').style('max-height: 75vh; object-fit: contain;')
                                break
                    elif ext in ['.wav', '.mp3', '.m4a', '.aac', '.flac']:
                        for p in paths:
                            if abs_path.startswith(p):
                                rel_to_media = os.path.relpath(abs_path, p)
                                safe_name = os.path.basename(p.strip('/')) or 'logs'
                                ui.audio(f'/log_media_{safe_name}/{rel_to_media}').classes('w-full mt-4')
                                break
                    elif ext in ['.json', '.txt', '.yaml', '.csv', '.log']:
                        try:
                            with open(abs_path, 'r') as f:
                                content = f.read()
                                if len(content) > 250000:
                                    content = content[:250000] + "\n...[truncated]"
                                with ui.scroll_area().classes('w-full flex-1 border rounded p-2'):
                                    ui.label(content).classes('font-mono text-xs whitespace-pre-wrap')
                        except Exception as e:
                            ui.label(f'Error reading file: {e}')
                    else:
                        ui.label('Preview not available for this file type. Please download.').classes('italic text-gray-500')
                    
                dialog.open()
                
            table.on('review', lambda e: open_review_dialog(e.args))
            
            def update_ui(force=False):
                current_files = fetch_log_files(paths)
                
                # Simple hash/check to avoid updating DOM if no changes
                state_hash = hash(str([(f['path'], f['mtime'], f['size']) for f in current_files]) + search.value)
                if not force and page_state['last_hash'] == state_hash:
                    return
                page_state['last_hash'] = state_hash
                
                # Sort reversed by modified time dynamically on updates
                sorted_files = sorted(current_files, key=lambda x: x['mtime'], reverse=True)
                
                # Keep original filter logic intact
                
                valid_exts = set()
                if filter_video.value: valid_exts.update(['.mp4', '.mkv', '.avi', '.webm', '.ts', '.mov', '.quic'])
                if filter_image.value: valid_exts.update(['.jpg', '.jpeg', '.png', '.gif', '.svg'])
                if filter_text.value: valid_exts.update(['.txt', '.json', '.yaml', '.csv', '.log', '.md'])
                if filter_audio.value: valid_exts.update(['.wav', '.mp3', '.m4a', '.aac', '.flac'])
                if filter_depth.value: valid_exts.update(['.oni', '.bag', '.bin', '.depth', '.ply', '.pcd'])
                
                filtered_files = []
                for f in sorted_files:
                    # Hidden filter
                    if filter_hidden.value and any(part.startswith('.') for part in Path(f['path']).parts):
                        continue
                        
                    # Source filter
                    if not filter_rig.value and 'datacapture' in f['path'].lower(): continue
                    if not filter_mobile.value and 'mobile-client' in f['path'].lower(): continue
                    
                    # Ext filter
                    if valid_exts and f['ext'] not in valid_exts and f['ext'] != 'unknown':
                        # Allow unknown to sneak through? Let's just strict filter if ANY checkbox is checked
                        if filter_video.value or filter_image.value or filter_text.value or filter_audio.value or filter_depth.value:
                            continue
                            
                    # Text filter
                    filter_text_search = search.value.lower() if search.value else ''
                    if filter_text_search:
                        if filter_text_search not in f['path'].lower() and filter_text_search not in f['ext'].lower():
                            continue
                            
                    filtered_files.append(f)
                    
                table.rows = filtered_files
            
            # Bind events
            search.on_value_change(lambda _: update_ui(force=True))
            filter_video.on_value_change(lambda _: update_ui(force=True))
            filter_image.on_value_change(lambda _: update_ui(force=True))
            filter_text.on_value_change(lambda _: update_ui(force=True))
            filter_audio.on_value_change(lambda _: update_ui(force=True))
            filter_depth.on_value_change(lambda _: update_ui(force=True))
            filter_hidden.on_value_change(lambda _: update_ui(force=True))
            filter_rig.on_value_change(lambda _: update_ui(force=True))
            filter_mobile.on_value_change(lambda _: update_ui(force=True))
            
            # Ensure directories exist
            for p in paths:
                os.makedirs(p, exist_ok=True)
                
            # Render initially and poll
            update_ui()
            ui.timer(3.0, update_ui)
