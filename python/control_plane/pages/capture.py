from nicegui import ui
from theme import menu

def create_page():
    @ui.page('/capture')
    def capture_page():
        with menu('Data Capture'):
            ui.label('Logs & QUIC Videos Review').classes('text-xl font-bold mb-4')
            
            with ui.tabs().classes('w-full') as tabs:
                videos_tab = ui.tab('Videos')
                logs_tab = ui.tab('Logs')
            
            with ui.tab_panels(tabs, value=videos_tab).classes('w-full bg-transparent'):
                with ui.tab_panel(videos_tab):
                    with ui.row().classes('w-full gap-4'):
                        for i in range(1, 4):
                            with ui.card().classes('w-64'):
                                ui.html('<div class="w-full text-center p-8 bg-gray-200 text-gray-500 rounded"><i class="fas fa-video fa-2x"></i><br>rig_capture_00{}.mp4</div>'.format(i))
                                ui.label(f'Capture 0{i}').classes('font-bold mt-2')
                                ui.label('Duration: 00:05:23').classes('text-sm text-gray-500')
                                ui.button('Play', icon='play_arrow', on_click=lambda: ui.notify('Playing video stream...')).classes('w-full mt-2')

                with ui.tab_panel(logs_tab):
                    ui.label('Recent MQTT Logs').classes('font-bold mb-2')
                    log_text = "[2026-03-25 10:15:01] INFO - Connected to rig_alpha\n" \
                               "[2026-03-25 10:15:05] WARN - Latency spike detected: 154ms\n" \
                               "[2026-03-25 10:16:22] ERROR - Disconnected from rig_beta\n" \
                               "[2026-03-25 10:16:45] INFO - Reconnected to rig_beta"
                    ui.textarea(value=log_text).props('readonly').classes('w-full font-mono text-sm bg-black text-green-400 p-2 rounded h-64')
