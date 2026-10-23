import asyncio, json, pathlib, urllib.parse, websockets

tok_file = pathlib.Path.home() / "AppData" / "Roaming" / "pc-mcp-bridge" / "session_token"
raw = tok_file.read_text(encoding="utf-8").strip()
if raw.startswith("["):
    tok = json.loads(raw)[0]
else:
    tok = raw

async def main():
    url = "ws://127.0.0.1:8765/ws?client=cho-zen1&token=" + urllib.parse.quote(tok)
    try:
        async with websockets.connect(url, additional_headers={"Authorization": "Bearer " + tok}) as ws:
            await ws.send('{"to":"all","text":"loopback probe"}')
            print("CONNECTED", await asyncio.wait_for(ws.recv(), 5))
    except Exception as e:
        print(type(e).__name__, e)

asyncio.run(main())
