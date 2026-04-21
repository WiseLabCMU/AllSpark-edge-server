from nicegui import app, ui
import aiohttp
import asyncio
import json
from theme import menu, get_local_ip
from pages.settings import load_config, get_edge_base_url

def create_page():
    edge_base_url = get_edge_base_url()

    @ui.page('/clients')
    def clients_page():
        with menu('Mobile Client Connections'):
            ip = get_local_ip()
            server_url = f"ws://{ip}:8080"

            with ui.row().classes('w-full gap-8'):
                with ui.card().classes('items-center p-6 w-1/3'):
                    ui.label('Server Pairing').classes('text-xl font-bold mb-4 text-gray-800')
                    ui.label('Scan this QR code with the AllSpark app:').classes('text-sm text-gray-600 text-center mb-4')

                    # Generate a QR code using a public API for the prototype
                    ui.image(f'https://api.qrserver.com/v1/create-qr-code/?size=150x150&data={server_url}').classes('w-32 h-32')
                    ui.label(server_url).classes('mt-4 font-mono font-bold bg-gray-100 p-2 rounded max-w-full break-all text-center')

                with ui.column().classes('w-2/3 gap-4'):
                    ui.label('Active Connections').classes('text-xl font-bold text-gray-800')

                    cards_container = ui.column().classes('w-full gap-2')

                    
                    client_ui_refs = {}
                    last_updated_label = ui.label('Last updated: Never').classes('text-gray-400 text-sm mt-4')

                    async def fetch_and_render_clients():
                        import datetime
                        try:
                            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                                async with session.get(f'{edge_base_url}/api/status', timeout=2) as resp:
                                    if resp.status == 200:
                                        data = await resp.json()
                                        clients_list = data.get('connections', [])
                                        if not clients_list and 'clients' in data:
                                            clients_list = data['clients']
                                        elif not clients_list and 'connectedClients' in data:
                                            clients_list = data['connectedClients']
                                        
                                        # Update existing or create new
                                        current_ids = []
                                        for c in clients_list:
                                            cid = c.get('id', 'Unknown')
                                            current_ids.append(cid)
                                            if cid not in client_ui_refs:
                                                create_client_card(c)
                                            else:
                                                file_str = f"Latest File: {c['lastFilename']}" if c.get("lastFilename") else "No files received yet"
                                                client_ui_refs[cid]['file_status'].set_text(file_str)
                                                
                                        # Remove disconnected
                                        for cid in list(client_ui_refs.keys()):
                                            if cid not in current_ids:
                                                client_ui_refs[cid]['card'].delete()
                                                del client_ui_refs[cid]
                                                
                                        now_str = datetime.datetime.now().strftime("%I:%M:%S %p")
                                        last_updated_label.set_text(f"Last updated: {now_str}")
                                    else:
                                        last_updated_label.set_text(f"Last updated: Error {resp.status}")
                        except Exception as e:
                            last_updated_label.set_text("Last updated: Server Unreachable")

                    async def request_upload(client_id, start_input, end_input):
                        try:
                            import datetime
                            import time
                            
                            s_val = start_input.value
                            e_val = end_input.value
                            
                            if not s_val or not e_val:
                                ui.notify('Please fill both Start and End times', type='warning')
                                return
                                
                            try:
                                s_dt = datetime.datetime.strptime(s_val, "%Y-%m-%dT%H:%M:%S")
                                e_dt = datetime.datetime.strptime(e_val, "%Y-%m-%dT%H:%M:%S")
                                s_ts = s_dt.timestamp()
                                e_ts = e_dt.timestamp()
                            except ValueError:
                                ui.notify('Invalid datetime format', type='warning')
                                return

                            payload = {"command": "uploadTimeRange", "startTime": s_ts, "endTime": e_ts}
                            
                            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                                async with session.post(f'{edge_base_url}/api/command/{client_id}', json=payload) as resp:
                                    if resp.status == 200:
                                        ui.notify(f'Upload requested for {client_id[:4]}', type='positive')
                                    else:
                                        ui.notify(f'Failed to send command: HTTP {resp.status}', type='negative')
                        except Exception as e:
                            ui.notify(f'Error sending command: {e}', type='negative')

                    def create_client_card(c):
                        client_id = c.get('id', 'Unknown')
                        c_type = c.get('type', 'Client')
                        client_name = c.get('clientName', f'{c_type}')
                        
                        import datetime
                        
                        with cards_container:
                            card = ui.card().classes('w-full border shadow-sm mb-2')
                            with card:
                                with ui.row().classes('w-full justify-between items-start'):
                                    with ui.column().classes('gap-0 w-full'):
                                        with ui.row().classes('items-end gap-1'):
                                            ui.label(f'{client_name}').classes('font-bold text-lg')
                                            ui.label(f'({client_id})').classes('text-sm text-gray-500 pb-0.5')
                                        
                                        file_str = f"Latest File: {c['lastFilename']}" if c.get("lastFilename") else "No files received yet"
                                        file_status_label = ui.label(file_str).classes('text-sm text-gray-500 mt-1 mb-2')
                                        
                                        def set_time(target_input, delta_mins=0, is_now=False):
                                            now = datetime.datetime.now()
                                            t = now if is_now else now - datetime.timedelta(minutes=delta_mins)
                                            target_input.value = t.strftime("%Y-%m-%dT%H:%M:%S")

                                        with ui.row().classes('items-center gap-2 mt-2'):
                                            ui.label('Start Time:').classes('font-bold w-20')
                                            start_input = ui.input().props('type="datetime-local" step="1"').classes('w-56 border rounded px-2')
                                            ui.button('Last 1 min', on_click=lambda: set_time(start_input, 1)).classes('text-gray-700 bg-gray-200').props('outline size=sm')
                                            ui.button('Last 5 mins', on_click=lambda: set_time(start_input, 5)).classes('text-gray-700 bg-gray-200').props('outline size=sm')
                                            ui.button('Last 1 hour', on_click=lambda: set_time(start_input, 60)).classes('text-gray-700 bg-gray-200').props('outline size=sm')
                                        
                                        with ui.row().classes('items-center gap-2 mt-2'):
                                            ui.label('End Time:').classes('font-bold w-20')
                                            end_input = ui.input().props('type="datetime-local" step="1"').classes('w-56 border rounded px-2')
                                            ui.button('Now', on_click=lambda: set_time(end_input, 0, True)).classes('text-gray-700 bg-gray-200').props('outline size=sm')
                                        
                                        def make_upload_handler(ci, s, e):
                                            async def handler():
                                                await request_upload(ci, s, e)
                                            return handler

                                        ui.button('Request Upload Time Range', on_click=make_upload_handler(client_id, start_input, end_input)).classes('bg-blue-500 text-white mt-4 capitalize')
                            
                            client_ui_refs[client_id] = {
                                'card': card,
                                'file_status': file_status_label,
                                'start': start_input,
                                'end': end_input
                            }

                    # Initialisation
                    ui.timer(2.0, fetch_and_render_clients)
