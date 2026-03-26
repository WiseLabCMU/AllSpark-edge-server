"""
A lightweight dummy script to run a rerun.io web viewer for the AllSpark dashboard demo.
Requirements: pip install rerun-sdk
"""
import rerun as rr
import rerun.blueprint as rrb
import time
import math

def main():
    # Initialize the rerun SDK and set it to serve over WebSocket + HTTP mapping for web viewer
    rr.init("AllSpark_Demo", spawn=False)
    
    # Create the web viewer
    rr.serve_web_viewer(web_port=9090, open_browser=False)
    
    print("Dummy Rerun Server running at http://127.0.0.1:9090")
    print("Populating example dummy data...")
    
    # Try sending initial blueprint and some data
    rr.send_blueprint(rrb.Blueprint(
        rrb.Horizontal(
            rrb.Spatial2DView(name="Camera View", origin="camera"),
            rrb.TextDocumentView(name="Agent Response", origin="agent_response")
        )
    ))

    # Static UI init
    rr.log("agent_response", rr.TextDocument("AllSpark Agentic Analysis Results\n\nResults from using the AllSpark Agentic Framework will appear here."))

    i = 0
    try:
        while True:
            # Removed time sequence for backward/forward compatibility
            # rr.set_time_sequence("frame", i)
            # Log a simple moving point payload
            rr.log("camera/tracked_point", rr.Points2D([[math.sin(i * 0.1) * 10, math.cos(i * 0.1) * 10]], colors=[255, 0, 0]))
            
            time.sleep(0.1)
            i += 1
    except KeyboardInterrupt:
        print("Shutting down dummy rerun server.")

if __name__ == "__main__":
    main()
