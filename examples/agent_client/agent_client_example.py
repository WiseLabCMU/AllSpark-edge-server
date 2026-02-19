import asyncio
import aiohttp
import json
import sys

# Change this if your server is running on a different machine or port
SERVER_URL = "http://127.0.0.1:8080"
WS_URL = "ws://127.0.0.1:8080"

async def main():
    print(f"Fetching server status from REST API: {SERVER_URL}/api/status ...")

    # We use aiohttp to act as the agent's network stack
    async with aiohttp.ClientSession() as session:
        # 1. Example REST GET request to get current state
        try:
            async with session.get(f"{SERVER_URL}/api/status") as resp:
                if resp.status == 200:
                    status = await resp.json()
                    print("\n--- Server REST Status ---")
                    print(json.dumps(status, indent=2))
                else:
                    print(f"Failed to get status. Code: {resp.status}")
        except Exception as e:
            print(f"Failed to connect to REST API at {SERVER_URL}: {e}")
            print("Make sure the server is running!")
            sys.exit(1)

        # 2. Example WebSocket connection to listen for broadcasts
        print(f"\nConnecting to WebSocket at {WS_URL} ...")
        try:
            async with session.ws_connect(WS_URL) as ws:
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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAgent disconnected.")
