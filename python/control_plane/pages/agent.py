from nicegui import ui
from theme import menu

def create_page():
    @ui.page('/agent')
    def agent_page():
        with menu('Agentic Framework Control'):
            
            with ui.row().classes('w-full gap-6'):
                with ui.column().classes('w-1/3'):
                    ui.label('Investigate Issue').classes('text-xl font-bold mb-4')
                    ui.select(['Latency > 500ms (Camera Rig A)', 'Connection Dropped (Mobile Client B)'], label='Target Anomaly').classes('w-full mb-4')
                    ui.textarea(label='Context / Prompt', value='Analyze the provided QUIC videos and MQTT logs to determine why the latency spiked over 500ms on Rig A.').classes('w-full h-32 mb-4')
                    ui.button('Execute Investigation', icon='science', on_click=lambda: ui.notify('Agent investigation dispatched! Context sent to agentic frameworks.')).classes('w-full bg-blue-600')

                with ui.column().classes('flex-1 w-full'):
                    ui.label('Recent Responses').classes('text-xl font-bold mb-4')
                    
                    with ui.card().classes('w-full bg-gray-50'):
                        with ui.row().classes('w-full justify-between mb-2'):
                            ui.label('Analysis: Issue 742 - Camera Frame Drop').classes('font-bold')
                            ui.label('2026-03-24 14:02:11').classes('text-xs text-gray-500')
                        ui.markdown('''
**Agent Summary:**
I have reviewed the logs between 14:00 and 14:05. The frame drop was caused by an underlying network buffer overflow on the edge interface when `agent_client` transmitted a high-frequency telemetry burst.

I have generated a visualization of the synchronized data streams.
                        ''')
                        ui.button('View in Rerun.io', icon='open_in_new', on_click=lambda: ui.navigate.to('/rerun')).classes('mt-2')
