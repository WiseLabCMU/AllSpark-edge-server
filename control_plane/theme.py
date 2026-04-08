from nicegui import ui
import socket
import contextlib

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
    """A context manager for the page layout and navigation menu."""
    with ui.header().classes('justify-between items-center bg-blue-grey-9'):
        ui.label('AllSpark Edge Server').classes('text-lg font-bold')
        with ui.row().classes('items-center'):
            ui.link('Anomalies', '/anomalies').classes('text-white no-underline hover:text-blue-300 mx-2 transition-colors')
            ui.link('Clients', '/clients').classes('text-white no-underline hover:text-blue-300 mx-2 transition-colors')
            ui.link('Logs', '/logs').classes('text-white no-underline hover:text-blue-300 mx-2 transition-colors')
            ui.link('Agent', '/agent').classes('text-white no-underline hover:text-blue-300 mx-2 transition-colors')
            ui.link('Rerun', '/rerun').classes('text-white no-underline hover:text-blue-300 mx-2 transition-colors')
            ui.link('Settings', '/settings').classes('text-white no-underline hover:text-blue-300 mx-2 transition-colors')
            ui.label('👤 test-user').classes('ml-8 mr-2 text-sm text-gray-300')

    container_classes = 'w-full mx-auto p-4' + ('' if full_width else ' max-w-5xl mt-6')
    with ui.column().classes(container_classes):
        if not hide_title:
            ui.label(navtitle).classes('text-2xl font-bold mb-4 text-gray-800')
        yield
