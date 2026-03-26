from nicegui import app, ui
import paho.mqtt.client as mqtt
import json
import time
from theme import menu

anomalies = {}  # Store anomalies grouped by topic or RIG

def on_message(client, userdata, msg):
    try:
        topic = msg.topic
        if "anomalies" in topic:
            payload = json.loads(msg.payload.decode())
            source = topic.split('/')[2] if len(topic.split('/')) > 2 else topic
            
            if source not in anomalies:
                anomalies[source] = []
            
            anomalies[source].append({
                "time": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()),
                "data": payload
            })
            anomalies[source] = anomalies[source][-10:]
    except Exception:
        pass

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2) if hasattr(mqtt, "CallbackAPIVersion") else mqtt.Client()

def start_mqtt():
    try:
        mqtt_client.on_message = on_message
        mqtt_client.connect("127.0.0.1", 1883, 60)
        mqtt_client.subscribe("#")
        mqtt_client.loop_start()
    except Exception as e:
        print(f"MQTT start error: {e}")

def create_page():
    @ui.page('/')
    def dashboard_page():
        with menu('Dashboard - MQTT Anomalies'):
            ui.label('Active System Anomalies').classes('text-xl font-semibold mb-2')
            
            # Start MQTT if not started
            if not getattr(app, 'mqtt_started', False):
                start_mqtt()
                app.mqtt_started = True

            container = ui.list().classes('w-full border rounded p-2')
            
            def render_anomalies():
                container.clear()
                with container:
                    if not anomalies:
                        ui.label('No anomalies detected yet. Listening on MQTT 127.0.0.1:1883...').classes('text-gray-500 italic p-4')
                        return
                        
                    for source, events in anomalies.items():
                        title = f'Rig: {source} ({len(events)} events)'
                        with ui.expansion(title, icon='warning').classes('w-full bg-gray-50 mb-2'):
                            for ev in reversed(events):
                                data_str = json.dumps(ev["data"])
                                ui.label(f'Event: {ev["time"]} - {data_str}').classes('font-mono text-sm mb-1')
                            ui.button('Investigate via Agent', on_click=lambda s=source: ui.navigate.to(f'/agent?source={s}')).classes('mt-2 bg-blue-500 text-white')

            ui.timer(1.0, render_anomalies)

