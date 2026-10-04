"""Tests for tools_files (real tmp dirs; approval dialog stubbed)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools_files
import tool_wrappers

tools_files.request_approval = lambda *a, **k: True


def test_write_and_overwrite(tmp_path):
    p = str(tmp_path / "a.txt")
    r = tools_files.write_file(p, "hello")
    assert r["bytes_written"] == 5, r
    assert open(p, encoding="utf-8").read() == "hello"
    r = tools_files.write_file(p, "hi")
    assert open(p, encoding="utf-8").read() == "hi"
    print("PASS write/overwrite")


def test_write_creates_parents(tmp_path):
    p = str(tmp_path / "x" / "y" / "b.txt")
    tools_files.write_file(p, "deep")
    assert open(p, encoding="utf-8").read() == "deep"
    print("PASS write parents")


def test_write_rejects_relative():
    try:
        tools_files.write_file("relative/path.txt", "x")
    except ValueError as e:
        assert "absolute" in str(e), e
        print("PASS write relative rejected")
    else:
        raise AssertionError("should have raised")


def test_edit_replaces_first(tmp_path):
    p = str(tmp_path / "c.txt")
    open(p, "w", encoding="utf-8").write("aaa bbb aaa")
    r = tools_files.edit_file(p, "aaa", "zzz")
    assert r["replacements"] == 1, r
    assert open(p, encoding="utf-8").read() == "zzz bbb aaa"
    print("PASS edit first occurrence")


def test_edit_missing_text(tmp_path):
    p = str(tmp_path / "d.txt")
    open(p, "w", encoding="utf-8").write("hello")
    try:
        tools_files.edit_file(p, "nope", "x")
    except ValueError as e:
        assert "not found" in str(e), e
        print("PASS edit missing text")
    else:
        raise AssertionError("should have raised")


def test_delete(tmp_path):
    p = str(tmp_path / "e.txt")
    open(p, "w").write("x")
    r = tools_files.delete_file(p)
    assert r["deleted"] is True and not os.path.exists(p), r
    try:
        tools_files.delete_file(p)
    except FileNotFoundError:
        print("PASS delete + missing")
    else:
        raise AssertionError("should have raised")


def test_delete_rejects_dir(tmp_path):
    try:
        tools_files.delete_file(str(tmp_path))
    except FileNotFoundError as e:
        assert "not a file" in str(e), e
        print("PASS delete rejects dir")
    else:
        raise AssertionError("should have raised")


def test_create_dir(tmp_path):
    p = str(tmp_path / "n1" / "n2")
    r = tools_files.create_dir(p)
    assert r["created"] is True and os.path.isdir(p), r
    tools_files.create_dir(p)  # idempotent
    print("PASS create_dir")


def test_denied_raises(tmp_path):
    tools_files.request_approval = lambda *a, **k: False
    try:
        tools_files.write_file(str(tmp_path / "f.txt"), "x")
    except PermissionError:
        print("PASS denied raises")
    else:
        raise AssertionError("should have raised")
    finally:
        tools_files.request_approval = lambda *a, **k: True


def test_wrappers_register_all():
    names = [f.__name__ for f in tool_wrappers.ALL_TOOLS]
    assert len(names) == 17, names
    assert len(set(names)) == 17, "duplicate tool names"
    for n in ("write_file", "edit_file", "delete_file", "create_dir"):
        assert n in names, n
    print("PASS wrappers: 17 tools registered")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        from pathlib import Path
        tp = Path(td)
        test_write_and_overwrite(tp)
        test_write_creates_parents(tp)
        test_write_rejects_relative()
        test_edit_replaces_first(tp)
        test_edit_missing_text(tp)
        test_delete(tp)
        test_delete_rejects_dir(tp)
        test_create_dir(tp)
        test_denied_raises(tp)
    test_wrappers_register_all()
    print("ALL TOOLS_FILES TESTS PASS")
