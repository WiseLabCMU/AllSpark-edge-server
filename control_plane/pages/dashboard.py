from nicegui import app, ui
import json
import time
import os
import glob
import requests
from theme import menu
from pages.settings import load_config

anomalies = {}
active_connections = []

def fetch_anomalies():
    full_config = load_config()
    cp_config = full_config.get('control_plane', {})
    anomaly_path = cp_config.get('logPaths', {}).get('anomalyLogs', 'logs/anomalies/')
    abs_anomaly_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', anomaly_path))
    
    # Read JSON files in this directory
    new_anomalies = {}
    if os.path.exists(abs_anomaly_path):
        for file in glob.glob(os.path.join(abs_anomaly_path, '*.json')):
            try:
                with open(file, 'r') as f:
                    data = json.load(f)
                    
                    # Ensure source exists using filename or data
                    source = data.get('source', 'unknown')
                    if source == 'unknown':
                        # Try to parse from filename: anomaly_rig1_timestamp.json
                        basename = os.path.basename(file)
                        if '_' in basename:
                            source = basename.split('_')[1]

                    if source not in new_anomalies:
                        new_anomalies[source] = []
                    new_anomalies[source].append({
                        "time": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(file))),
                        "data": data,
                        "mtime": os.path.getmtime(file)
                    })
            except Exception as e:
                pass
    
    # Sort and keep latest 10
    global anomalies
    anomalies.clear()
    for source, events in new_anomalies.items():
        sorted_events = sorted(events, key=lambda x: x['mtime'])
        anomalies[source] = sorted_events[-10:]


def fetch_connections():
    full_config = load_config()
    mc_config = full_config.get('mobile_client', {})
    edge_port = mc_config.get('port', 8080)
    try:
        resp = requests.get(f'http://127.0.0.1:{edge_port}/api/status', timeout=1)
        if resp.status_code == 200:
            data = resp.json()
            global active_connections
            active_connections = data.get('connections', [])
    except Exception:
        pass


def create_page():
    @ui.page('/')
    def dashboard_page():
        val1 = load_config()
        cp_config = val1.get('control_plane', {})
        anomaly_path = cp_config.get('logPaths', {}).get('anomalyLogs', 'logs/anomalies/')

        with menu('Dashboard'):
            # Fetch connections layout
            ui.label('Active Mobile Connections').classes('text-xl font-semibold mb-2 mt-4')
            conn_container = ui.list().classes('w-full border rounded p-2 mb-6')

            def render_connections():
                fetch_connections()
                conn_container.clear()
                with conn_container:
                    if not active_connections:
                        ui.label('No active connections. Wait for mobile clients to pair...').classes('text-gray-500 italic p-4')
                        return
                    for conn in active_connections:
                        ui.label(f'Active Device: {conn.get("clientName", "Unknown")} (ID: {conn.get("id")})').classes('font-bold')
                        if conn.get("lastFilename"):
                            ui.label(f'  Last Upload: {conn["lastFilename"]} ({conn["lastFilesize"]} bytes)').classes('text-sm text-gray-600')

            ui.timer(2.0, render_connections)

            ui.label('System Anomalies').classes('text-xl font-semibold mb-2')
            container = ui.list().classes('w-full border rounded p-2')
            
            def render_anomalies():
                fetch_anomalies()
                container.clear()
                with container:
                    if not anomalies:
                        ui.label(f'No anomalies detected yet. Watching: {anomaly_path}').classes('text-gray-500 italic p-4')
                        return
                        
                    for source, events in anomalies.items():
                        title = f'Rig: {source} ({len(events)} events)'
                        with ui.expansion(title, icon='warning').classes('w-full bg-gray-50 mb-2'):
                            for ev in reversed(events):
                                data_str = json.dumps(ev["data"])
                                ui.label(f'Event: {ev["time"]} - {data_str}').classes('font-mono text-sm mb-1')
                            ui.button('Investigate via Agent', on_click=lambda s=source: ui.navigate.to(f'/agent?source={s}')).classes('mt-2 bg-blue-500 text-white')

            ui.timer(3.0, render_anomalies)

