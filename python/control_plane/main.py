from nicegui import ui

# Import all pages to register their routes
from pages import dashboard, clients, capture, agent, rerun_view

# Initialize pages
dashboard.create_page()
clients.create_page()
capture.create_page()
agent.create_page()
rerun_view.create_page()

if __name__ in {"__main__", "__mp_main__"}:
    # Run the control plane
    ui.run(title='AllSpark Control Plane', port=8080, storage_secret='allspark-secret')
