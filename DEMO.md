# 5-Minute Demo Runbook

Reproduces the hackathon arc end to end. Run from the repo root on
the PC (or set `PC_BRIDGE_URL` to the tunnel address). Assumes the
server and tunnel are already up and you hold a token
(`PC_BRIDGE_TOKEN` set or `pair` first).

## Act 1 — It is alive (60s)

```bash
python bridge/op_call.py health
# tunnel: UP, server: UP, token: VALID
python bridge/op_call.py tools        # 109 tools
python bridge/op_call.py call system_info '{}'
```

## Act 2 — It can see (60s)

```bash
python bridge/op_call.py call screenshot '{"scale": 0.5}'
python bridge/op_call.py call list_windows '{}'
python bridge/op_call.py call doctor '{}'
```

Show the screenshot and the `doctor` checks payload.

## Act 3 — It can research (90s)

```bash
python bridge/op_call.py call browser_navigate '{"url":"https://modelcontextprotocol.io/"}'
python bridge/op_call.py call browser_snapshot '{}'
python bridge/op_call.py call web_search '{"query":"Model Context Protocol MCP servers use cases examples"}'
python bridge/op_call.py call web_fetch '{"url":"<a result URL>","max_bytes": 20000}'
```

Fallback: if `browser_*` errors (Edge not attached), do the whole
act with `web_search` + `web_fetch` — same content, no browser.

## Act 4 — It can write (90s)

```bash
# Empty target first, then open exactly that file:
python bridge/op_call.py call shell_pwsh '{"script":"New-Item $env:TEMP\\demo.txt -ItemType File -Force"}'
python bridge/op_call.py call shell_pwsh '{"script":"Start-Process notepad.exe -ArgumentList \"$env:TEMP\\demo.txt\""}'
python bridge/op_call.py call wait_for '{"kind":"window","target":"demo.txt"}'
# CHECK the reported title matches demo.txt before pasting. Then:
python bridge/op_call.py call paste_text '{"text":"Hello from the bridge."}'
python bridge/op_call.py call hotkey '{"keys":"ctrl+s"}'
python bridge/op_call.py call read_file '{"path":"<TEMP>\\demo.txt"}'
```

Rules that keep this act safe: verify the destination title
before any input (a fresh launch can restore old tabs); if a
Save dialog never appears after Ctrl+S you are in an existing
file's tab — stop and check the title before typing anything.

## Act 5 — It is tested (30s)

```bash
cd pc-agent
python -m pytest tests/test_session_attribution.py -q   # 5 passed
```

Closer: "109 tools, wired, audited, fixed, and green."
