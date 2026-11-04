# AlterEgo meets Muse — Meta Hackathon Entry

**One line:** an open Windows PC bridge that gives a Muse agent a second
self on a real machine — 109 MCP tools, human-gated writes, wired live,
audited live, fixed live, all in one session.

## The problem

Coding agents live in chat windows. Computer-use agents live behind
closed products. There is no open bridge where an AI agent gets a real
operator's seat — screen, mouse, keyboard, shell, files, browser —
with the human keeping final say over every dangerous action.

## The solution (this repo)

AlterEgo: a Windows PC agent (MCP server, Streamable HTTP, loopback
only) reached through a Cloudflare tunnel the owner starts. Pairing
codes mint bearer tokens; read tools run silent; every write pops a
native Yes/No dialog on the owner's screen (30s timeout = deny);
every call lands in an event-sourced audit log. Job mode survives
flaky tunnels; hooks and command rules let the owner inject policy.

## What happened in this session (the full arc)

Starting from a fresh clone, a Muse session:

1. **Audited the codebase** — found four approval/auth gaps (stale
   batch write-list, process-global gate muting, `/ws` localhost
   trust that predates the tunnel, ungated macro writes with lying
   receipts) plus a duplicate `doctor` registration shadowing the
   MCP surface.
2. **Fixed two of them with tests green** — per-client session
   attribution (the presenting token's fingerprint, journaled
   through background jobs with a SQLite migration) and the
   `doctor` de-shadowing (`doctor_jobs`, 108 → 109 tools).
   New suite: 5/5; full suite: 466 passed, 6 pre-existing
   failures identical on the unmodified baseline.
3. **Wired itself in as a second client** — health, token, system
   info, screenshot; proved it can see the screen.
4. **Drove the PC end to end** — researched MCP through the
   bridge's own Edge tools, wrote the report into Notepad via
   launch/wait/paste/save, verified by read-back and screenshot.
5. **Kept the receipts** — including one honest failure (a paste
   into the wrong restored tab, detected, byte-verified repair)
   and two new live findings (chat presence events failing
   validation; a torn event-log line).

## Live proof

- `demo-evidence/final-muse-wired.png` — Muse session beside the
  live bridge server log.
- `demo-evidence/final-muse-notepad-report.png` — the researched
  report, all 4,677 chars, in Notepad via bridge input tools.
- `docs/muse.md` — the operator guide written for Muse sessions.
- `DEMO.md` — the 5-minute runbook reproducing all of it.

## Why it matters

Agent + PC is the shape of real computer use. This entry shows the
whole loop open and inspectable: the bridge, the client, the audit
trail, the bugs found by the agent itself, and the fixes with
proof. No black boxes — the evidence directory is the pitch.
