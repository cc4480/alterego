# Operator guide — the controlling side

You are the operator: an AI agent (or a human with curl) driving a
Windows PC through its tunnel. The PC owner runs the server and hands
you exactly one thing in chat — safe to share:

1. A 6-digit pairing code (single-use, 30-minute expiry)

The tunnel address is permanent: `https://pc.secscan.info` (named
Cloudflare tunnel `pc-bridge`). The bundled client is
`bridge/op_call.py` — Python stdlib only, nothing to install.

## 0. Health check (run first, every session)

```bash
export PC_BRIDGE_URL=https://pc.secscan.info
python3 bridge/op_call.py health
```

It reports the layers independently, in order:

- `tunnel: UP/DOWN` — can we reach the PC's `/health` at all (no auth)
- `token: NONE / VALID / <error>` — do we hold a session the server
  accepts
- `server: UP` — the MCP `initialize` handshake succeeded

If the tunnel is DOWN, the PC is off, the tunnel isn't running, or
there's a network issue — the remaining checks are skipped. Diagnose
before retrying blindly (see
[troubleshooting.md](troubleshooting.md)).

## 1. Pair

Exchange the code for a session bearer token:

```bash
python3 bridge/op_call.py pair 279416    # saves the session token, mode 0600
```

Or raw HTTP:

```bash
curl -s -X POST $PC_BRIDGE_URL/pair \
  -H 'Content-Type: application/json' \
  -d '{"code":"279416"}'
# -> {"token":"<64 hex chars>"}        (200)
#    401 wrong code (counts toward the 5-attempt lockout)
#    403 pairing unavailable / expired / already used
```

**Token handling rules:**

- The CLI saves the token to `~/.config/pc-bridge/token` (mode 0600)
  and reuses it across restarts; `PC_BRIDGE_TOKEN` in the environment
  overrides the file. The token never goes in chat, logs, or URLs.
- It **persists on the PC across server restarts** and lasts until
  revoked. Pairing is **additive**: a new pairing adds a token and
  does *not* invalidate existing ones — several clients can be paired
  at once.
- A 401 on every call means the token was revoked (owner deleted the
  token file, `/logout`, or `/revoke`). The CLI clears its saved
  token on a 401. Ask for a fresh pairing code and re-pair. Do not
  ask for any other secret — there isn't one.
- Codes can't be reused, and **5 wrong guesses lock pairing** until
  the owner deletes `%APPDATA%\pc-mcp-bridge\pairing.lock` or
  restarts the server. Don't burn attempts.
- When you're done, log out:
  `python3 bridge/op_call.py logout` — asks the server to revoke
  **all** sessions (`POST /logout`). `POST /revoke` revokes only the
  token you present.

## 2. Call tools (synchronous)

```bash
python3 bridge/op_call.py tools                       # 67 tools
python3 bridge/op_call.py call system_info '{}'
python3 bridge/op_call.py call screenshot '{}'        # -> /tmp/shot-full.json
python3 bridge/op_call.py call shell_exec '{"command":"whoami"}'
```

- The full response of every `call` is stashed at `/tmp/op-full.json`;
  stdout is capped for readability.
- Write tools pop a native Yes/No dialog **on the PC owner's
  screen** — the call blocks until they answer (30 s timeout =
  deny). A denial comes back as `{"error": "..."}`, never a crash.
- Approval is tiered (see [tools.md](tools.md)): reads never ask;
  routine writes ask (skipped when the server runs with
  `PC_BRIDGE_AUTO_APPROVE=1`); destructive tools (`power`,
  `kill_process`, `delete_file`, `write_file`, `shell_exec`,
  `shell_pwsh`, `browser_eval`, `batch`) **always** pop a
  DESTRUCTIVE-titled dialog — auto-approve cannot bypass them, and
  only the owner-side `dontAsk` posture turns dialogs off entirely.
- Transient transport failures (the tunnel flapping, a dropped
  connection) are retried with backoff; HTTP errors are not. **A
  retry after a lost *response* can double-execute a write** — verify
  state after a retried write call. For anything long or
  write-heavy, prefer job mode.

## 3. Job mode (durable, recommended for real work)

Job mode submits the call to the server's journal **once**, under an
idempotency key; the PC executes it in the background through the
same wrappers (same approvals, same audit events) whether or not
you stay connected.

```bash
# fire and forget — returns a job_id immediately
python3 bridge/op_call.py submit shell_pwsh '{"script":"..."}' --key build-42

# submit + poll until done (default timeout: PC_BRIDGE_JOB_TIMEOUT, 3600 s)
python3 bridge/op_call.py calljob shell_pwsh '{"script":"..."}' --key build-42

python3 bridge/op_call.py job <job_id>          # status
python3 bridge/op_call.py job-result <job_id>   # result when terminal
python3 bridge/op_call.py job-cancel <job_id>   # kill a queued/running job
python3 bridge/op_call.py resume <job_id>       # re-attach and poll
python3 bridge/op_call.py resume --key build-42 # ...or by idempotency key
```

- Resubmitting with the same `--key` returns the existing job —
  it never executes twice. If `--key` is omitted, one is derived
  from the tool name + arguments.
- `--lineage` records where the request came from (`typed`,
  `relay`, `scheduled`, `agent`).
- Terminal states: `succeeded`, `failed`, `killed`, `cancelled`.
- If the owner has engaged ESTOP (a sentinel file on the PC),
  submissions are still accepted and journaled but nothing new
  executes until it's lifted.

## 4. Protocol notes (writing your own client)

- Transport: MCP **Streamable HTTP** — `POST {url}/mcp` with
  `Accept: application/json, text/event-stream`.
- Handshake: `initialize` → read the `Mcp-Session-Id` response
  header → send it on every subsequent request →
  `notifications/initialized` (no id) → `tools/list` /
  `tools/call`.
- Responses arrive as SSE `data:` lines — take the last one,
  JSON-parse it.
- Auth: `Authorization: Bearer <session token>` on every request.
  The unauthenticated routes are only `GET /health`, `POST /pair`,
  and `GET /chat`.
- Don't route the bearer token through an egress proxy
  (`trust_env=False` in httpx): proxies would see the token, and
  some `no_proxy` forms break parsing.
- The server deliberately disables the MCP SDK's Host-header
  (DNS-rebinding) check: the tunnel hostname can't be allowlisted,
  the server is loopback-only, and the bearer token is the real
  authentication.
- A WebSocket relay lives at `/ws?client=<name>&token=<bearer>`
  (`cosmo`, `cho-zen1`, `judith`); the chat UI is at `GET /chat`.

## 5. No-PC testing

```bash
cd bridge
MOCK_TOKEN=secret python3 mock_mcp_server.py 8765 &
PC_BRIDGE_URL=http://127.0.0.1:8765 PC_BRIDGE_TOKEN=secret python3 op_call.py tools
```

The mock serves the real tool schemas (`bridge/tools.json`) with
canned responses — no PC or tunnel needed. (`pc_bridge.py` is the
older minimal CLI; use `op_call.py`.)
