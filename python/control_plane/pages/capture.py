from nicegui import ui
import os
import glob
from theme import menu
from pages.settings import load_config

def create_page():
    @ui.page('/capture')
    def capture_page():
        config = load_config()
        upload_path = config.get('uploadPath', 'uploads/orgs/default')
        abs_upload_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', upload_path))
        
        with menu('Data Capture'):
            ui.label('Logs & QUIC Videos Review').classes('text-xl font-bold mb-4')
            
            with ui.tabs().classes('w-full') as tabs:
                videos_tab = ui.tab('Videos')
                logs_tab = ui.tab('Logs')
            
            with ui.tab_panels(tabs, value=videos_tab).classes('w-full bg-transparent'):
                with ui.tab_panel(videos_tab):
                    with ui.row().classes('w-full gap-4 flex-wrap mt-2'):
                        if os.path.exists(abs_upload_path):
                            files = glob.glob(os.path.join(abs_upload_path, '*.*'))
                            vid_files = [f for f in files if f.endswith(('.mp4', '.quic', '.json'))]
                            if not vid_files:
                                ui.label('No capture files found in upload path yet.').classes('text-gray-500 italic p-4 mt-2')
                            
                            for f in sorted(vid_files, reverse=True):
                                filename = os.path.basename(f)
                                size_mb = os.path.getsize(f) / (1024 * 1024)
                                with ui.card().classes('w-64 relative'):
                                    if filename.endswith('.mp4'):
                                        ui.video(f'/videos/{filename}').classes('w-full h-32 bg-black object-contain')
                                    elif filename.endswith('.quic'):
                                        ui.html(f'<div class="w-full text-center p-8 bg-blue-100 text-blue-500 rounded"><i class="fas fa-video fa-2x"></i><br><b>.QUIC</b></div>')
                                    else:
                                        ui.html(f'<div class="w-full text-center p-8 bg-gray-100 text-gray-500 rounded"><i class="fas fa-file fa-2x"></i><br><b>.JSON</b></div>')
                                    
                                    ui.label(filename).classes('font-bold mt-2 truncate max-w-full').tooltip(filename)
                                    ui.label(f'Size: {size_mb:.2f} MB').classes('text-sm text-gray-500')
                                    # Create a simple direct download link or trigger notify for quic
                                    ui.button('Play' if filename.endswith('.mp4') else 'Download', 
                                              icon='play_arrow' if filename.endswith('.mp4') else 'download', 
                                              on_click=lambda fn=filename: ui.download(f'/videos/{fn}')).classes('w-full mt-2')
                        else:
                            ui.label('Upload directory not created yet. Awaiting initial mobile rig connection.').classes('text-gray-500 italic')

                with ui.tab_panel(logs_tab):
                    ui.label('Recent Edge Logs (simulated tail)').classes('font-bold mb-2')
                    log_text = "[2026-03-25 10:15:01] INFO - Connected to rig_alpha\n" \
                               "[2026-03-25 10:15:05] WARN - Latency spike detected: 154ms\n" \
                               "[2026-03-25 10:16:22] ERROR - Disconnected from rig_beta\n" \
                               "[2026-03-25 10:16:45] INFO - Reconnected to rig_beta"
                    ui.textarea(value=log_text).props('readonly').classes('w-full font-mono text-sm bg-black text-green-400 p-2 rounded h-64')
