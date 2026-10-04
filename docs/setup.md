# PC setup — first run (do this on the Windows PC)

You are the first person to run the Windows half. Nothing here phones home;
the PC only ever dials **out** through a tunnel you start yourself.

## 1. Install Python

1. Install Python 3.11+ from https://www.python.org/downloads/
2. On the installer, check **"Add python.exe to PATH"**.
3. Verify in a new terminal: `python --version`

## 2. Get the code

```powershell
git clone https://github.com/cc4480/pc-mcp-bridge
cd pc-mcp-bridge
pip install -r pc-agent\requirements.txt
```

## 3. Start the agent

```powershell
python pc-agent\server.py
```

- It listens on `http://127.0.0.1:8765` — loopback only, unreachable from the
  network except through the tunnel below.
- It prints a **6-digit pairing code** (single-use, expires in 5 minutes).
  **Paste the code in chat** — it's safe to share: it dies 5 minutes after
  the server starts and can't be reused.
- It also prints the audit log location: `%APPDATA%\pc-mcp-bridge\audit.log`.

## 4. Open the tunnel

In a second terminal:

```powershell
winget install cloudflare.cloudflared
cloudflared tunnel --url http://127.0.0.1:8765
```

Copy the `https://<random>.trycloudflare.com` URL it prints.

## 5. Hand two things to the operator (both in chat — both are safe to share)

1. The `https://....trycloudflare.com` tunnel URL.
2. The 6-digit pairing code from step 3.

The operator POSTs the code to `/pair` and receives a session bearer token
over TLS. That token never touches chat — it lives only in the operator's
session memory and dies when the server restarts.

That's it. Keep both terminals running while the operator works.

## What to expect

- Read tools (screenshot, windows, files) just work.
- Every write tool (`focus_window`, `type_text`, `shell_exec`) pops a native
  Windows **Yes/No dialog on your screen** showing the exact action. 30s with
  no answer = denied. You are the final say on everything.
- To stop: close both terminals. The tunnel URL dies with it.

## Troubleshooting

- `pip install` fails on `pywin32`: run the terminal as Administrator once,
  or `pip install --only-binary :all: pywin32`.
- Operator gets 401: the token in their vault doesn't match
  `%APPDATA%\pc-mcp-bridge\token` — re-copy it via the vault.
- Tunnel URL changed: quick tunnels get a new URL every restart — paste the
  new one in chat.
