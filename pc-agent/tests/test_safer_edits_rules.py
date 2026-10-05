"""Tests for safer edit_file (unique match, diff preview) and
command-level allow/deny rules."""
import json
import os
from pathlib import Path

import pytest

import toolcall
from seams.providers import mock as mock_state
import command_rules


@pytest.fixture(autouse=True)
def _isolate_rules(tmp_path, monkeypatch):
    """Point APPDATA at a temp dir so tests don't touch real rules."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    yield


def _write(path, content):
    toolcall.call("write_file", {"path": path, "content": content},
                  write=True)


# ---- safer edits -----------------------------------------------------------

def test_edit_unique_match_ok():
    _write("docs/a.txt", "hello world\n")
    r = toolcall.call("edit_file", {
        "path": "docs/a.txt", "old_text": "world", "new_text": "there"},
        write=True)
    assert r["replacements"] == 1
    assert r["matches_found"] == 1
    assert "diff" in r and r["diff"]
    assert "-hello world" in r["diff"] or "world" in r["diff"]


def test_edit_multiple_matches_fails_by_default():
    _write("docs/b.txt", "foo bar foo bar foo\n")
    r = toolcall.call("edit_file", {
        "path": "docs/b.txt", "old_text": "foo", "new_text": "baz"},
        write=True)
    assert "error" in r
    assert "multiple matches found" in r["error"]


def test_edit_multiple_matches_allowed_with_flag():
    _write("docs/c.txt", "foo bar foo bar\n")
    r = toolcall.call("edit_file", {
        "path": "docs/c.txt", "old_text": "foo", "new_text": "baz",
        "require_unique": False}, write=True)
    assert r["replacements"] == 1
    assert r["matches_found"] == 2
    content = toolcall.call("read_file", {"path": "docs/c.txt"})["content"]
    assert content == "baz bar foo bar\n"  # only first replaced


def test_edit_diff_preview_in_result():
    _write("docs/d.txt", "line1\nline2\nline3\n")
    r = toolcall.call("edit_file", {
        "path": "docs/d.txt", "old_text": "line2", "new_text": "CHANGED"},
        write=True)
    assert r["diff"]
    assert "line2" in r["diff"] and "CHANGED" in r["diff"]
    assert "before" in r and "after" in r
    assert "line2" in r["before"] and "CHANGED" in r["after"]


def test_edit_dry_run_shows_diff_no_write():
    _write("docs/e.txt", "original\n")
    r = toolcall.call("edit_file", {
        "path": "docs/e.txt", "old_text": "original", "new_text": "new",
        "dry_run": True})
    assert r["dry_run"] is True
    assert r["diff"]
    content = toolcall.call("read_file", {"path": "docs/e.txt"})["content"]
    assert content == "original\n"  # unchanged


# ---- command rules ---------------------------------------------------------

def _write_rules(data):
    p = command_rules.rules_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def test_no_rules_allows_everything():
    allowed, reason = command_rules.check_command("rm -rf /tmp/x")
    assert allowed is True
    assert "no rules" in reason


def test_deny_pattern_blocks():
    _write_rules({"deny": ["rm -rf *", "format *"], "allow": []})
    allowed, reason = command_rules.check_command("rm -rf /tmp/stuff")
    assert allowed is False
    assert "denied by rule" in reason


def test_deny_wins_over_allow():
    _write_rules({"allow": ["git *"], "deny": ["git push *"]})
    allowed, _ = command_rules.check_command("git push origin main")
    assert allowed is False


def test_allow_list_default_deny():
    _write_rules({"allow": ["git *", "npm test"], "deny": []})
    allowed, _ = command_rules.check_command("git status")
    assert allowed is True
    allowed, reason = command_rules.check_command("curl evil.com")
    assert allowed is False
    assert "not in allow list" in reason


def test_wildcard_matching():
    _write_rules({"deny": ["*password*", "del *"], "allow": []})
    allowed, _ = command_rules.check_command("echo PASSWORD=secret")
    assert allowed is False  # case-insensitive
    allowed, _ = command_rules.check_command("del C:\\temp\\x")
    assert allowed is False


def test_hook_denies_shell_exec():
    _write_rules({"deny": ["rm -rf *"], "allow": []})
    action = command_rules._pre_tool_use_hook(
        "shell_exec", {"command": "rm -rf /tmp/x"})
    assert action[0] == "deny"
    assert "denied by rule" in action[1]


def test_hook_allows_clean_command():
    _write_rules({"deny": ["rm -rf *"], "allow": []})
    assert command_rules._pre_tool_use_hook(
        "shell_exec", {"command": "echo hello"}) == "allow"


def test_invalid_rules_file_allows_everything():
    p = command_rules.rules_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not valid json{{{", encoding="utf-8")
    allowed, _ = command_rules.check_command("anything at all")
    assert allowed is True
