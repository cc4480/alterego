# Development & contributing

## Repo layout

```
alterego/
├─ pc-agent/            the Windows half (runs on the PC)
│  ├─ server.py         entry point: MCP server, routes, pairing, chat relay
│  ├─ tool_wrappers*.py MCP-facing signatures/docstrings → ALL_TOOLS
│  ├─ toolcall.py       the pipeline every call flows through
│  ├─ approval.py       native Yes/No dialog + permission postures
│  ├─ tool_profiles.py  per-tool risk profiles → approval tiers
│  ├─ pairing.py        pairing codes + brute-force lockout
│  ├─ events.py         the event-sourced operation log
│  ├─ hooks.py          PreToolUse / PostToolUse policy hooks
│  ├─ command_rules.py  shell allow/deny patterns (a built-in hook)
│  ├─ jobs.py           the journal-first background job layer
│  ├─ seams/
│  │  ├─ definitions/   tool declarations (name, group, doc, arg schema)
│  │  ├─ providers/
│  │  │  ├─ windows/    the real implementations
│  │  │  └─ mock/       in-memory fakes the test suite runs against
│  │  └─ registry.py    tool name → provider function (PC_BRIDGE_PROVIDER)
│  ├─ chrome_cdp.py / browser_cdp.py   the two browser hands
│  ├─ tests/            the maintained pytest suite (mock provider)
│  └─ test_*.py         standalone suites (CDP fakes, legacy tool tests)
├─ bridge/              the operator half (any OS, stdlib only)
│  ├─ op_call.py        the operator CLI (sync + job mode)
│  ├─ token_store.py    saved session token (~/.config/pc-bridge/token)
│  ├─ pc_bridge.py      the older minimal CLI (kept working)
│  ├─ mock_mcp_server.py  fake PC for client work without a PC
│  └─ tools.json        the tool schemas the mock serves
├─ scripts/             PowerShell: autostart installer, launcher, watchdog,
│                       validate_seams.py
├─ docs/                this documentation set
└─ demo-evidence/       screenshots from live verification sessions
```

House conventions worth knowing before you touch anything:

- **Definitions describe; providers implement; wrappers publish.**
  No wrapper imports a provider, and no provider knows about MCP.
- **Fail loud, never silently degrade** — an unknown provider, an
  unimplemented tool, or an invalid env value raises; it never
  quietly picks a behavior.
- **The event log is the primary record.** New behavior that acts on
  the PC should flow through `toolcall.call()` so it is journaled,
  hooked, and risk-snapshotted like everything else.

## Adding a tool

Five places, in order:

1. **Definition** — add a `ToolDef` in
   `pc-agent/seams/definitions/<group>.py` (groups: `read`, `write`,
   `files`, `pc`, `browser`, `recon`, `support`, `tasks`, `misc`),
   including the `platform_notes` honest-gaps text the validator
   requires.
2. **Implementations** — the Windows provider in
   `pc-agent/seams/providers/windows/<group>.py` (call the internal
   `_approved(tool, summary)` gate before any side effect if the
   tool writes), and the mock provider in
   `pc-agent/seams/providers/mock/<group>.py` so the suite can
   exercise it.
3. **Risk profile** — add an entry to `pc-agent/tool_profiles.py`
   (blast radius, recoverability, side effects, compensating
   action, tags). Skip this and the tool fails closed to the
   strictest tier (`always_ask`) — safe, but noisy.
4. **Wrapper** — add the public function (signature + docstring are
   the MCP schema) to the matching `pc-agent/tool_wrappers*.py`
   and to `ALL_TOOLS`. Without this step the tool exists in the
   seams but is **not** on the MCP surface. (The `cdp_*` ops are
   exactly this state today: steps 1–3 done, step 4 pending.)
5. **Validate + test** — run the seams validator and the suite
   (below), and update [tools.md](tools.md).

## Validating the seams

```bash
python3 scripts/validate_seams.py
```

For every definition it checks: `platform_notes` present, the arg
schema matches the provider function's signature, declared result
keys match the keys the provider actually returns, and the
definition's approval tier equals `tool_profiles.approval_tier()`.
Note: at this commit the validator reports **19 pre-existing
failures across 13 tools** (`edit_file`, `exec_background`,
`exec_cancel`, `exec_status`, `read_file_range`, `screenshot`,
`search_filenames`, `search_files`, `shell_exec`, `shell_pwsh`,
`web_fetch`, `web_search`, `write_file`) — mostly schema/type and
tier-declaration drift in the definitions. Compare against the tree
you started from, and don't let your change add new ones.

## Running the tests

The maintained suite runs entirely against the **mock provider** —
no Windows machine needed. `pc-agent/tests/conftest.py` pins the
environment (`PC_BRIDGE_PROVIDER=mock`,
`PC_BRIDGE_PERMISSION_MODE=dontAsk`, per-test temp `%APPDATA%`)
before any agent module is imported.

```bash
cd pc-agent
python -m pytest tests/ -q        # 238 passed, 1 skipped at this commit
```

Standalone files at `pc-agent/test_*.py`:

- `test_chrome_cdp.py` / `test_browser_cdp.py` — fake-socket CDP
  suites (including the interleaved-frame regression for the
  flatten-mode failure).
- `test_tools_files.py` — **stale**: it pins a hardcoded wrapper
  count of 50 and currently fails against the 63-wrapper registry.
  Don't treat its failures as regressions; the count assertion
  needs updating to the registry as it stands.

## Client-side development without a PC

```bash
cd bridge
MOCK_TOKEN=secret python3 mock_mcp_server.py 8765 &
PC_BRIDGE_URL=http://127.0.0.1:8765 PC_BRIDGE_TOKEN=secret python3 op_call.py tools
```

The mock serves the real schemas (`bridge/tools.json`) with canned
responses. For server-side work, the mock *provider*
(`PC_BRIDGE_PROVIDER=mock`) plus the pytest suite covers most
behavior; anything touching real Win32 (dialogs, UIA, CDP against a
live browser) ultimately needs a pass on an actual PC — say so in
your change notes rather than implying coverage you don't have.
`pc-agent/smoke_chrome_cdp.py` is the on-PC first-light script for
the `cdp_*` hand (data-URL route, no external network).

## A note on `mcp` versions

The server uses the `mcp` **2.x** API (`mcp.server.mcpserver`,
`streamable_http_app`). Develop against the pinned environment
(`pc-agent/requirements.txt`: `mcp>=2.0`) — ideally the same
`pc-agent\.venv` the deployment uses — because a stray `mcp<2` in
the interpreter changes the server API surface and the transport
security defaults underneath you.
