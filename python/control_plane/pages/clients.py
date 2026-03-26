from nicegui import ui
from theme import menu, get_local_ip

def create_page():
    @ui.page('/clients')
    def clients_page():
        with menu('Mobile Clients & Pairing'):
            ip = get_local_ip()
            server_url = f"ws://{ip}:8080"
            
            with ui.row().classes('w-full gap-8'):
                with ui.card().classes('items-center p-6 w-1/3'):
                    ui.label('Server Pairing').classes('text-xl font-bold mb-4')
                    ui.label('Scan this QR code with the AllSpark app:').classes('text-sm text-gray-600 text-center mb-4')
                    
                    # Generate a QR code using a public API for the prototype
                    ui.image(f'https://api.qrserver.com/v1/create-qr-code/?size=150x150&data={server_url}').classes('w-32 h-32')
                    ui.label(server_url).classes('mt-4 font-mono font-bold bg-gray-100 p-2 rounded max-w-full break-all text-center')

                with ui.column().classes('w-2/3'):
                    ui.label('Active Connections').classes('text-xl font-bold mb-4')
                    
                    with ui.card().classes('w-full mb-2'):
                        with ui.row().classes('w-full justify-between items-center'):
                            ui.label('Mobile Rig Alpha (ID: 39f2)').classes('font-bold')
                            ui.badge('Online', color='green')
                        ui.label('Last File: rig_alpha_001.quic (4.2 MB)').classes('text-sm text-gray-600 mt-1')
                        with ui.row().classes('mt-4 items-center gap-2'):
                            ui.input('Start Time', value='2026-03-25T09:00:00').props('type=datetime-local borderless dense').classes('w-48')
                            ui.input('End Time', value='2026-03-25T10:00:00').props('type=datetime-local borderless dense').classes('w-48')
                            ui.button('Request Upload', on_click=lambda: ui.notify('Time range upload command sent to Mobile Rig Alpha!'))
