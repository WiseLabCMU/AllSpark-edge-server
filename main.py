import subprocess
import os
import sys

def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    mobile_server_script = os.path.join(root_dir, 'mobile_client', 'server.py')
    control_plane_script = os.path.join(root_dir, 'control_plane', 'main.py')
    
    # Start mobile client server
    print("Starting mobile client server...")
    mobile_proc = subprocess.Popen([sys.executable, mobile_server_script])
    
    # Start control plane
    print("Starting control plane...")
    control_proc = subprocess.Popen([sys.executable, control_plane_script])
    
    try:
        mobile_proc.wait()
        control_proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down servers...")
        mobile_proc.terminate()
        control_proc.terminate()
        mobile_proc.wait()
        control_proc.wait()
        print("Servers stopped gracefully.")

if __name__ == "__main__":
    main()
