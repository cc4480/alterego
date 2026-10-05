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
