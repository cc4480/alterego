# Security policy

AlterEgo gives an AI agent a supervised operator's seat on the owner's own
Windows PC. The security model is described in
[docs/security.md](docs/security.md). This file records how to report a
problem and — in the open — the findings of the independent audit the
project commissioned against itself.

## Reporting

Open a GitHub issue on this repository, or use GitHub's private
vulnerability reporting if the issue is sensitive. The bridge is designed
for a machine's owner to run against their own PC; reports about anything
beyond that scope are still welcome.

## Independent audit — 2026-11-04

On 2026-11-04, Muse Code 1.4.6 — itself a candidate client of this
bridge — audited the codebase at commit `4294c36`. The maintainer's agent
independently verified each material finding against the source before
anything below was accepted. Verified findings:

1. **Batch approval coverage gap (high).** The batch tool's approval
   dialog consults a hard-coded list of write tools that omits some
   tool classes (UI-automation and composite tools), while batch mode
   suppresses the per-tool gates those classes would otherwise pass
   through — so those actions could run without a dialog. The gate
   suppression is also process-global while active, which concurrent
   requests can inherit. The bridge's owner runs it in the
   approval-free `dontAsk` posture by deliberate choice, which makes
   these two findings moot in that posture; they matter in the default
   approval posture and are recorded here rather than hidden.
2. **Chat room loopback trust (medium).** The WebSocket chat room
   trusted connections arriving from `127.0.0.1`. Because the tunnel
   process also dials from loopback, anyone holding the tunnel URL
   could join the chat room under a participant name. Chat only — no
   tool execution — but the name a message wears could be spoofed.
3. **Macro approval honesty (medium).** `macro_record` / `macro_delete`
   are documented as requiring approval but did not invoke the approval
   gate, and an approval log event could be written for a dialog that
   never fired.
4. **Session attribution (medium).** Tool-call journal events carried a
   single server-side session identifier regardless of which client
   token was presented, so actions of different clients could not be
   attributed per client in the journal.

## Remediation status

Remediation has been implemented in this repository **by the auditor
itself** — Muse Code — server-side and agent-agnostic (no
client-specific branches), covering findings 2, 3, and 4: the chat room
requires a token and derives the participant name from it, approval logs
only record dialogs that actually fired, and journal events are stamped
with a fingerprint of the token that was presented (tokens themselves
are never logged). Findings under item 1 stay as documented above under
the owner's chosen posture. No fix is trusted on the auditor's word:
the landing commit (585881b, 2026-11-04) was independently verified by
the maintainer's agent — full test suite green, re-verified 519 passed,
0 failed after stale-test cleanup — and a fix counts only when the running
server carries it live. Deployment completed 2026-11-04: the running
server now carries the verified code, confirmed live the same day
(109 tools registered, including `doctor_jobs`).

Disclosed 2026-11-04, the same day as the audit, because this project's
claim is supervised autonomy — and supervision starts with publishing
what an audit found.
