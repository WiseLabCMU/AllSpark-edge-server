import argparse
import asyncio
import json
import sys

import aiohttp


async def main(url):
    print(f"Fetching server status from REST API: {url}/api/status ...")

    # Disable SSL verification for self-signed development certs
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Example REST GET request to get current state
        try:
            async with session.get(f"{url}/api/status") as resp:
                if resp.status == 200:
                    status = await resp.json()
                    print("\n--- Server REST Status ---")
                    print(json.dumps(status, indent=2))
                else:
                    print(f"Failed to get status. Code: {resp.status}")
        except Exception as e:
            print(f"Failed to connect to REST API at {url}: {e}\nMake sure the server is running!")
            sys.exit(1)

        # Example WebSocket connection to listen for broadcasts
        ws_url = url.replace("http://", "ws://").replace("https://", "wss://")
        print(f"\nConnecting to WebSocket at {ws_url} ...")
        try:
            async with session.ws_connect(ws_url) as ws:
                print("WebSocket Connected!")

                # Identify as an 'agent' clientName to receive relevant events
                await ws.send_json({"type": "clientInfo", "clientName": "agent"})
                print("Sending client identification...\nListening for real-time events (like chunkSaved)...")

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                            event_type = data.get("type", "Unknown")
                            print(f"\n--- Broadcast Event Received: '{event_type}' ---")
                            print(json.dumps(data, indent=2))

                            # Automatically trigger an upload request for chunk timeframes
                            if event_type == "chunkSaved" and all(k in data for k in ("connectionId", "startTime", "endTime")):
                                print(f"-> Auto-requesting upload for chunk timeframe: {data['startTime']} to {data['endTime']}")
                                payload = {
                                    "command": "uploadTimeRange",
                                    "message": "Auto-requested from agent",
                                    "startTime": data["startTime"],
                                    "endTime": data["endTime"]
                                }
                                async with session.post(f"{url}/api/command/{data['connectionId']}", json=payload) as cmd_resp:
                                    cmd_data = await cmd_resp.json()
                                    if cmd_data.get("success"):
                                        print("-> Upload request confirmed by server!")
                                    else:
                                        print(f"-> Upload request failed: {cmd_data.get('error')}")

                        except json.JSONDecodeError:
                            print(f"\nReceived raw text: {msg.data}")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        print("WebSocket closed or error.")
                        break
        except Exception as e:
            print(f"WebSocket connection failed: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AllSpark Edge Server Agent Client")
    parser.add_argument("-u", "--url", help="Server URL", default="http://127.0.0.1:8080")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.url.rstrip('/')))
    except KeyboardInterrupt:
        print("\nAgent disconnected.")
