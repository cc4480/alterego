"""Tests for the staleness guard: expected_sha256 on write_file/edit_file,
and sha256 in read_file/read_file_range results.

The mock provider is active (conftest pins PC_BRIDGE_PROVIDER=mock and
PC_BRIDGE_PERMISSION_MODE=dontAsk, so approvals auto-pass).
"""
import hashlib

import toolcall


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _write(path, content):
    return toolcall.call("write_file", {"path": path, "content": content},
                         write=True)


def _read_sha(path):
    return toolcall.call("read_file", {"path": path})["sha256"]


# ---- sha256 in read results ------------------------------------------------

def test_read_file_returns_sha256():
    _write("sg/a.txt", "hello\n")
    r = toolcall.call("read_file", {"path": "sg/a.txt"})
    assert r["sha256"] == _sha("hello\n")


def test_read_file_range_returns_sha256_of_whole_file():
    _write("sg/b.txt", "l1\nl2\nl3\n")
    r = toolcall.call("read_file_range",
                      {"path": "sg/b.txt", "start_line": 2, "end_line": 2})
    assert r["content"] == "l2"
    assert r["sha256"] == _sha("l1\nl2\nl3\n")


def test_sha256_changes_when_content_changes():
    _write("sg/c.txt", "v1")
    h1 = _read_sha("sg/c.txt")
    _write("sg/c.txt", "v2")
    h2 = _read_sha("sg/c.txt")
    assert h1 != h2
    assert h2 == _sha("v2")


# ---- edit_file with expected_sha256 ----------------------------------------

def test_edit_matching_hash_succeeds():
    _write("sg/d.txt", "alpha beta\n")
    h = _read_sha("sg/d.txt")
    r = toolcall.call("edit_file",
                      {"path": "sg/d.txt", "old_text": "beta",
                       "new_text": "gamma", "expected_sha256": h},
                      write=True)
    assert r["replacements"] == 1
    assert toolcall.call("read_file", {"path": "sg/d.txt"})["content"] \
        == "alpha gamma\n"


def test_edit_stale_hash_fails():
    _write("sg/e.txt", "one\n")
    stale = _read_sha("sg/e.txt")
    _write("sg/e.txt", "two\n")  # someone else changed it
    r = toolcall.call("edit_file",
                      {"path": "sg/e.txt", "old_text": "two",
                       "new_text": "three", "expected_sha256": stale},
                      write=True)
    assert "error" in r
    assert "file changed since read" in r["error"]
    assert "Re-read the file and retry" in r["error"]
    # File untouched by the failed edit.
    assert toolcall.call("read_file", {"path": "sg/e.txt"})["content"] \
        == "two\n"


def test_edit_without_expected_sha256_is_backward_compat():
    _write("sg/f.txt", "aaa\n")
    _write("sg/f.txt", "bbb\n")  # external change; no guard requested
    r = toolcall.call("edit_file",
                      {"path": "sg/f.txt", "old_text": "bbb",
                       "new_text": "ccc"},
                      write=True)
    assert r["replacements"] == 1


def test_edit_dry_run_stale_hash_fails_fast():
    _write("sg/g.txt", "x\n")
    stale = _read_sha("sg/g.txt")
    _write("sg/g.txt", "y\n")
    r = toolcall.call("edit_file",
                      {"path": "sg/g.txt", "old_text": "y",
                       "new_text": "z", "dry_run": True,
                       "expected_sha256": stale},
                      write=True)
    assert "error" in r
    assert "file changed since read" in r["error"]


# ---- write_file with expected_sha256 ---------------------------------------

def test_write_matching_hash_succeeds():
    _write("sg/h.txt", "old\n")
    h = _read_sha("sg/h.txt")
    r = toolcall.call("write_file",
                      {"path": "sg/h.txt", "content": "new\n",
                       "expected_sha256": h},
                      write=True)
    assert r["bytes_written"] == len("new\n".encode("utf-8"))


def test_write_stale_hash_fails():
    _write("sg/i.txt", "v1\n")
    stale = _read_sha("sg/i.txt")
    _write("sg/i.txt", "v2\n")
    r = toolcall.call("write_file",
                      {"path": "sg/i.txt", "content": "v3\n",
                       "expected_sha256": stale},
                      write=True)
    assert "error" in r
    assert "file changed since read" in r["error"]
    assert toolcall.call("read_file", {"path": "sg/i.txt"})["content"] \
        == "v2\n"


def test_write_new_file_needs_no_hash():
    # Creating a file that doesn't exist yet: nothing stale to protect.
    r = toolcall.call("write_file",
                      {"path": "sg/brand-new.txt", "content": "hi\n"},
                      write=True)
    assert r["bytes_written"] == 3


def test_write_dry_run_stale_hash_fails():
    _write("sg/j.txt", "p\n")
    stale = _read_sha("sg/j.txt")
    _write("sg/j.txt", "q\n")
    r = toolcall.call("write_file",
                      {"path": "sg/j.txt", "content": "r\n",
                       "dry_run": True, "expected_sha256": stale},
                      write=True)
    assert "error" in r
    assert "file changed since read" in r["error"]
