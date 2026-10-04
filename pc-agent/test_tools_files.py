"""Tests for tools_files + tools_write pure logic (no Windows needed)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools_files
import tools_write
import tool_wrappers

tools_files.request_approval = lambda *a, **k: True
tools_write.request_approval = lambda *a, **k: True


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
    assert len(names) == 34, names
    assert len(set(names)) == 34, "duplicate tool names"
    for n in ("write_file", "edit_file", "delete_file", "create_dir",
              "close_window", "hotkey", "mouse_move", "mouse_click",
              "mouse_scroll", "minimize_window", "maximize_window",
              "kill_process", "clipboard_set", "clipboard_get",
              "copy_file", "move_file", "file_info", "paste_text",
              "memory_recall", "shell_pwsh", "batch"):
        assert n in names, n
    print("PASS wrappers: 34 tools registered")


def test_hotkey_parse():
    mods, vk = tools_write._parse_hotkey("ctrl+shift+s")
    assert mods == [0x11, 0x10] and vk == 0x53, (mods, vk)
    assert tools_write._parse_hotkey("enter") == ([], 0x0D)
    assert tools_write._parse_hotkey("alt+f4") == ([0x12], 0x73)
    assert tools_write._parse_hotkey("win+r") == ([0x5B], 0x52)
    for bad in ("", "ctrl+", "ctrl+bogus", "bogus+x", "+"):
        try:
            tools_write._parse_hotkey(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"should have raised: {bad!r}")
    print("PASS hotkey parse")


def test_paste_text_validation():
    for bad in ("", 123, None, "x" * 100001):
        try:
            tools_write.paste_text(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"should have raised for {str(bad)[:20]!r}")
    print("PASS paste_text validation")


def test_support_tools(tmp_path):
    import approval
    import tools_support
    # validation
    for bad in ([], [{"tool": "x"}] * 21, [{"tool": "batch", "args": {}}],
                [{"nope": 1}], [{"tool": "system_info", "args": []}]):
        try:
            tools_support.batch(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"batch should have raised for {str(bad)[:40]}")
    for bad in ("", "   ", "x" * 8001, None):
        try:
            tools_support.shell_pwsh(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("shell_pwsh should have raised")
    # live dispatch on linux: read-only memory_recall inside a batch
    (tmp_path / "m.md").write_text("# Hello\nbatch recall test", encoding="utf-8")
    os.environ["PC_BRIDGE_MEMORY_DIR"] = str(tmp_path)
    approval.AUTO_APPROVE = True
    try:
        r = tools_support.batch([
            {"tool": "memory_recall", "args": {"query": "batch recall"}},
            {"tool": "no_such_tool", "args": {}},
        ])
        assert r["calls"] == 2, r
        assert r["results"][0]["result"]["files_matched"] == 1, r
        assert "unknown tool" in r["results"][1]["error"], r
    finally:
        del os.environ["PC_BRIDGE_MEMORY_DIR"]
    print("PASS support tools (batch + shell_pwsh validation, live dispatch)")


def test_memory_recall(tmp_path):
    import tools_memory
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "transcripts" / "2026-10-04-main.md").write_text(
        "# Day log\n\n## paste_text tool\nBuilt paste_text for reliable long text entry via clipboard.\n",
        encoding="utf-8")
    (tmp_path / "transcripts" / "2026-10-03-main.md").write_text(
        "# Day log\n\nDiscussed video rendering pipelines and codecs.\n",
        encoding="utf-8")
    os.environ["PC_BRIDGE_MEMORY_DIR"] = str(tmp_path)
    try:
        r = tools_memory.memory_recall("paste_text clipboard")
        assert r["files_matched"] == 1, r
        assert r["results"][0]["file"].endswith("2026-10-04-main.md"), r
        assert "paste_text" in r["results"][0]["snippet"], r
        r = tools_memory.memory_recall("zzzznope")
        assert r["results"] == [] and r["files_matched"] == 0, r
        for bad in ("", "a", "!!!"):
            try:
                tools_memory.memory_recall(bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"should have raised for {bad!r}")
    finally:
        del os.environ["PC_BRIDGE_MEMORY_DIR"]
    print("PASS memory_recall")


def test_copy_move_info(tmp_path):
    src = str(tmp_path / "orig.txt")
    open(src, "w", encoding="utf-8").write("data123")
    dst = str(tmp_path / "sub" / "copy.txt")
    r = tools_files.copy_file(src, dst)
    assert r["bytes"] == 7 and open(dst, encoding="utf-8").read() == "data123", r
    mv = str(tmp_path / "moved.txt")
    r = tools_files.move_file(dst, mv)
    assert r["moved"] is True and not os.path.exists(dst), r
    assert open(mv, encoding="utf-8").read() == "data123"
    info = tools_files.file_info(mv)
    assert info["is_file"] and info["size"] == 7 and "modified" in info, info
    dinfo = tools_files.file_info(str(tmp_path))
    assert dinfo["is_dir"], dinfo
    try:
        tools_files.file_info(str(tmp_path / "nope.txt"))
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("should have raised")
    print("PASS copy/move/info")


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
        test_copy_move_info(tp)
        test_memory_recall(tp)
        test_support_tools(tp)
    test_wrappers_register_all()
    test_hotkey_parse()
    test_paste_text_validation()
    print("ALL TOOLS_FILES TESTS PASS")
