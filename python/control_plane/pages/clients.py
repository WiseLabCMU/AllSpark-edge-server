from nicegui import ui
import aiohttp
from theme import menu, get_local_ip
from pages.settings import load_config

def create_page():
    @ui.page('/clients')
    def clients_page():
        config = load_config()
        edge_port = config.get('port', 8080)
        
        with menu('Mobile Clients & Pairing'):
            ip = get_local_ip()
            server_url = f"ws://{ip}:{edge_port}"
            
            with ui.row().classes('w-full gap-8'):
                with ui.card().classes('items-center p-6 w-1/3'):
                    ui.label('Server Pairing').classes('text-xl font-bold mb-4')
                    ui.label('Scan this QR code with the AllSpark app:').classes('text-sm text-gray-600 text-center mb-4')
                    
                    # Generate a QR code using a public API for the prototype
                    ui.image(f'https://api.qrserver.com/v1/create-qr-code/?size=150x150&data={server_url}').classes('w-32 h-32')
                    ui.label(server_url).classes('mt-4 font-mono font-bold bg-gray-100 p-2 rounded max-w-full break-all text-center')

                with ui.column().classes('w-2/3'):
                    ui.label('Active Connections').classes('text-xl font-bold mb-4')
                    
                    cards_container = ui.column().classes('w-full')
                    
                    async def fetch_and_render_clients():
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.get(f'http://127.0.0.1:{edge_port}/api/status', timeout=2) as resp:
                                    if resp.status == 200:
                                        data = await resp.json()
                                        clients_list = data.get('clients', [])
                                        # Also handle `connectedClients` just in case
                                        if not clients_list and 'connectedClients' in data:
                                            clients_list = data['connectedClients']
                                        render_clients(clients_list)
                                    else:
                                        render_clients(None)
                        except Exception:
                            render_clients(None)
                            
                    async def request_upload(client_id):
                        try:
                            async with aiohttp.ClientSession() as session:
                                payload = {"command": "upload"}
                                async with session.post(f'http://127.0.0.1:{edge_port}/api/command/{client_id}', json=payload) as resp:
                                    if resp.status == 200:
                                        ui.notify(f'Upload command sent to {client_id}!', type='positive')
                                    else:
                                        ui.notify(f'Failed to send command: HTTP {resp.status}', type='negative')
                        except Exception as e:
                            ui.notify(f'Error sending command: {e}', type='negative')

                    def render_clients(clients_data):
                        cards_container.clear()
                        with cards_container:
                            if clients_data is None:
                                ui.label(f'Edge server offline or /api/status not reachable on port {edge_port}.').classes('text-red-500 italic p-4')
                                return
                            if not clients_data:
                                ui.label('Waiting for mobile rig connections...').classes('text-gray-500 italic p-4')
                                return
                                
                            for c in clients_data:
                                client_id = c.get('id', 'Unknown')
                                c_type = c.get('type', 'Rig')
                                with ui.card().classes('w-full mb-2'):
                                    with ui.row().classes('w-full justify-between items-center'):
                                        ui.label(f'{c_type} (ID: {client_id})').classes('font-bold')
                                        ui.badge('Online', color='green')
                                    # Depending on how edge API shapes IP/Stats, display it safely
                                    ui.label(f"Stats: {c.get('details', 'No details available')}").classes('text-sm text-gray-600 mt-1')
                                    with ui.row().classes('mt-4 items-center gap-2'):
                                        ui.input('Start Time', value='2026-03-25T09:00:00').props('type=datetime-local borderless dense').classes('w-48')
                                        ui.input('End Time', value='2026-03-25T10:00:00').props('type=datetime-local borderless dense').classes('w-48')
                                        ui.button('Request Upload', on_click=lambda ci=client_id: request_upload(ci)).classes('bg-blue-600 text-white')

                    # Poll the API
                    ui.timer(5.0, fetch_and_render_clients)
                    ui.timer(0.1, fetch_and_render_clients, once=True)
