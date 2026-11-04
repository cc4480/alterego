# Muse Operator Guide

Notes for Muse sessions driving this bridge (CLI or app). The
generic protocol lives in [operator.md](operator.md); this page is
the Muse-specific layer: connection, speed, and the safety rules
one session learned the hard way.

## Connect (fast path)

On the PC itself, talk loopback and skip the tunnel:

- Base: `http://127.0.0.1:8765`. Health: `GET /health`.
- Token: `PC_BRIDGE_TOKEN` env wins; else read the PC-side store
  `%APPDATA%\pc-mcp-bridge\session_token` (JSON list, first entry;
  old format is a bare token). Never print it.
- One MCP handshake (`initialize`, `notifications/initialized`)
  per batch of calls, not per call. Prefer a small driver that
  runs a list of `[{"tool","args"}]` under one session over
  per-call CLI spawns.

## Call efficiently

- Never inline JSON on a Windows shell command line — quote
  mangling is silent and total. Author args as files.
- Batch independent reads in one run; run dependent chains
  (launch, wait, paste, save) sequentially in one run.
- Screenshots: pass `scale` below 1 unless pixels matter. Prefer
  `desktop_state` and `wait_for` over loops. Verify with data
  reads where a screenshot would only confirm text.
- Long work: job mode (`submit_job`, poll `job_status`).

## Safety contract

- The deployment may run approval-free (`dontAsk`): writes then
  execute silently on a live desktop. Reads first, always.
- Verify the DESTINATION before any input: exact window title,
  empty-or-expected target. Fresh launches restore old tabs.
- Paste long text (`paste_text`), point with `uia_*`, never close
  windows holding others' unsaved tabs.
- On any surprise: stop writes, screenshot, reassess.

## Muse fast path (this machine)

- Base is loopback. Pair once under your own name and send that
  token on every call so the journal stamps your fingerprint.
- Reuse the cached batch driver when present: one handshake,
  many calls, args from files. New chains go in one calls file.

## Known live quirks (2026-11-04)

- `WsConnected`/`WsDisconnected` are typed journal events in this
  tree - verify they are deployed before relying on presence.
- Two clients share one journal; reads in this tree stamp the
  presenting token fingerprint plus the job submitter.
- `doctor_jobs` is the jobs/infra health tool (listener, tunnel,
  token, journal, ESTOP).
