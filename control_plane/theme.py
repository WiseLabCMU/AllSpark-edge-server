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
def menu(navtitle: str):
    """A context manager for the page layout and navigation menu."""
    with ui.header().classes('justify-between items-center bg-blue-grey-9'):
        ui.label('AllSpark').classes('text-lg font-bold')
        with ui.row().classes('items-center'):
            ui.link('Dashboard', '/').classes('text-white no-underline hover:text-blue-300 mx-2 transition-colors')
            ui.link('Clients', '/clients').classes('text-white no-underline hover:text-blue-300 mx-2 transition-colors')
            ui.link('Capture', '/capture').classes('text-white no-underline hover:text-blue-300 mx-2 transition-colors')
            ui.link('Agent', '/agent').classes('text-white no-underline hover:text-blue-300 mx-2 transition-colors')
            ui.link('Rerun', '/rerun').classes('text-white no-underline hover:text-blue-300 mx-2 transition-colors')
            ui.link('Settings', '/settings').classes('text-white no-underline hover:text-blue-300 mx-2 transition-colors')
            ui.label('👤 test-user').classes('ml-8 mr-2 text-sm text-gray-300')

    with ui.column().classes('w-full max-w-5xl mx-auto mt-6 p-4'):
        ui.label(navtitle).classes('text-2xl font-bold mb-4 text-gray-800')
        yield
