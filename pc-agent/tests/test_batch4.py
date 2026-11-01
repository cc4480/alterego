"""Batch 4: annotations, tool search, argument completion, pagination.

Annotations are pure derivations from tool_profiles (no tier changes).
search_tools / complete_arg / pagination run through the mock pipeline
and the mock providers directly, like the Batch 2/3 tests.
"""
import pytest

import toolcall
from seams import definitions
from seams.providers.mock.misc import complete_arg as mock_complete_arg
from seams.providers.mock.misc import search_tools as mock_search_tools
from tool_profiles import annotations_for


# ---- annotations_for ------------------------------------------------------

def test_annotations_read_only_tool():
    assert annotations_for("screenshot") == {
        "read_only_hint": True, "destructive_hint": False,
        "idempotent_hint": True, "open_world_hint": False}


def test_annotations_destructive_tool():
    hints = annotations_for("delete_file")
    assert hints["destructive_hint"] is True
    assert hints["read_only_hint"] is False
    assert hints["idempotent_hint"] is False


def test_annotations_network_read():
    hints = annotations_for("web_fetch")
    assert hints["read_only_hint"] is True
    assert hints["open_world_hint"] is True
    assert hints["destructive_hint"] is False


def test_annotations_write_tool():
    hints = annotations_for("type_text")
    assert hints == {"read_only_hint": False, "destructive_hint": False,
                     "idempotent_hint": False, "open_world_hint": False}


def test_annotations_unknown_tool_is_conservative():
    hints = annotations_for("no_such_tool_xyz")
    assert hints["destructive_hint"] is True
    assert hints["read_only_hint"] is False


def test_annotations_new_tools_are_silent_reads():
    for name in ("search_tools", "complete_arg"):
        assert annotations_for(name)["read_only_hint"] is True
        assert annotations_for(name)["destructive_hint"] is False


# ---- search_definitions (shared helper) ------------------------------------

def test_search_empty_query_lists_everything():
    res = definitions.search_definitions()
    assert res["total"] == len(definitions.ALL_DEFS)
    assert res["count"] == min(10, res["total"])
    names = [t["name"] for t in res["tools"]]
    assert names == sorted(names)


def test_search_name_match_first():
    res = definitions.search_definitions("screenshot")
    assert res["tools"][0]["name"] == "screenshot"


def test_search_terms_are_anded():
    res = definitions.search_definitions("window title")
    names = [t["name"] for t in res["tools"]]
    assert "list_windows" in names
    for t in res["tools"]:
        hay = f"{t['name']} {t['group']} {t['doc']}".lower()
        assert "window" in hay and "title" in hay


def test_search_group_filter_and_paging():
    res = definitions.search_definitions("", group="misc")
    assert res["total"] > 0
    assert all(t["group"] == "misc" for t in res["tools"])
    page1 = definitions.search_definitions("", max_results=2, offset=0)
    page2 = definitions.search_definitions("", max_results=2, offset=2)
    names1 = [t["name"] for t in page1["tools"]]
    names2 = [t["name"] for t in page2["tools"]]
    assert not set(names1) & set(names2)
    assert page1["total"] == page2["total"]


def test_search_no_match():
    res = definitions.search_definitions("zzz_no_such_keyword")
    assert res == {"query": "zzz_no_such_keyword", "count": 0, "total": 0,
                   "tools": []}


def test_search_tools_via_pipeline():
    res = toolcall.call("search_tools", {"query": "window"},
                        write=False)
    assert res["total"] >= 1
    assert any(t["name"] == "list_windows" for t in res["tools"])
    assert set(res) == {"query", "count", "total", "tools"}


# ---- complete_arg -----------------------------------------------------------

def test_complete_arg_windows_direct():
    res = mock_complete_arg("uia_find", "window", "Mock")
    assert res["values"] == ["Mock Window", "Mock Console"]
    assert res["total"] == 2


def test_complete_arg_windows_case_insensitive_prefix():
    res = mock_complete_arg("wait_for", "window", "mock c")
    assert res["values"] == ["Mock Console"]


def test_complete_arg_paths_direct():
    res = mock_complete_arg("read_file", "path", "se")
    assert res["values"] == ["seed.txt"]
    assert res["total"] == 1
    res = mock_complete_arg("read_file", "path", "")
    assert set(res["values"]) == {"seed.txt", "todelete.txt", "tomove.txt"}
    assert res["total"] == 3


def test_complete_arg_unknown_tool_and_pair_fail_loud():
    with pytest.raises(ValueError, match="unknown tool"):
        mock_complete_arg("no_such_tool_xyz", "window", "")
    with pytest.raises(ValueError, match="no completions"):
        mock_complete_arg("system_info", "window", "")
    with pytest.raises(ValueError, match="no completions"):
        mock_complete_arg("uia_find", "name_rx", "")


def test_complete_arg_via_pipeline_error_envelope():
    res = toolcall.call("complete_arg",
                        {"tool": "no_such_tool_xyz", "argument": "window"},
                        write=False)
    assert "error" in res


def test_complete_arg_via_pipeline():
    res = toolcall.call("complete_arg",
                        {"tool": "uia_find", "argument": "window",
                         "prefix": "Mock"},
                        write=False)
    assert res["values"] == ["Mock Window", "Mock Console"]
    assert set(res) == {"tool", "argument", "prefix", "values", "total"}


# ---- pagination --------------------------------------------------------------

def test_list_windows_pagination():
    all_res = toolcall.call("list_windows", {}, write=False)
    assert all_res["total"] == 2
    assert len(all_res["windows"]) == 2
    page = toolcall.call("list_windows", {"offset": 1, "limit": 1},
                         write=False)
    assert page == {"windows": [all_res["windows"][1]], "total": 2,
                    "offset": 1}
    beyond = toolcall.call("list_windows", {"offset": 99}, write=False)
    assert beyond["windows"] == [] and beyond["total"] == 2


def test_list_dir_pagination():
    res = toolcall.call("list_dir", {"path": ""}, write=False)
    assert res["total"] == 3
    assert {i["name"] for i in res["items"]} == {
        "seed.txt", "todelete.txt", "tomove.txt"}
    page = toolcall.call("list_dir", {"path": "", "limit": 1}, write=False)
    assert len(page["items"]) == 1 and page["total"] == 3
    assert page["offset"] == 0


def test_list_processes_pagination():
    res = toolcall.call("list_processes", {}, write=False)
    assert res["count"] == res["total"] and res["total"] > 0
    page = toolcall.call("list_processes", {"limit": 1}, write=False)
    assert page["count"] == 1 and page["total"] == res["total"]
    assert page["offset"] == 0


def test_query_events_pagination():
    # The log is append-only and self-recording: every query_events call
    # appends its own events, so totals grow between calls. Paging is
    # tested by advancing the offset and by an offset past the end.
    toolcall.call("system_info", {}, write=False)
    page1 = toolcall.call("query_events", {"limit": 1}, write=False)
    assert page1["total"] >= 1
    assert len(page1["events"]) == 1
    assert set(page1) == {"events", "total", "truncated"}
    page2 = toolcall.call("query_events", {"limit": 1, "offset": 1},
                          write=False)
    assert len(page2["events"]) == 1
    assert page2["events"][0]["event_id"] != page1["events"][0]["event_id"]
    beyond = toolcall.call("query_events",
                           {"limit": 10, "offset": 10 ** 6},
                           write=False)
    assert beyond["events"] == []
    assert beyond["total"] >= 1
