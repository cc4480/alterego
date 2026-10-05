"""Full-pipeline tests: every tool through toolcall.call() with the mock
provider — hooks -> plan-mode -> execute -> audit + typed events.

Covers: result_keys contract for all 50 tools, hook allow/deny/modify,
plan-mode dry-run, approval tiers, and the expected event chain per call.
"""
import pytest

import approval
import hooks
import toolcall
from seams import definitions, registry
from seams.providers import mock as mock_state
from conftest import events_for, read_events

# Canonical args for tools that need no cross-test state. task_checkpoint
# and task_status are exercised in test_task_lifecycle instead.
ARGS = {
    "screenshot": {}, "list_windows": {}, "system_info": {},
    "list_dir": {"path": ""}, "read_file": {"path": "seed.txt"},
    "clipboard_get": {},
    "focus_window": {"hwnd": 12345}, "close_window": {"hwnd": 12345},
    "type_text": {"text": "hi"}, "shell_exec": {"command": "echo hello"},
    "hotkey": {"keys": "ctrl+c"}, "mouse_move": {"x": 10, "y": 20},
    "mouse_click": {"x": 10, "y": 20}, "mouse_scroll": {},
    "minimize_window": {"hwnd": 1}, "maximize_window": {"hwnd": 1},
    "kill_process": {"pid": 1234}, "clipboard_set": {"text": "abc"},
    "paste_text": {"text": "abc"},
    "write_file": {"path": "t.txt", "content": "x"},
    "edit_file": {"path": "seed.txt", "old_text": "seed",
                  "new_text": "SEED"},
    "delete_file": {"path": "todelete.txt"}, "create_dir": {"path": "nd"},
    "copy_file": {"src": "seed.txt", "dst": "copy.txt"},
    "move_file": {"src": "tomove.txt", "dst": "moved.txt"},
    "file_info": {"path": "seed.txt"},
    "active_window": {}, "idle_seconds": {}, "list_processes": {},
    "notify": {"title": "t", "message": "m"}, "speak": {"text": "hi"},
    "set_volume": {"level": 50}, "power": {"action": "shutdown"},
    "browser_snapshot": {},
    "browser_navigate": {"url": "https://example.com"},
    "browser_click": {"text": "OK"},
    "browser_fill": {"label": "q", "text": "x"},
    "browser_eval": {"js": "1+1"},
    "http_headers": {"url": "https://example.com"},
    "dns_query": {"domain": "example.com"},
    "tls_info": {"host": "example.com"},
    "tcp_check": {"host": "example.com", "ports": [80, 443]},
    "shell_pwsh": {"script": "echo hi"},
    "batch": {"calls": [{"tool": "idle_seconds", "args": {}}]},
    "task_create": {"goal": "g", "plan": ["a", "b"]},
    "memory_recall": {"query": "x"}, "doctor": {},
    "arbitrate": {"trajectories": [["screenshot"], ["shell_exec"]]},
}

ALL_NAMES = [d.name for d in definitions.ALL_DEFS]
PARAM_NAMES = [n for n in ALL_NAMES
               if n not in ("task_checkpoint", "task_status")]
assert len(PARAM_NAMES) == 48, len(PARAM_NAMES)


def _write_flag(name):
    return definitions.by_name(name).approval_tier != "silent"


def test_all_50_tools_resolve():
    """Gate 1: every definition resolves to a callable in the mock."""
    for name in ALL_NAMES:
        assert callable(registry.resolve(name)), name
    assert callable(registry.resolve("arbitrate_tool"))  # MCP alias


@pytest.mark.parametrize("name", PARAM_NAMES)
def test_result_keys_exact(name):
    """Mock returns EXACTLY the ToolDef's result_keys through the pipeline."""
    defn = definitions.by_name(name)
    result = toolcall.call(name, ARGS[name], write=_write_flag(name))
    # A pipeline failure is exactly {"error": <str>}; tls_info legitimately
    # has an "error" result key, so only the single-key envelope counts.
    assert not (set(result.keys()) == {"error"}
                and isinstance(result["error"], str)), \
        f"{name}: {result['error']}"
    assert set(result.keys()) == set(defn.result_keys), \
        f"{name}: got {sorted(result.keys())}, want {defn.result_keys}"


def test_task_lifecycle_result_keys():
    created = toolcall.call("task_create", {"goal": "g", "plan": ["a"]})
    assert set(created) == {"task_id", "goal", "steps"}
    cp = toolcall.call("task_checkpoint",
                       {"task_id": created["task_id"], "step": 0,
                        "result": "done"})
    assert set(cp) == {"task_id", "step", "done"}
    st = toolcall.call("task_status", {"task_id": created["task_id"]})
    assert set(st) == set(definitions.by_name("task_status").result_keys)
    assert st["done_steps"] == 1
    listed = toolcall.call("task_status", {})
    assert any(t["task_id"] == created["task_id"]
               for t in listed["tasks"])


def test_hook_deny(_clean_hooks):
    hooks.register_hook("PreToolUse", "delete_file",
                        lambda tool, args: ("deny", "policy: no deletes"),
                        name="deny_deletes")
    result = toolcall.call("delete_file", {"path": "todelete.txt"},
                           write=True)
    assert result == {"error": "denied by hook: policy: no deletes"}
    # ApprovalRequested/HookEvaluated carry no "tool" key in their data,
    # so read the full per-test event stream (APPDATA is isolated).
    evts = read_events()
    types = [e["type"] for e in evts]
    assert types == ["ToolCalled", "HookEvaluated", "ToolDenied"]
    denied = evts[-1]
    assert denied["data"]["denied_by"] == "hook"
    assert denied["caused_by"] == evts[0]["event_id"]
    # File survived the denial.
    assert "todelete.txt" in str(mock_state.FS)


def test_hook_modify_args(_clean_hooks):
    def upper(tool, args):
        return "modify", {"text": args["text"].upper()}
    hooks.register_hook("PreToolUse", "type_text", upper, name="upper")
    result = toolcall.call("type_text", {"text": "hi"}, write=True)
    assert result == {"typed_chars": 2}  # "HI"
    assert mock_state.CALLS[-1]["args"] == {"text": "HI"}


def test_hook_post_replace(_clean_hooks):
    hooks.register_hook(
        "PostToolUse", "idle_seconds",
        lambda tool, args, result: {"idle_seconds": 999}, name="override")
    result = toolcall.call("idle_seconds", {})
    assert result == {"idle_seconds": 999}


def test_plan_mode_forces_dry_run(_clean_hooks, monkeypatch):
    monkeypatch.setattr(approval, "PERMISSION_MODE", "plan")
    result = toolcall.call("write_file",
                           {"path": "plan.txt", "content": "x"}, write=True)
    assert result["dry_run"] is True
    assert result["would"] == "create"
    assert not any(k.endswith("plan.txt") for k in mock_state.FS)


def test_plan_mode_stub_for_non_file_tool(monkeypatch):
    monkeypatch.setattr(approval, "PERMISSION_MODE", "plan")
    result = toolcall.call("type_text", {"text": "hi"}, write=True)
    assert result["plan_mode"] is True
    assert result["would_execute"] == "type_text"
    assert mock_state.CALLS == []  # nothing journaled: not executed


def test_read_call_event_chain():
    toolcall.call("idle_seconds", {})
    evts = events_for("idle_seconds", read_events())
    types = [e["type"] for e in evts]
    assert types == ["ToolCalled", "ToolCompleted"]
    assert evts[1]["caused_by"] == evts[0]["event_id"]
    assert evts[1]["data"]["plan_mode"] is False


def test_write_call_event_chain_with_approval_preview():
    toolcall.call("shell_exec", {"command": "echo hi"}, write=True)
    evts = read_events()  # ApprovalRequested has no "tool" key in data
    types = [e["type"] for e in evts]
    assert types == ["ToolCalled", "ApprovalRequested", "ToolCompleted"]
    appr = evts[1]
    assert appr["data"]["tier"] == "always_ask"
    assert appr["data"]["dialog_shown"] is False
    assert appr["data"]["skipped_reason"] == "dontAsk"
    assert appr["data"]["dialog_result"] == "yes"
    assert appr["caused_by"] == evts[0]["event_id"]


def test_strict_mode_unknown_tool_raises():
    with pytest.raises(RuntimeError, match="not implemented by the 'mock'"):
        toolcall.call("definitely_not_a_tool", {})


def test_lenient_mode_returns_generic_mock(monkeypatch):
    monkeypatch.setenv("PC_BRIDGE_MOCK_MODE", "lenient")
    saved_active, saved_map = registry._ACTIVE, dict(registry._PROVIDERS)
    registry._ACTIVE, registry._PROVIDERS = None, {}
    try:
        fn = registry.resolve("mystery_tool")
        assert fn(tool="x") == {"mock": True, "tool": "mystery_tool",
                                "args": {"tool": "x"}}
    finally:
        registry._ACTIVE, registry._PROVIDERS = saved_active, saved_map


def test_audit_log_written(tmp_path):
    toolcall.call("idle_seconds", {})
    from auth import app_dir
    lines = (app_dir() / "audit.log").read_text(
        encoding="utf-8").splitlines()
    entry = [l for l in lines if '"tool": "idle_seconds"' in l]
    assert entry, "audit.log has no idle_seconds entry"
