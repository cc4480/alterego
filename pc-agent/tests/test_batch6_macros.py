"""Batch 6 macros slice: record/list/replay/delete, validation,
persistence, fresh-read replay, prompts, and resources.

Dedicated lifecycle test: macro_replay/macro_delete are EXCLUDED from
PARAM_NAMES (persistence across parametrize cases is not guaranteed
under per-test appdata isolation), so the full record -> replay ->
delete cycle lives here, not in test_pipeline_mock.py.

Runs under the mock provider (conftest pins PC_BRIDGE_PROVIDER=mock
and dontAsk, APPDATA per-test). Provider functions are called directly:
the defs/registry group wiring is the merge agent's job, so registry
resolution of macro_* is expected to fail in a slice copy.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import macros
from seams import definitions
from seams.definitions.macros import ALL_MACRO_DEFS
from tool_profiles import approval_tier


@pytest.fixture(autouse=True)
def _clean_macros():
    macros.reset()
    yield
    macros.reset()


def _record(name="peek", steps=None):
    if steps is None:
        steps = [{"tool": "idle_seconds", "args": {}}]
    return macros.record_macro(name, steps)


def test_defs_shape():
    assert [d.name for d in ALL_MACRO_DEFS] == [
        "macro_record", "macro_list", "macro_replay", "macro_delete"]
    assert all(d.group == "macros" for d in ALL_MACRO_DEFS)
    tiers = {d.name: d.approval_tier for d in ALL_MACRO_DEFS}
    assert tiers == {"macro_record": "routine", "macro_list": "silent",
                     "macro_replay": "always_ask",
                     "macro_delete": "routine"}
    keys = {d.name: d.result_keys for d in ALL_MACRO_DEFS}
    assert keys["macro_record"] == ["name", "steps", "step_count"]
    assert keys["macro_list"] == ["macros", "count"]
    assert keys["macro_replay"] == ["macro", "calls", "results"]
    assert keys["macro_delete"] == ["name", "deleted"]


def test_profile_tiers_match_defs():
    assert approval_tier("macro_record") == "routine"
    assert approval_tier("macro_list") == "silent"
    assert approval_tier("macro_replay") == "always_ask"
    assert approval_tier("macro_delete") == "routine"
    from jobs import RESOURCE_MAP
    assert RESOURCE_MAP["macro_replay"] == "desktop"


def test_record_list_delete_lifecycle():
    r = _record("peek")
    assert r == {"name": "peek",
                 "steps": [{"tool": "idle_seconds", "args": {}}],
                 "step_count": 1}
    _record("alpha", [{"tool": "active_window", "args": {}}])
    listed = macros.list_macros()
    assert listed["count"] == 2
    assert [m["name"] for m in listed["macros"]] == ["alpha", "peek"]
    assert macros.delete_macro("peek") == {"name": "peek", "deleted": True}
    assert macros.list_macros()["count"] == 1
    with pytest.raises(ValueError, match="unknown macro"):
        macros.delete_macro("peek")
    with pytest.raises(ValueError, match="unknown macro"):
        macros.get_macro_steps("nope")


def test_record_overwrite_and_name_rules():
    _record("peek")
    r = _record("peek", [{"tool": "active_window", "args": {}},
                         {"tool": "idle_seconds", "args": {}}])
    assert r["step_count"] == 2  # overwrite, not duplicate
    assert macros.list_macros()["count"] == 1
    for bad in ("", "Peek", "has space", "a" * 33, "has.dot",
                "UPPER", None, 7):
        with pytest.raises(ValueError, match="name must match"):
            macros.record_macro(bad, [{"tool": "idle_seconds",
                                       "args": {}}])
    assert macros.record_macro("ok-name_1", [{"tool": "idle_seconds",
                                              "args": {}}])["name"] \
        == "ok-name_1"


def test_step_validation():
    good = [{"tool": "idle_seconds", "args": {}}]
    for bad_steps, match in (
            ([], "1-20"),
            ([{"tool": "idle_seconds", "args": {}}] * 21, "1-20"),
            ("idle_seconds", "1-20"),
            ([{"args": {}}], "must look like"),
            ([{"tool": "no_such_tool", "args": {}}], "unknown tool"),
            ([{"tool": "idle_seconds", "args": {"bogus": 1}}],
             "has no argument"),
            ([{"tool": "idle_seconds", "args": []}], "must be an object"),
            ([{"tool": "batch", "args": {"calls": []}}], "no nesting"),
            ([{"tool": "macro_replay", "args": {"name": "x"}}],
             "no nesting"),
            ([{"tool": "mouse", "args": {"operation": "move"}}],
             "no nesting"),
            ([{"tool": "file", "args": {"operation": "read"}}],
             "no nesting"),
            ([{"tool": "exec", "args": {"operation": "run"}}],
             "no nesting"),
            ([{"tool": "cdp", "args": {"operation": "snapshot"}}],
             "no nesting"),
    ):
        with pytest.raises(ValueError, match=match):
            macros.record_macro("v", bad_steps)
    assert macros.record_macro("v", good)["step_count"] == 1


def test_replay_mock_fresh_reads():
    from seams.providers import mock as mock_state
    from seams.providers.mock import macros as mock_macros
    _record("peek", [{"tool": "clipboard_get", "args": {}}])
    first = mock_macros.macro_replay("peek")
    assert first["macro"] == "peek" and first["calls"] == 1
    assert first["results"][0]["result"]["text"] == "mock clipboard contents"
    # Live state is re-read on every replay: change the clipboard,
    # replay, and the new value comes back (nothing memoized).
    mock_state.CLIPBOARD["text"] = "fresh value"
    second = mock_macros.macro_replay("peek")
    assert second["results"][0]["result"]["text"] == "fresh value"


def test_replay_order_and_result_keys():
    from seams.providers.mock import macros as mock_macros
    _record("two", [{"tool": "idle_seconds", "args": {}},
                    {"tool": "active_window", "args": {}}])
    r = mock_macros.macro_replay("two")
    assert r["calls"] == 2
    assert [e["tool"] for e in r["results"]] == [
        "idle_seconds", "active_window"]
    assert set(r) == {"macro", "calls", "results"}
    assert set(mock_macros.macro_list()) == {"macros", "count"}


def test_replay_revalidates_stale_steps(tmp_path, monkeypatch):
    import auth
    from seams.providers.mock import macros as mock_macros
    _record("stale", [{"tool": "idle_seconds", "args": {}}])
    # Rewrite the store behind the cache with a step naming a tool
    # that does not exist: replay must fail loud, not run stale args.
    store = (auth.app_dir() / "macros.json").resolve()
    store.write_text(json.dumps({
        "stale": {"name": "stale",
                  "steps": [{"tool": "no_such_tool", "args": {}}]}}),
        encoding="utf-8")
    macros.reset()
    with pytest.raises(ValueError, match="unknown tool"):
        mock_macros.macro_replay("stale")


def test_persistence_across_cache_reset(tmp_path, monkeypatch):
    import auth
    _record("kept", [{"tool": "idle_seconds", "args": {}}])
    macros.reset()  # drop the cache: the store file is re-read
    assert macros.list_macros()["count"] == 1
    assert macros.get_macro_steps("kept") == [
        {"tool": "idle_seconds", "args": {}}]


def test_stores_are_per_appdata_dir(tmp_path, monkeypatch):
    import auth
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    monkeypatch.setenv("APPDATA", str(dir_a))
    macros.reset()
    _record("in-a")
    monkeypatch.setenv("APPDATA", str(dir_b))
    macros.reset()
    assert macros.list_macros()["count"] == 0
    _record("in-b")
    monkeypatch.setenv("APPDATA", str(dir_a))
    macros.reset()
    assert [m["name"] for m in macros.list_macros()["macros"]] == ["in-a"]


def test_corrupt_store_fails_loud(tmp_path, monkeypatch):
    import auth
    store = (auth.app_dir() / "macros.json").resolve()
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{not json", encoding="utf-8")
    macros.reset()
    with pytest.raises(ValueError, match="corrupt"):
        macros.list_macros()


def test_windows_provider_replay_and_gate_restore():
    from seams.providers.windows import macros as win_macros
    from seams.providers.windows import support as support_mod
    _record("peek", [{"tool": "idle_seconds", "args": {}}])
    original = support_mod._approved
    r = win_macros.macro_replay("peek")  # dontAsk: no dialog, runs live
    assert r["macro"] == "peek" and r["calls"] == 1
    assert support_mod._approved is original  # mute restored
    assert win_macros.macro_list()["count"] == 1
    assert win_macros.macro_delete("peek")["deleted"] is True
    with pytest.raises(ValueError, match="unknown macro"):
        win_macros.macro_replay("peek")


def test_prompts():
    import bridge_prompts
    health = bridge_prompts.pc_health_check()
    assert isinstance(health, str) and "doctor" in health
    found = bridge_prompts.find_window("Notepad")
    assert "Notepad" in found and "focus_window" in found
    summary = bridge_prompts.summarize_file("notes.md")
    assert "notes.md" in summary and "read_file" in summary
    for fn, bad in ((bridge_prompts.find_window, ""),
                    (bridge_prompts.find_window, None),
                    (bridge_prompts.summarize_file, "  ")):
        with pytest.raises(ValueError):
            fn(bad)


def test_resources():
    import bridge_resources
    tools = json.loads(bridge_resources.tools_text())
    assert tools["total"] == len(definitions.ALL_DEFS)
    assert tools["count"] == tools["total"]
    assert any(t["name"] == "screenshot" for t in tools["tools"])
    _record("peek")
    listed = json.loads(bridge_resources.macros_text())
    assert listed["count"] == 1
    assert listed["macros"][0]["name"] == "peek"


def test_server_registers_prompts_and_resources():
    pytest.importorskip("mcp")  # SDK-less venvs skip; merge venv has it
    import asyncio
    import server
    prompts = asyncio.run(server.mcp.list_prompts())
    assert sorted(p.name for p in prompts) == [
        "find_window", "pc_health_check", "summarize_file"]
    got = asyncio.run(server.mcp.get_prompt(
        "summarize_file", {"path": "x.md"}))
    assert got.messages[0].content.text.startswith("Read the file")
    resources = asyncio.run(server.mcp.list_resources())
    uris = sorted(str(r.uri) for r in resources)
    assert "pc://bridge/tools" in uris
    assert "pc://bridge/macros" in uris
