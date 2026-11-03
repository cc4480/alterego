"""Portmanteau slice tests (Batch 6): dispatch envelope, forbidden
targets, write-flag table, mock dispatch, profile tiers.

Runs under tests/conftest.py: mock provider, dontAsk mode, per-test
isolated appdata. Only this slice's tools are exercised here; count
bumps (PARAM_NAMES / test_tools_files / validator) are the merge
agent's job and are expected to fail in a slice copy.
"""
import inspect

import pytest

import portmanteau
import toolcall
from seams import definitions, registry
from tool_profiles import approval_tier
from seams.providers import mock as mock_state
from conftest import read_events

TIER_ORDER = {"silent": 0, "routine": 1, "ask": 2, "always_ask": 3}

# Mock-safe call per portmanteau tool (member succeeds in the mock).
ARGS = {
    "mouse": {"operation": "move", "args": {"x": 10, "y": 20}},
    "keyboard": {"operation": "type", "args": {"text": "hi"}},
    "window": {"operation": "list", "args": {}},
    "uia": {"operation": "find", "args": {"window": "w", "name_rx": "x"}},
    "file": {"operation": "read", "args": {"path": "seed.txt"}},
    "process": {"operation": "list", "args": {}},
    "browser": {"operation": "snapshot", "args": {}},
    "cdp": {"operation": "snapshot", "args": {}},
    "clipboard": {"operation": "get", "args": {}},
    "exec": {"operation": "shell", "args": {"command": "echo hello"}},
}

# Every member the op maps may reach (50 tools, verified against defs).
ALL_MEMBERS = {
    "mouse_move", "mouse_click", "mouse_scroll",
    "type_text", "hotkey", "paste_text",
    "list_windows", "active_window", "focus_window", "close_window",
    "minimize_window", "maximize_window",
    "uia_find", "uia_click", "uia_set_text",
    "read_file", "read_file_range", "search_files", "search_filenames",
    "list_dir", "write_file", "edit_file", "delete_file", "create_dir",
    "copy_file", "move_file", "file_info",
    "list_processes", "kill_process",
    "browser_snapshot", "browser_navigate", "browser_click",
    "browser_fill", "browser_eval",
    "cdp_launch", "cdp_connect", "cdp_targets", "cdp_navigate",
    "cdp_snapshot", "cdp_click", "cdp_type", "cdp_evaluate",
    "cdp_readback", "cdp_close",
    "clipboard_get", "clipboard_set",
    "shell_exec", "shell_pwsh", "exec_background", "exec_status",
    "exec_cancel",
}


def test_op_maps_cover_exactly_the_members():
    reached = set()
    for tool, ops in portmanteau.OP_MAPS.items():
        assert ops, tool
        for verb, member in ops.items():
            assert definitions.by_name(member) is not None, member
            assert not portmanteau._is_forbidden(member), member
            reached.add(member)
    assert reached == ALL_MEMBERS, reached ^ ALL_MEMBERS
    assert set(portmanteau.OP_MAPS) == set(ARGS)


def test_forbidden_guard():
    assert portmanteau._is_forbidden("batch")
    for name in portmanteau.OP_MAPS:
        assert portmanteau._is_forbidden(name)
    assert portmanteau._is_forbidden("macro_record")
    assert portmanteau._is_forbidden("macro_replay")
    assert not portmanteau._is_forbidden("mouse_move")
    assert not portmanteau._is_forbidden("read_file")


def test_unknown_operation_rejected_with_valid_list():
    with pytest.raises(ValueError, match="unknown operation"):
        portmanteau.resolve_member("mouse", "teleport")
    with pytest.raises(ValueError, match="move"):
        portmanteau.resolve_member("mouse", "teleport")
    with pytest.raises(ValueError, match="unknown portmanteau tool"):
        portmanteau.resolve_member("nope", "move")
    with pytest.raises(ValueError):
        portmanteau.resolve_member("mouse", "")
    with pytest.raises(ValueError):
        portmanteau.resolve_member("mouse", None)


def test_non_dict_args_rejected():
    with pytest.raises(ValueError, match="args must be an object"):
        portmanteau.dispatch("mouse", "move", args=["x"])
    with pytest.raises(ValueError, match="args must be an object"):
        portmanteau.dispatch("mouse", "move", args="x")


def test_write_flag_table():
    # Reads execute (False), writes stub in plan mode (True), file
    # operations honor dry_run, unknown operations fail closed (True).
    assert portmanteau.write_flag("mouse", "move") is True
    assert portmanteau.write_flag("mouse", "click") is True
    assert portmanteau.write_flag("keyboard", "type") is True
    assert portmanteau.write_flag("window", "list") is False
    assert portmanteau.write_flag("window", "active") is False
    assert portmanteau.write_flag("window", "focus") is True
    assert portmanteau.write_flag("window", "close") is True
    assert portmanteau.write_flag("uia", "find") is False
    assert portmanteau.write_flag("uia", "click") is True
    assert portmanteau.write_flag("file", "read") is False
    assert portmanteau.write_flag("file", "info") is False
    assert portmanteau.write_flag("file", "search") is False
    assert portmanteau.write_flag("file", "write", {}) is True
    assert portmanteau.write_flag("file", "write",
                                  {"dry_run": True}) is False
    assert portmanteau.write_flag("file", "delete",
                                  {"dry_run": False}) is True
    assert portmanteau.write_flag("file", "write", None) is True
    assert portmanteau.write_flag("process", "list") is False
    assert portmanteau.write_flag("process", "kill") is True
    assert portmanteau.write_flag("browser", "snapshot") is False
    assert portmanteau.write_flag("browser", "eval") is True
    assert portmanteau.write_flag("cdp", "snapshot") is False
    assert portmanteau.write_flag("cdp", "readback") is False
    assert portmanteau.write_flag("cdp", "close") is True
    assert portmanteau.write_flag("clipboard", "get") is False
    assert portmanteau.write_flag("clipboard", "set") is True
    assert portmanteau.write_flag("exec", "status") is False
    assert portmanteau.write_flag("exec", "shell") is True
    assert portmanteau.write_flag("exec", "cancel") is True
    assert portmanteau.write_flag("mouse", "teleport") is True


def test_profile_tier_is_most_restrictive_member_tier():
    for tool, ops in portmanteau.OP_MAPS.items():
        worst = max(TIER_ORDER[approval_tier(m)] for m in ops.values())
        assert TIER_ORDER[approval_tier(tool)] == worst, tool
        assert definitions.by_name(tool).approval_tier == approval_tier(tool)


def test_all_portmanteau_resolve_in_mock():
    for name in ARGS:
        assert callable(registry.resolve(name)), name


@pytest.mark.parametrize("name", sorted(ARGS))
def test_envelope_shape_and_result_keys(name):
    defn = definitions.by_name(name)
    result = toolcall.call(name, dict(ARGS[name]), write=True)
    assert set(result) == {"operation", "tool", "result"}, result
    assert set(result) == set(defn.result_keys)
    assert result["operation"] == ARGS[name]["operation"]
    assert result["tool"] in portmanteau.OP_MAPS[name].values()
    assert isinstance(result["result"], dict)


def test_mock_dispatch_read_and_write():
    r = toolcall.call("window", {"operation": "list", "args": {}},
                      write=False)
    assert r["tool"] == "list_windows"
    assert "windows" in r["result"], r
    r = toolcall.call("keyboard", {"operation": "type",
                                   "args": {"text": "hi"}}, write=True)
    assert r == {"operation": "type", "tool": "type_text",
                 "result": {"typed_chars": 2, "window": "Mock Window",
                            "hwnd": 12345, "focus_changed": False}}, r


def test_args_filtered_and_nones_dropped():
    toolcall.call("mouse", {"operation": "move",
                            "args": {"x": 10, "y": 20, "bogus": 1,
                                     "button": None}}, write=True)
    last = mock_state.CALLS[-1]
    assert last["tool"] == "mouse_move", last
    assert last["args"] == {"x": 10, "y": 20}, last


def test_unknown_operation_is_error_envelope_through_pipeline():
    result = toolcall.call("mouse", {"operation": "teleport", "args": {}},
                           write=True)
    assert set(result) == {"error"}, result
    assert "unknown operation" in result["error"], result


def test_events_fire_for_both_calls():
    toolcall.call("mouse", {"operation": "move",
                            "args": {"x": 1, "y": 2}}, write=True)
    evts = read_events()
    called = [e["data"]["tool"] for e in evts
              if e["type"] == "ToolCalled"]
    assert "mouse" in called, called
    assert "mouse_move" in called, called
    assert called.index("mouse") < called.index("mouse_move"), called
    completed = [e["data"]["tool"] for e in evts
                 if e["type"] == "ToolCompleted"]
    assert "mouse" in completed and "mouse_move" in completed, completed


def test_providers_delegate_without_approval_gate():
    """Portmanteau provider fns must contain no _approved call: the
    member's own gate fires. (Static guard; behavior is covered by the
    pipeline tests.)"""
    from seams.providers.windows import write as win_write
    from seams.providers.windows import files as win_files
    from seams.providers.windows import browser as win_browser
    from seams.providers.windows import support as win_support
    from seams.providers.mock import write as mock_write
    from seams.providers.mock import files as mock_files
    from seams.providers.mock import browser as mock_browser
    from seams.providers.mock import support as mock_support
    fns = [win_write.mouse, win_write.keyboard, win_write.window,
           win_write.uia, win_write.clipboard, win_write.process,
           win_files.file, win_browser.browser, win_browser.cdp,
           win_support.exec,
           mock_write.mouse, mock_write.keyboard, mock_write.window,
           mock_write.uia, mock_write.clipboard, mock_write.process,
           mock_files.file, mock_browser.browser, mock_browser.cdp,
           mock_support.exec]
    assert len(fns) == 20
    for fn in fns:
        src = inspect.getsource(fn)
        assert "_approved" not in src, fn.__name__
        assert "portmanteau.dispatch" in src, fn.__name__
