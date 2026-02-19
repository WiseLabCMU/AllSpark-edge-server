import asyncio
import aiohttp
import json
import sys
import argparse
import os

async def main(server_url, ws_url):
    print(f"Fetching server status from REST API: {server_url}/api/status ...")

    # We use aiohttp to act as the agent's network stack
    # Disable SSL verification for self-signed development certs
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        # 1. Example REST GET request to get current state
        try:
            async with session.get(f"{server_url}/api/status") as resp:
                if resp.status == 200:
                    status = await resp.json()
                    print("\n--- Server REST Status ---")
                    print(json.dumps(status, indent=2))
                else:
                    print(f"Failed to get status. Code: {resp.status}")
        except Exception as e:
            print(f"Failed to connect to REST API at {server_url}: {e}")
            print("Make sure the server is running!")
            sys.exit(1)

        # 2. Example WebSocket connection to listen for broadcasts
        print(f"\nConnecting to WebSocket at {ws_url} ...")
        try:
            async with session.ws_connect(ws_url) as ws:
                print("WebSocket Connected!")

                # Identify as an 'agent' clientName so the server knows to broadcast events to us
                client_info = {
                    "type": "clientInfo",
                    "clientName": "agent"
                }

                print("Sending client identification...")
                await ws.send_json(client_info)

                print("\nListening for real-time events (like chunkSaved)...")

                # Enter a loop listening indefinitely for incoming events
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                            event_type = data.get("type", "Unknown")
                            print(f"\n--- Broadcast Event Received: '{event_type}' ---")
                            print(json.dumps(data, indent=2))

                            # Automatically trigger an upload request for chunk timeframes
                            if event_type == "chunkSaved":
                                cid = data.get("connectionId")
                                start_time = data.get("startTime")
                                end_time = data.get("endTime")

                                if cid and start_time and end_time:
                                    print(f"-> Auto-requesting upload for chunk timeframe: {start_time} to {end_time}")
                                    payload = {
                                        "command": "uploadTimeRange",
                                        "message": "Auto-requested from agent",
                                        "startTime": start_time,
                                        "endTime": end_time
                                    }
                                    try:
                                        async with session.post(f"{server_url}/api/command/{cid}", json=payload) as cmd_resp:
                                            cmd_data = await cmd_resp.json()
                                            if cmd_data.get("success"):
                                                print("-> Upload request confirmed by server!")
                                            else:
                                                print(f"-> Upload request failed: {cmd_data.get('error')}")
                                    except Exception as e:
                                        print(f"-> Failed to send upload command: {e}")

                        except json.JSONDecodeError:
                            print(f"\nReceived raw text: {msg.data}")

                    elif msg.type == aiohttp.WSMsgType.CLOSED:
                        print("WebSocket closed.")
                        break
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        print("WebSocket error.")
                        break
        except Exception as e:
            print(f"WebSocket connection failed: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AllSpark Edge Server Agent Client")
    parser.add_argument("-c", "--config", help="Path to config.json", default="../../config.json")
    args = parser.parse_args()

    hostname = "127.0.0.1"
    port = 8080
    use_ssl = False

    if os.path.isfile(args.config):
        try:
            with open(args.config, "r") as f:
                config_data = json.load(f)

            # Use 'localhost' instead of '0.0.0.0' for local connections
            cfg_hostname = config_data.get("hostname", hostname)
            hostname = "127.0.0.1" if cfg_hostname == "0.0.0.0" else cfg_hostname

            port = config_data.get("port", port)

            if config_data.get("keyFile") and config_data.get("certFile"):
                # We do a basic check to see if the SSL keys exist.
                # Resolving from config.json's directory relative location:
                config_dir = os.path.dirname(os.path.abspath(args.config))
                key_path = os.path.join(config_dir, config_data["keyFile"])
                cert_path = os.path.join(config_dir, config_data["certFile"])

                if os.path.exists(key_path) and os.path.exists(cert_path):
                    use_ssl = True
                    print(f"Loaded config from {args.config} (SSL Enabled)")
                else:
                    print(f"Loaded config from {args.config} (SSL Disabled - Certs missing)")
            else:
                print(f"Loaded config from {args.config} (SSL Disabled - No Certs set)")

        except Exception as e:
            print(f"Error reading {args.config}: {e}. Falling back to default settings.")
    else:
        print(f"Config file not found at {args.config}. Using explicit default targets.")

    protocol = "https" if use_ssl else "http"
    ws_protocol = "wss" if use_ssl else "ws"

    server_url_parsed = f"{protocol}://{hostname}:{port}"
    ws_url_parsed = f"{ws_protocol}://{hostname}:{port}"

    try:
        asyncio.run(main(server_url_parsed, ws_url_parsed))
    except KeyboardInterrupt:
        print("\nAgent disconnected.")
