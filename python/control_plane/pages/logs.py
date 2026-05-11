from nicegui import ui, app
import os
import time
from pathlib import Path
from theme import menu
from pages.settings import load_config, get_edge_base_url

def get_log_paths():
    """
    Resolve the set of root directories the Logs page should watch.

    After the folder restructure, anomaly artefacts can live in *either*:
      - uploads/agent_responses/Anomaly_YYYY-MM-DD/<HHMMSS_uuid>/...   (legacy)
      - uploads/anomaly_<ISO-TS>/agent_responses/<HHMMSS_uuid>/...     (new, per-anomaly)
      - uploads/anomaly_<ISO-TS>/{video_clips,machine_anomaly_data}/...
    and DataCapture rig output now lands under several subfolders of logs/:
      - logs/data/datacapture-rig/      (config: rigLogs)
      - logs/data/kafka_logs/
      - logs/data/video_logs/
      - logs/data/agent_responses/
      - logs/anomalies/                  (config: anomalyLogs)

    To capture everything without enumerating each subdir, we watch the two
    top-level roots (uploads/ and logs/) in addition to the explicit config
    paths, then dedupe so overlapping roots aren't crawled twice.
    """
    full_config = load_config()
    cp_config = full_config.get('control_plane', {})
    mc_config = full_config.get('mobile_client', {})

    client_uploads_path = mc_config.get('clientUploadsPath', 'uploads/mobile_clients/')
    agent_response_path = mc_config.get('agentResponsePath', 'uploads/agent_responses/')
    upload_root = mc_config.get('uploadPath', 'uploads/')
    log_paths_cfg = cp_config.get('logPaths', {}) or {}
    rig_logs_path = log_paths_cfg.get('rigLogs', 'logs/data/datacapture-rig/')
    anomaly_logs_path = log_paths_cfg.get('anomalyLogs', 'logs/anomalies/')

    base_dir = os.path.dirname(__file__)
    edge_root = os.path.abspath(os.path.join(base_dir, '..', '..', '..'))

    candidates = [
        os.path.abspath(os.path.join(edge_root, client_uploads_path)),
        os.path.abspath(os.path.join(edge_root, agent_response_path)),
        os.path.abspath(os.path.join(edge_root, upload_root)),  # catches uploads/anomaly_*/
        os.path.abspath(os.path.join(edge_root, rig_logs_path)),
        os.path.abspath(os.path.join(edge_root, anomaly_logs_path)),
        os.path.abspath(os.path.join(edge_root, 'logs')),  # catches logs/data/* and logs/anomalies/*
    ]

    # Dedupe: drop any path that is a (proper) descendant of another candidate
    # — Path.rglob on the ancestor will already cover it.
    unique: list = []
    candidates_norm = sorted(set(candidates), key=len)
    for c in candidates_norm:
        if not any(
            c != other and c.startswith(other.rstrip(os.sep) + os.sep)
            for other in candidates_norm
        ):
            unique.append(c)
    return unique

def fetch_log_files(paths):
    files_dict = {}

    for base_path in paths:
        if not os.path.exists(base_path):
            continue

        for p in Path(base_path).rglob('*'):
            if p.is_file():
                abs_str = str(p)
                if abs_str in files_dict:
                    continue
                try:
                    stat = p.stat()
                    # Relative to the edge_server root for cleaner UI
                    # e.g. logs/data/datacapture-rig/foo.mp4
                    try:
                        edge_server_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
                        rel_path = str(p.relative_to(edge_server_root))
                    except ValueError:
                        rel_path = str(p)

                    files_dict[abs_str] = {
                        'path': rel_path,
                        'abs_path': str(p), # Used for local references if needed
                        'ext': p.suffix.lower() if p.suffix else 'unknown',
                        'size': round(stat.st_size / 1024, 2), # KB
                        'mtime': stat.st_mtime,
                        'date': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stat.st_mtime))
                    }
                except Exception:
                    pass
    return list(files_dict.values())

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

        # Initialize defaults for user storage if empty
        defaults = {
            'logs_search': '',
            'logs_filter_rig': True,
            'logs_filter_mobile': True,
            'logs_filter_agent': True,
            'logs_filter_video': True,
            'logs_filter_audio': True,
            'logs_filter_image': True,
            'logs_filter_text': True,
            'logs_filter_hidden': False,
            'logs_filter_source_all': False,
            'logs_filter_type_all': False,
        }
        for k, v in defaults.items():
            if k not in app.storage.user:
                app.storage.user[k] = v

        with menu('Logs & Data Capture'):
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label('Aggregate System Records').classes('text-xl font-bold text-gray-800')
                search = ui.input('Search Path/Type', value=app.storage.user.get('logs_search')).classes('w-64').props('dense outlined clearable')

            full_config = load_config()
            cp_config = full_config.get('control_plane', {})
            mc_config = full_config.get('mobile_client', {})

            client_uploads_path = mc_config.get('clientUploadsPath', 'uploads/mobile_clients/')
            agent_response_path = mc_config.get('agentResponsePath', 'uploads/agent_responses/')
            rig_logs_path = cp_config.get('logPaths', {}).get('rigLogs', 'logs/data/datacapture-rig/')

            base_dir = os.path.dirname(__file__)
            edge_root = os.path.abspath(os.path.join(base_dir, '..', '..', '..'))
            abs_client_uploads = os.path.abspath(os.path.join(edge_root, client_uploads_path))
            abs_agent_response = os.path.abspath(os.path.join(edge_root, agent_response_path))
            abs_rig_logs = os.path.abspath(os.path.join(edge_root, rig_logs_path))
            abs_uploads_root = os.path.abspath(os.path.join(edge_root, 'uploads'))
            abs_logs_root = os.path.abspath(os.path.join(edge_root, 'logs'))

            with ui.row().classes('w-full gap-4 items-center mb-2'):
                ui.label('Filter Source:').classes('font-semibold')
                filter_source_all = ui.checkbox('All', value=app.storage.user.get('logs_filter_source_all')).classes('font-bold')
                filter_rig = ui.checkbox('DataCapture / Rig Logs', value=app.storage.user.get('logs_filter_rig')).tooltip(
                    f"Includes:\n  • {abs_rig_logs}\n  • {abs_logs_root} (data/, anomalies/)"
                )
                filter_mobile = ui.checkbox('Mobile Client', value=app.storage.user.get('logs_filter_mobile')).tooltip(
                    f"Watching: {abs_client_uploads}"
                )
                filter_agent = ui.checkbox('Agent / Anomalies', value=app.storage.user.get('logs_filter_agent')).tooltip(
                    f"Includes:\n  • {abs_agent_response}\n  • {abs_uploads_root}/anomaly_*/  (per-anomaly folders)"
                )

            with ui.row().classes('w-full gap-4 items-center mb-4'):
                ui.label('Filter Type:').classes('font-semibold')
                filter_type_all = ui.checkbox('All', value=app.storage.user.get('logs_filter_type_all')).classes('font-bold')
                filter_video = ui.checkbox('Video', value=app.storage.user.get('logs_filter_video'))
                filter_audio = ui.checkbox('Audio', value=app.storage.user.get('logs_filter_audio'))
                filter_image = ui.checkbox('Image', value=app.storage.user.get('logs_filter_image'))
                filter_text = ui.checkbox('Text', value=app.storage.user.get('logs_filter_text'))
                filter_hidden = ui.checkbox('Hidden (.*)', value=app.storage.user.get('logs_filter_hidden')).classes('ml-4')

            def toggle_all_sources(e):
                val = e.value
                filter_rig.value = val
                filter_mobile.value = val
                filter_agent.value = val

            def toggle_all_types(e):
                val = e.value
                filter_video.value = val
                filter_audio.value = val
                filter_image.value = val
                filter_text.value = val

            filter_source_all.on_value_change(toggle_all_sources)
            filter_type_all.on_value_change(toggle_all_types)

            columns = [
                {'name': 'action', 'label': 'Review', 'field': 'action', 'align': 'center'},
                {'name': 'path', 'label': 'File Path', 'field': 'path', 'sortable': True, 'align': 'left', 'style': 'max-width: 40vw; white-space: normal; word-break: break-all;'},
                {'name': 'ext', 'label': 'Type', 'field': 'ext', 'sortable': True, 'align': 'left'},
                {'name': 'size', 'label': 'Size (KB)', 'field': 'size', 'sortable': True},
                {'name': 'date', 'label': 'Date Modified', 'field': 'date', 'sortable': True, 'align': 'left'}
            ]

            # Create table with pagination
            table = ui.table(columns=columns, rows=[], row_key='path', pagination=10, selection='multiple').classes('w-full')

            with ui.row().classes('w-full items-center mt-2 gap-4'):
                anomaly_btn = ui.button('New Investigation', icon='travel_explore').classes('bg-blue-600 text-white shadow-md')
                update_inv_btn = ui.button('Update Investigation', icon='update').classes('bg-indigo-600 text-white shadow-md')
                delete_btn = ui.button('Delete Selected', icon='delete').classes('bg-red-600 text-white shadow-md')

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
                    elif ext in ['.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg']:
                        for p in paths:
                            if abs_path.startswith(p):
                                rel_to_media = os.path.relpath(abs_path, p)
                                safe_name = os.path.basename(p.strip('/')) or 'logs'
                                ui.audio(f'/log_media_{safe_name}/{rel_to_media}').classes('w-full mt-10')
                                break
                    elif ext in ['.jpg', '.png', '.jpeg']:
                        for p in paths:
                            if abs_path.startswith(p):
                                rel_to_media = os.path.relpath(abs_path, p)
                                safe_name = os.path.basename(p.strip('/')) or 'logs'
                                ui.image(f'/log_media_{safe_name}/{rel_to_media}').classes('w-full bg-black').style('max-height: 75vh; object-fit: contain;')
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

            def on_new_investigation():
                selected = table.selected
                if not selected:
                    ui.notify("Please select files first.", type="warning")
                    return

                videos = [row for row in selected if row['ext'] in ['.mp4', '.webm', '.mkv', '.mov', '.ts', '.avi']]
                logs = [row for row in selected if row['ext'] in ['.log', '.json', '.txt', '.csv', '.yaml']]

                if not videos:
                    ui.notify("A video clip must be selected to generate an anomaly.", type="warning")
                    return

                video = videos[0]
                clip_path_val = os.path.basename(video['abs_path'])

                import datetime
                dt_now = datetime.datetime.now()
                inv_dir_name = f"Anomaly_{dt_now.strftime('%Y-%m-%d_%H%M%S')}"
                
                # Use agentResponsePath instead of anomalyLogs
                ar_path = load_config().get('mobile_client', {}).get('agentResponsePath', 'uploads/agent_responses/')
                abs_ar_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', ar_path))
                new_inv_path = os.path.join(abs_ar_path, inv_dir_name)
                
                dt_now_str = dt_now.replace(microsecond=0).isoformat()

                def submit_anomaly(error_val, expected_top, clip_time, anom_time):
                    import aiohttp
                    import shutil
                    edge_base_url = get_edge_base_url()

                    video_clips_dir = os.path.join(new_inv_path, 'video_clips')
                    machine_data_dir = os.path.join(new_inv_path, 'machine_anomaly_data')
                    os.makedirs(video_clips_dir, exist_ok=True)
                    os.makedirs(machine_data_dir, exist_ok=True)
                    
                    copied_log_path = ""
                    for s in selected:
                        src = s['abs_path']
                        video_exts = ['.mp4', '.webm', '.mkv', '.mov', '.ts', '.avi']
                        sub_dir = video_clips_dir if s['ext'] in video_exts else machine_data_dir
                        dst = os.path.join(sub_dir, os.path.basename(src))
                        try:
                            shutil.copy2(src, dst)
                            if s in logs and not copied_log_path:
                                copied_log_path = dst
                        except Exception as e:
                            ui.notify(f"Error copying {os.path.basename(src)}: {e}", type="negative")

                    payload = {
                        "clip_path": clip_path_val,
                        "log_path": copied_log_path,
                        "anomaly_time": anom_time,
                        "clip_start_time": clip_time,
                        "error": error_val,
                        "expected_topic": expected_top,
                        "video_storage_path": video_clips_dir,
                        "mqtt_clip_messages": []
                    }

                    async def do_post():
                        try:
                            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as http:
                                async with http.post(f"{edge_base_url}/api/agent/analyze", json=payload, timeout=aiohttp.ClientTimeout(total=1000)) as resp:
                                    res = await resp.json(content_type=None)
                            if res.get("success"):
                                ui.notify(f"Investigation submitted. ID: {res.get('request_id')} - Check Agent page.", type="positive", timeout=5000)
                                ui.timer(1.0, lambda: ui.open('/agent'), once=True)
                            else:
                                ui.notify(f"Submission failed: {res.get('error_message')}", type="negative", timeout=5000)
                        except Exception as e:
                            ui.notify(f"Request error: {e}", type="negative", timeout=5000)

                    dialog.close()
                    ui.notify("Copying files and dispatching to agent framework...", type="info")
                    ui.timer(0.1, do_post, once=True)
                    table.selected.clear()
                    update_ui(force=True)

                with ui.dialog() as dialog, ui.card().classes('w-full max-w-2xl flex-col'):
                    ui.label("New Investigation").classes('text-xl font-bold')
                    ui.label(f"Copying {len(selected)} files to: {inv_dir_name}").classes('text-sm text-gray-500 font-mono break-all')

                    err_input = ui.input("Error Description", value="Manual anomaly triggered by operator").classes('w-full')
                    topic_input = ui.input("Expected Topic", value="allspark/status").classes('w-full')
                    anom_time_input = ui.input("Anomaly Time", value=dt_now_str).classes('w-full')
                    clip_time_input = ui.input("Clip Start Time", value=dt_now_str).classes('w-full')

                    with ui.row().classes('w-full justify-end mt-4 gap-2'):
                        ui.button('Cancel', on_click=dialog.close).props('flat')
                        ui.button('Submit to Agent', on_click=lambda: submit_anomaly(
                            err_input.value,
                            topic_input.value,
                            clip_time_input.value,
                            anom_time_input.value
                        )).classes('bg-blue-600 text-white')
                dialog.open()

            anomaly_btn.on_click(on_new_investigation)

            def on_update_investigation():
                selected = table.selected
                if not selected:
                    ui.notify("Please select files first.", type="warning")
                    return

                ar_path = load_config().get('mobile_client', {}).get('agentResponsePath', 'uploads/agent_responses/')
                abs_ar_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', ar_path))
                
                os.makedirs(abs_ar_path, exist_ok=True)
                existing_dirs = [d for d in os.listdir(abs_ar_path) if os.path.isdir(os.path.join(abs_ar_path, d))]
                
                if not existing_dirs:
                    ui.notify("No existing investigations found.", type="warning")
                    return
                    
                def do_update(target_dir):
                    import shutil
                    dest_path = os.path.join(abs_ar_path, target_dir)
                    video_clips_dir = os.path.join(dest_path, 'video_clips')
                    machine_data_dir = os.path.join(dest_path, 'machine_anomaly_data')
                    os.makedirs(video_clips_dir, exist_ok=True)
                    os.makedirs(machine_data_dir, exist_ok=True)
                    for s in selected:
                        src = s['abs_path']
                        video_exts = ['.mp4', '.webm', '.mkv', '.mov', '.ts', '.avi']
                        sub_dir = video_clips_dir if s['ext'] in video_exts else machine_data_dir
                        dst = os.path.join(sub_dir, os.path.basename(src))
                        try:
                            shutil.copy2(src, dst)
                        except Exception as e:
                            ui.notify(f"Error copying {os.path.basename(src)}: {e}", type="negative")
                    ui.notify(f"Copied {len(selected)} files to {target_dir}.", type="positive")
                    dialog.close()
                    table.selected.clear()
                    update_ui(force=True)
                    ui.timer(1.0, lambda: ui.open('/agent'), once=True)

                with ui.dialog() as dialog, ui.card().classes('w-full max-w-xl flex-col'):
                    ui.label("Update Investigation").classes('text-xl font-bold')
                    ui.label(f"Select an existing investigation directory to copy {len(selected)} files into:").classes('text-sm text-gray-500 mb-2')
                    dir_select = ui.select(options=existing_dirs, value=existing_dirs[0]).classes('w-full mb-4')
                    
                    with ui.row().classes('w-full justify-end mt-4 gap-2'):
                        ui.button('Cancel', on_click=dialog.close).props('flat')
                        ui.button('Update Directory', on_click=lambda: do_update(dir_select.value)).classes('bg-indigo-600 text-white')
                dialog.open()

            update_inv_btn.on_click(on_update_investigation)

            def on_delete_selected():
                selected = table.selected
                if not selected:
                    ui.notify("Please select files first.", type="warning")
                    return

                n = len(selected)

                def proceed_delete():
                    deleted_count = 0
                    for row in selected:
                        try:
                            abs_path = row.get('abs_path')
                            if abs_path and os.path.exists(abs_path):
                                os.remove(abs_path)
                                deleted_count += 1
                        except Exception as e:
                            ui.notify(f"Error deleting {row['path']}: {e}", type="negative")
                    dialog.close()
                    table.selected.clear()
                    update_ui(force=True)
                    ui.notify(f"Deleted {deleted_count} files.", type="positive", timeout=5000)

                with ui.dialog() as dialog, ui.card().classes('p-6'):
                    ui.label(f"Delete these {n} files?").classes('text-lg font-bold')
                    ui.label("This action cannot be undone.").classes('text-sm text-gray-500 mb-4')
                    with ui.row().classes('w-full justify-end gap-2'):
                        ui.button('Cancel', on_click=dialog.close).props('flat')
                        ui.button('Confirm Delete', on_click=proceed_delete).classes('bg-red-600 text-white')
                dialog.open()

            delete_btn.on_click(on_delete_selected)

            def update_ui(force=False):
                # Ensure storage reflects current UI selections
                app.storage.user['logs_search'] = search.value or ''
                app.storage.user['logs_filter_rig'] = filter_rig.value
                app.storage.user['logs_filter_mobile'] = filter_mobile.value
                app.storage.user['logs_filter_agent'] = filter_agent.value
                app.storage.user['logs_filter_video'] = filter_video.value
                app.storage.user['logs_filter_audio'] = filter_audio.value
                app.storage.user['logs_filter_image'] = filter_image.value
                app.storage.user['logs_filter_text'] = filter_text.value
                app.storage.user['logs_filter_hidden'] = filter_hidden.value
                app.storage.user['logs_filter_source_all'] = filter_source_all.value
                app.storage.user['logs_filter_type_all'] = filter_type_all.value

                current_files = fetch_log_files(paths)

                # Simple hash/check to avoid updating DOM if no changes
                search_val = search.value or ''
                state_hash = hash(str([(f['path'], f['mtime'], f['size']) for f in current_files]) + search_val)
                if not force and page_state['last_hash'] == state_hash:
                    return
                page_state['last_hash'] = state_hash

                # Sort reversed by modified time dynamically on updates
                sorted_files = sorted(current_files, key=lambda x: x['mtime'], reverse=True)

                # Keep original filter logic intact

                valid_exts = set()
                if filter_video.value: valid_exts.update(['.mp4', '.mkv', '.avi', '.webm', '.ts', '.mov', '.quic'])
                if filter_audio.value: valid_exts.update(['.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg'])
                if filter_image.value: valid_exts.update(['.jpg', '.jpeg', '.png', '.gif', '.svg'])
                if filter_text.value: valid_exts.update(['.txt', '.json', '.yaml', '.csv', '.log', '.md'])

                filtered_files = []
                for f in sorted_files:
                    # Hidden filter
                    if not filter_hidden.value and any(part.startswith('.') for part in Path(f['path']).parts):
                        continue

                    # Source filter — categorise each file by its path.
                    # A single file can belong to multiple sources (e.g. an
                    # uploads/anomaly_*/agent_responses/.../video.mp4 is both
                    # "agent" and arguably "rig" data); we hide it only when
                    # ALL of its categories are unchecked.
                    path_lower = f['path'].lower().replace('\\', '/')
                    is_mobile = 'mobile_clients' in path_lower
                    is_agent = (
                        'agent_responses' in path_lower
                        or '/anomaly_' in path_lower
                        or path_lower.startswith('uploads/anomaly_')
                        or 'anomalies/' in path_lower
                    )
                    is_rig = (
                        'datacapture' in path_lower
                        or path_lower.startswith('logs/')
                        or '/logs/' in path_lower
                    ) and not is_agent and not is_mobile

                    # If the file matched no category at all, treat it as rig
                    # so it isn't silently dropped after the restructure.
                    if not (is_mobile or is_agent or is_rig):
                        is_rig = True

                    show = (
                        (is_mobile and filter_mobile.value)
                        or (is_agent and filter_agent.value)
                        or (is_rig and filter_rig.value)
                    )
                    if not show:
                        continue

                    # Ext filter
                    if valid_exts and f['ext'] not in valid_exts and f['ext'] != 'unknown':
                        # Allow unknown to sneak through? Let's just strict filter if ANY checkbox is checked
                        if filter_video.value or filter_audio.value or filter_image.value or filter_text.value:
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
            filter_audio.on_value_change(lambda _: update_ui(force=True))
            filter_image.on_value_change(lambda _: update_ui(force=True))
            filter_text.on_value_change(lambda _: update_ui(force=True))
            filter_hidden.on_value_change(lambda _: update_ui(force=True))
            filter_rig.on_value_change(lambda _: update_ui(force=True))
            filter_mobile.on_value_change(lambda _: update_ui(force=True))
            filter_agent.on_value_change(lambda _: update_ui(force=True))

            # Ensure directories exist
            for p in paths:
                os.makedirs(p, exist_ok=True)

            # Render initially and poll
            update_ui()
            _logs_timer = ui.timer(3.0, update_ui)
            def _safe_update_ui(_t=_logs_timer):
                try:
                    update_ui()
                except RuntimeError:
                    _t.cancel()
            _logs_timer.callback = _safe_update_ui
