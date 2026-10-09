#!/usr/bin/env python3
"""A2A Protocol listener for Cho-zen1 (Windows).

Auto-responds: ping->pong, check-in->status, help->commands, status->info.
Logs to C:/Users/Cho-zen/pc-mcp-bridge/ws_messages_chozen1.log
"""
import asyncio
import json
import time
import os
from datetime import datetime
from pathlib import Path

import websockets

# Token from bridge config (or env)
TOKEN_FILE = Path(os.environ.get("APPDATA", "")) / "pc-mcp-bridge" / "session_token"
LOG_FILE = Path("C:/Users/Cho-zen/pc-mcp-bridge/ws_messages_chozen1.log")
WS_URL = "wss://pc.secscan.info/ws"
CLIENT_NAME = "cho-zen1"


def get_token():
    try:
        data = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if data.startswith("["):
            tokens = json.loads(data)
            return tokens[0] if tokens else ""
        return data
    except Exception:
        return ""


def log(direction, frm, to, text, msg_id=""):
    ts = datetime.now().isoformat()
    entry = {"ts": ts, "dir": direction, "from": frm, "to": to,
             "text": text, "id": msg_id}
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"[{ts}] {direction} [{frm}->{to}] {text[:80]}", flush=True)


def a2a_respond(text, frm):
    low = text.lower().strip()
    if "ping" in low:
        return "pong"
    if "check" in low and "in" in low:
        return (f"Cho-zen1 here, online. "
                f"{datetime.now().strftime('%H:%M:%S')}.")
    if low.startswith("help") or low == "?":
        return ("A2A commands: ping, check-in, help, status.")
    if "status" in low:
        return "Cho-zen1 A2A listener active."
    return None


async def run():
    token = get_token()
    if not token:
        print("No token found!", flush=True)
        return
    url = f"{WS_URL}?client={CLIENT_NAME}&token={token}"
    backoff = 1
    while True:
        try:
            print(f"[{datetime.now().isoformat()}] A2A connecting...",
                  flush=True)
            async with websockets.connect(url) as ws:
                print(f"[{datetime.now().isoformat()}] A2A online",
                      flush=True)
                backoff = 1
                await ws.send(json.dumps({
                    "to": "all",
                    "text": "Cho-zen1 A2A listener online (auto-respond enabled)",
                    "id": f"chozen1-a2a-{int(time.time())}",
                }))
                async for raw in ws:
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if data.get("ok") is not None and "from" not in data:
                        continue
                    frm = data.get("from", "?")
                    if frm == CLIENT_NAME:
                        continue
                    text = data.get("text", "")
                    msg_id = data.get("id", "")
                    log("IN", frm, CLIENT_NAME, text, msg_id)
                    reply = a2a_respond(text, frm)
                    if reply:
                        await ws.send(json.dumps({
                            "to": frm,
                            "text": reply,
                            "id": f"chozen1-auto-{int(time.time()*1000)}",
                        }))
                        log("OUT", CLIENT_NAME, frm, reply)
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] A2A error: {e}, "
                  f"retry in {backoff}s", flush=True)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    asyncio.run(run())
