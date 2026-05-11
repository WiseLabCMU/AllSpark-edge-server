from nicegui import ui
import socket
import contextlib
import aiohttp

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

@contextlib.contextmanager
def menu(navtitle: str, full_width: bool = False, hide_title: bool = False):
    from pages.settings import load_config, get_edge_base_url
    config = load_config()
    mc_cfg = config.get('mobile_client', {})
    edge_port = mc_cfg.get('port', 8080)
    edge_base_url = get_edge_base_url()
    agent_url = mc_cfg.get('agentConfig', {}).get('agent_url', 'http://localhost:8000/run')

    cp_cfg = config.get('control_plane', {})

    with ui.header().classes('justify-between items-center bg-blue-grey-9'):
        with ui.row().classes('items-center'):
            ui.label('AllSpark Edge').classes('text-lg font-bold mr-6')

            with ui.row().classes('items-center gap-1 mr-4'):
                ui.label('Status:').classes('text-sm font-bold text-gray-300 mr-2')

                with ui.label('ADK').classes('px-2 py-0.5 bg-gray-500 rounded text-white text-xs font-bold cursor-help') as adk_status:
                    adk_tt = ui.tooltip('ADK Agentic Framework Checking...')

                with ui.label('Edge').classes('px-2 py-0.5 bg-gray-500 rounded text-white text-xs font-bold cursor-help') as edge_status:
                    edge_tt = ui.tooltip('Edge Server API Checking...')

                with ui.label('Client').classes('px-2 py-0.5 bg-gray-500 rounded text-white text-xs font-bold cursor-help') as client_status:
                    client_tt = ui.tooltip('Mobile Client API Checking...')

            async def fetch_status():
                try:
                    import asyncio
                    from urllib.parse import urlparse

                    parsed_agent = urlparse(agent_url)
                    agent_p_host = parsed_agent.hostname or 'unknown'
                    agent_p_port = parsed_agent.port or (443 if parsed_agent.scheme == 'https' else 80)
                    agent_p_scheme = parsed_agent.scheme or 'http'

                    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                        # 1. Edge API HTTP & WS Checks
                        try:
                            async with session.get(f'{edge_base_url}/api/health', timeout=2) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    protocols = data.get('protocols', ['ws'])
                                    is_secure = 'wss' in protocols
                                    edge_scheme = 'https' if is_secure else 'http'
                                    client_scheme = 'wss' if is_secure else 'ws'
                                    hname = data.get('address', '127.0.0.1')

                                    edge_status.classes(replace='px-2 py-0.5 bg-green-600 rounded text-white text-xs font-bold cursor-help')
                                    edge_tt.set_text(f"Edge Server API Online at {edge_scheme}://{hname}:{edge_port}")

                                    client_status.classes(replace='px-2 py-0.5 bg-green-600 rounded text-white text-xs font-bold cursor-help')
                                    client_tt.set_text(f"Mobile Client API Online at {client_scheme}://{hname}:{edge_port}")
                                else:
                                    raise Exception("Health check failed")
                        except Exception:
                            edge_status.classes(replace='px-2 py-0.5 bg-red-600 rounded text-white text-xs font-bold cursor-help')
                            edge_tt.set_text(f"Edge Server API Offline at {edge_base_url}")
                            client_status.classes(replace='px-2 py-0.5 bg-red-600 rounded text-white text-xs font-bold cursor-help')
                            client_tt.set_text(f"Mobile Client API Offline at ws://127.0.0.1:{edge_port}")

                        # 2. Agent Framework Check
                        try:
                            _, writer = await asyncio.wait_for(asyncio.open_connection(agent_p_host, agent_p_port), timeout=2.0)
                            writer.close()
                            await writer.wait_closed()
                            adk_status.classes(replace='px-2 py-0.5 bg-green-600 rounded text-white text-xs font-bold cursor-help')
                            adk_tt.set_text(f"ADK Agentic Framework Online at {agent_p_scheme}://{agent_p_host}:{agent_p_port}")
                        except Exception:
                            adk_status.classes(replace='px-2 py-0.5 bg-red-600 rounded text-white text-xs font-bold cursor-help')
                            adk_tt.set_text(f"ADK Agentic Framework Offline at {agent_p_scheme}://{agent_p_host}:{agent_p_port}")

                except Exception as e:
                    pass

            _status_timer = ui.timer(5.0, fetch_status)
            async def _safe_fetch_status(_t=_status_timer):
                try:
                    await fetch_status()
                except RuntimeError:
                    _t.cancel()
            _status_timer.callback = _safe_fetch_status
            ui.timer(0.1, fetch_status, once=True)

        with ui.row().classes('items-center'):
            nav_items = [
                ('Agent', '/agent', 'Agent'),
                ('Clients', '/clients', 'Client'),
                ('Logs', '/logs', 'Log'),
                ('Settings', '/settings', 'Setting'),
                ('Debug', '/debug', 'Debug')
            ]
            for title, route, kw in nav_items:
                base_classes = 'no-underline mx-2 transition-colors px-2 py-1 rounded'
                if kw in navtitle:
                    classes = f'{base_classes} text-blue-300 bg-blue-grey-8 font-bold'
                else:
                    classes = f'{base_classes} text-white hover:text-blue-300 hover:bg-blue-grey-8'
                ui.link(title, route).classes(classes)

            ui.label('👤 test-user').classes('ml-8 mr-2 text-sm text-gray-300')

    container_classes = 'w-full mx-auto px-1 py-1' + ('' if full_width else ' mt-1')
    with ui.column().classes(container_classes):
        if not hide_title:
            ui.label(navtitle).classes('text-2xl font-bold mb-4 text-gray-800')
        yield
