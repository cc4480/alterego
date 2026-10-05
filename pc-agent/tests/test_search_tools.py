"""Behavioral tests for the search tools: search_files, search_filenames,
read_file_range — through toolcall.call() with the mock provider.

conftest seeds: seed.txt ("seed content seed"), todelete.txt, tomove.txt.
"""
import pytest

import toolcall
from seams.providers import mock as mock_state


def _write(path, content):
    from seams.providers.mock import files as mock_files
    mock_files.write_file(path, content)


# ---- search_files ---------------------------------------------------------

def test_search_files_finds_seed():
    r = toolcall.call("search_files", {"pattern": "seed", "path": ""})
    assert r["truncated"] is False
    assert any(m["file"].endswith("seed.txt")
               and m["line_number"] == 1
               and m["match_text"] == "seed"
               for m in r["matches"])


def test_search_files_regex():
    r = toolcall.call("search_files",
                      {"pattern": r"seed\s+content", "path": ""})
    assert any(m["match_text"] == "seed content" for m in r["matches"])


def test_search_files_file_pattern_filter():
    _write("notes.md", "seed in markdown")
    r = toolcall.call("search_files", {"pattern": "seed", "path": "",
                                      "file_pattern": "*.md"})
    assert r["matches"], "expected a match in notes.md"
    assert all(m["file"].endswith(".md") for m in r["matches"])


def test_search_files_max_results_truncates():
    _write("big.txt", "\n".join(f"hit line {i}" for i in range(20)))
    r = toolcall.call("search_files",
                      {"pattern": "hit", "path": "",
                       "file_pattern": "big.txt", "max_results": 5})
    assert len(r["matches"]) == 5
    assert r["truncated"] is True


def test_search_files_no_match():
    r = toolcall.call("search_files",
                      {"pattern": "zzz_no_such_token", "path": ""})
    assert r["matches"] == [] and r["truncated"] is False


def test_search_files_invalid_regex():
    r = toolcall.call("search_files", {"pattern": "([", "path": ""})
    assert "error" in r and "invalid regex" in r["error"]


def test_search_files_outside_profile_denied():
    r = toolcall.call("search_files",
                      {"pattern": "x", "path": "../../.."})
    assert "error" in r


# ---- search_filenames -----------------------------------------------------

def test_search_filenames_finds_txt():
    r = toolcall.call("search_filenames",
                      {"pattern": "*.txt", "path": ""})
    assert r["truncated"] is False
    names = [f["path"].rsplit("/", 1)[-1] for f in r["files"]]
    assert "seed.txt" in names
    for f in r["files"]:
        assert set(f) == {"path", "size", "modified"}
        assert f["size"] > 0


def test_search_filenames_no_match():
    r = toolcall.call("search_filenames",
                      {"pattern": "*.zzz_no_ext", "path": ""})
    assert r["files"] == []


def test_search_filenames_max_results():
    for i in range(8):
        _write(f"f{i:02d}.log", "x")
    r = toolcall.call("search_filenames",
                      {"pattern": "*.log", "path": "", "max_results": 3})
    assert len(r["files"]) == 3
    assert r["truncated"] is True


def test_search_filenames_bad_dir():
    r = toolcall.call("search_filenames",
                      {"pattern": "*", "path": "no_such_dir_xyz"})
    assert "error" in r


# ---- read_file_range ------------------------------------------------------

def test_read_file_range_first_line():
    r = toolcall.call("read_file_range",
                      {"path": "seed.txt", "start_line": 1,
                       "end_line": 1})
    assert r["path"].endswith("seed.txt")
    assert r["start_line"] == 1 and r["end_line"] == 1
    assert r["total_lines"] == 1
    assert r["content"] == "seed content seed"


def test_read_file_range_default_end():
    _write("multi.txt", "\n".join(f"line {i}" for i in range(1, 101)))
    r = toolcall.call("read_file_range",
                      {"path": "multi.txt", "start_line": 10})
    assert r["end_line"] == 60  # start + 50
    assert r["total_lines"] == 100
    lines = r["content"].splitlines()
    assert lines[0] == "line 10" and lines[-1] == "line 60"


def test_read_file_range_past_eof_empty():
    r = toolcall.call("read_file_range",
                      {"path": "seed.txt", "start_line": 99})
    assert r["content"] == ""
    assert r["total_lines"] == 1


def test_read_file_range_bad_start():
    r = toolcall.call("read_file_range",
                      {"path": "seed.txt", "start_line": 0})
    assert "error" in r and "start_line" in r["error"]


def test_read_file_range_end_before_start():
    r = toolcall.call("read_file_range",
                      {"path": "seed.txt", "start_line": 5, "end_line": 2})
    assert "error" in r


def test_read_file_range_missing_file():
    r = toolcall.call("read_file_range",
                      {"path": "no_such_file_xyz.txt", "start_line": 1})
    assert "error" in r


def test_read_file_range_outside_profile_denied():
    r = toolcall.call("read_file_range",
                      {"path": "../../secret.txt", "start_line": 1})
    assert "error" in r


# ---- search_files upgrades: context, case_insensitive, output_mode, offset

def test_search_files_context():
    _write("ctx.txt", "line one\nline two MATCH\nline three\nline four")
    r = toolcall.call("search_files",
                      {"pattern": "MATCH", "path": "",
                       "file_pattern": "ctx.txt", "context": 1})
    assert len(r["matches"]) == 1
    m = r["matches"][0]
    assert m["line_number"] == 2
    assert m["context_before"] == ["line one"]
    assert m["context_after"] == ["line three"]


def test_search_files_context_at_edges():
    _write("edge.txt", "MATCH first\nmiddle\nMATCH last")
    r = toolcall.call("search_files",
                      {"pattern": "MATCH", "path": "",
                       "file_pattern": "edge.txt", "context": 2})
    first, last = r["matches"]
    assert first["context_before"] == []  # clamped at file start
    assert first["context_after"] == ["middle", "MATCH last"]
    assert last["context_before"] == ["MATCH first", "middle"]
    assert last["context_after"] == []  # clamped at file end


def test_search_files_no_context_keys_by_default():
    r = toolcall.call("search_files", {"pattern": "seed", "path": ""})
    m = next(m for m in r["matches"] if m["file"].endswith("seed.txt"))
    assert "context_before" not in m and "context_after" not in m
    assert set(m) == {"file", "line_number", "line_content", "match_text"}


def test_search_files_case_insensitive():
    r = toolcall.call("search_files",
                      {"pattern": "SEED", "path": "",
                       "file_pattern": "seed.txt",
                       "case_insensitive": True})
    assert any(m["file"].endswith("seed.txt") for m in r["matches"])


def test_search_files_case_sensitive_default():
    r = toolcall.call("search_files",
                      {"pattern": "SEED", "path": "",
                       "file_pattern": "seed.txt"})
    assert r["matches"] == []


def test_search_files_output_mode_files():
    _write("fa.txt", "needle here")
    _write("fb.txt", "needle here too\nneedle again")
    r = toolcall.call("search_files",
                      {"pattern": "needle", "path": "",
                       "file_pattern": "f?.txt", "output_mode": "files"})
    assert r["truncated"] is False
    assert all(set(m) == {"file"} for m in r["matches"])
    names = sorted(m["file"].rsplit("/", 1)[-1] for m in r["matches"])
    assert names == ["fa.txt", "fb.txt"]  # unique, first-seen order


def test_search_files_output_mode_count():
    _write("ca.txt", "needle here")
    _write("cb.txt", "needle here too\nneedle again\nnope")
    r = toolcall.call("search_files",
                      {"pattern": "needle", "path": "",
                       "file_pattern": "c?.txt", "output_mode": "count"})
    assert r["truncated"] is False
    counts = {m["file"].rsplit("/", 1)[-1]: m["count"]
              for m in r["matches"]}
    assert counts == {"ca.txt": 1, "cb.txt": 2}
    assert all(set(m) == {"file", "count"} for m in r["matches"])


def test_search_files_output_mode_bad():
    r = toolcall.call("search_files",
                      {"pattern": "x", "path": "", "output_mode": "bogus"})
    assert "error" in r and "output_mode" in r["error"]


def test_search_files_offset_paginates():
    _write("pg.txt", "\n".join(f"pg line {i}" for i in range(10)))
    kw = {"pattern": "pg line", "path": "", "file_pattern": "pg.txt",
          "max_results": 3}
    p1 = toolcall.call("search_files", {**kw, "offset": 0})
    p2 = toolcall.call("search_files", {**kw, "offset": 3})
    assert [m["line_number"] for m in p1["matches"]] == [1, 2, 3]
    assert [m["line_number"] for m in p2["matches"]] == [4, 5, 6]
    assert p1["truncated"] is True and p2["truncated"] is True


def test_search_files_offset_beyond_end():
    _write("pg2.txt", "\n".join(f"q line {i}" for i in range(4)))
    r = toolcall.call("search_files",
                      {"pattern": "q line", "path": "",
                       "file_pattern": "pg2.txt", "offset": 10})
    assert r["matches"] == [] and r["truncated"] is False


def test_search_files_negative_offset_rejected():
    r = toolcall.call("search_files",
                      {"pattern": "x", "path": "", "offset": -1})
    assert "error" in r and "offset" in r["error"]


def test_search_files_negative_context_rejected():
    r = toolcall.call("search_files",
                      {"pattern": "x", "path": "", "context": -1})
    assert "error" in r and "context" in r["error"]


# ---- search_filenames upgrades: sort_by, offset ---------------------------

def test_search_filenames_sort_mtime_newest_first():
    _write("m1.log", "x")
    _write("m2.log", "x")
    _write("m3.log", "x")
    r = toolcall.call("search_filenames",
                      {"pattern": "m?.log", "path": "",
                       "sort_by": "mtime"})
    names = [f["path"].rsplit("/", 1)[-1] for f in r["files"]]
    assert names == ["m3.log", "m2.log", "m1.log"]


def test_search_filenames_sort_name_is_default():
    _write("z1.log", "x")
    _write("a1.log", "x")
    r = toolcall.call("search_filenames",
                      {"pattern": "?.log", "path": "", "sort_by": "name"})
    names = [f["path"].rsplit("/", 1)[-1] for f in r["files"]]
    assert names == sorted(names)


def test_search_filenames_sort_bad():
    r = toolcall.call("search_filenames",
                      {"pattern": "*", "path": "", "sort_by": "bogus"})
    assert "error" in r and "sort_by" in r["error"]


def test_search_filenames_offset_paginates():
    for i in range(6):
        _write(f"g{i:02d}.log", "x")
    kw = {"pattern": "g*.log", "path": "", "max_results": 2}
    p1 = toolcall.call("search_filenames", {**kw, "offset": 0})
    p2 = toolcall.call("search_filenames", {**kw, "offset": 2})
    p3 = toolcall.call("search_filenames", {**kw, "offset": 4})
    names1 = [f["path"].rsplit("/", 1)[-1] for f in p1["files"]]
    names2 = [f["path"].rsplit("/", 1)[-1] for f in p2["files"]]
    names3 = [f["path"].rsplit("/", 1)[-1] for f in p3["files"]]
    assert names1 == ["g00.log", "g01.log"]
    assert names2 == ["g02.log", "g03.log"]
    assert names3 == ["g04.log", "g05.log"]
    assert p1["truncated"] is True
    assert p3["truncated"] is False  # exactly at the end


def test_search_filenames_offset_beyond_end():
    r = toolcall.call("search_filenames",
                      {"pattern": "*.txt", "path": "", "offset": 999})
    assert r["files"] == [] and r["truncated"] is False
