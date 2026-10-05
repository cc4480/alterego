"""Mock-provider behavior tests: virtual FS, safety guarantees, journaling.

Safety core: the mock NEVER performs real destructive actions — no
reboots, no host file deletes, no real process kills, no shell execution.
"""
import os

import toolcall
from seams.providers import mock as mock_state
from conftest import read_events


def test_shell_echo_returns_text():
    r = toolcall.call("shell_exec", {"command": "echo hello world"})
    assert r["stdout"] == "hello world"
    assert r["returncode"] == 0 and r["timed_out"] is False


def test_shell_never_executes():
    # "rm -rf /" is data to the mock, not a command.
    r = toolcall.call("shell_exec", {"command": "rm -rf /"}, write=True)
    assert r["stdout"] == "mock output"
    assert os.path.exists("/")  # host untouched


def test_shell_pwsh_echo():
    r = toolcall.call("shell_pwsh", {"script": "echo hi"}, write=True)
    assert r["stdout"] == "hi" and r["returncode"] == 0


def test_virtual_fs_roundtrip():
    toolcall.call("write_file",
                  {"path": "docs/a.txt", "content": "hello"}, write=True)
    r = toolcall.call("read_file", {"path": "docs/a.txt"})
    assert r["content"] == "hello"
    d = toolcall.call("list_dir", {"path": "docs"})
    assert [i["name"] for i in d["items"]] == ["a.txt"]
    info = toolcall.call("file_info", {"path": "docs/a.txt"})
    assert info["is_file"] and info["size"] == 5
    toolcall.call("delete_file", {"path": "docs/a.txt"}, write=True)
    assert toolcall.call("read_file", {"path": "docs/a.txt"}) \
        .get("error", "").startswith("FileNotFoundError")


def test_dry_run_mutates_nothing():
    before = dict(mock_state.FS)
    r = toolcall.call("write_file",
                      {"path": "new.txt", "content": "x", "dry_run": True},
                      write=True)
    assert r["dry_run"] is True and r["would"] == "create"
    assert mock_state.FS == before


def test_path_jail_escape_denied():
    r = toolcall.call("read_file", {"path": "../../etc/passwd"})
    assert r.get("error", "").startswith("ValueError: denied")


def test_delete_only_touches_virtual_fs(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("host data")
    # Virtual delete of a same-named virtual path leaves the host file.
    toolcall.call("write_file", {"path": "real.txt", "content": "v"},
                  write=True)
    toolcall.call("delete_file", {"path": "real.txt"}, write=True)
    assert real.read_text() == "host data"


def test_kill_process_mock_only():
    before = toolcall.call("list_processes", {})
    assert before["count"] == 3
    r = toolcall.call("kill_process", {"pid": 1234}, write=True)
    assert r["terminated"] is True
    after = toolcall.call("list_processes", {})
    assert after["count"] == 2
    assert all(p["pid"] != 1234 for p in after["processes"])
    assert os.getpid() > 0  # this process is alive: nothing real was killed


def test_power_never_reboots():
    r = toolcall.call("power", {"action": "reboot"}, write=True)
    assert r == {"power": "reboot"}
    journaled = [c for c in mock_state.CALLS if c["tool"] == "power"]
    assert journaled == [{"tool": "power", "args": {"action": "reboot"}}]


def test_ui_calls_journaled():
    toolcall.call("mouse_click", {"x": 5, "y": 6}, write=True)
    toolcall.call("type_text", {"text": "ab"}, write=True)
    tools = [c["tool"] for c in mock_state.CALLS]
    assert tools == ["mouse_click", "type_text"]
    assert mock_state.CALLS[0]["args"] == {"x": 5, "y": 6, "button": "left"}


def test_clipboard_roundtrip():
    toolcall.call("clipboard_set", {"text": "paste me"}, write=True)
    assert toolcall.call("clipboard_get", {})["text"] == "paste me"


def test_doctor_defender_warning():
    r = toolcall.call("doctor", {})
    by_name = {c["check"]: c for c in r["checks"]}
    assert by_name["check_defender_exclusions"]["status"] == "warning"
    assert all(c["status"] == "ok" for n, c in by_name.items()
               if n != "check_defender_exclusions")
    assert r["counts"] == {"ok": 5, "warning": 1, "fail": 0}


def test_batch_fan_out():
    r = toolcall.call("batch", {"calls": [
        {"tool": "idle_seconds", "args": {}},
        {"tool": "shell_exec", "args": {"command": "echo yo"}},
        {"tool": "nope", "args": {}},
    ]}, write=True)
    assert r["calls"] == 3
    assert r["results"][0]["result"] == {"idle_seconds": 42}
    assert r["results"][1]["result"]["stdout"] == "yo"
    assert "error" in r["results"][2]


def test_batch_rejects_nesting():
    r = toolcall.call("batch",
                      {"calls": [{"tool": "batch", "args": {}}]}, write=True)
    assert r.get("error", "").startswith("ValueError: nested batch")


def test_arbitrate_delegates_to_real_logic():
    r = toolcall.call("arbitrate",
                      {"trajectories": [["screenshot"], ["shell_exec"]]})
    assert set(r) == {"ranked", "recommended"}
    assert r["recommended"]["score"] >= r["ranked"][-1]["score"]
    assert all("score" in t and "tier" in t for t in r["ranked"])


def test_recon_is_canned_not_live():
    r = toolcall.call("dns_query", {"domain": "example.com"}, write=True)
    assert r["records"] == ["93.184.216.34"]
    assert "no real DNS lookup" in r["note"]
    # No network was needed: suite stays hermetic offline.


def test_browser_canned():
    r = toolcall.call("browser_snapshot", {})
    assert r["title"] == "Mock Page" and len(r["elements"]) == 2
    n = toolcall.call("browser_navigate",
                      {"url": "https://example.com"}, write=True)
    assert n["page_url"] == "https://example.com"


def test_events_single_write(tmp_path):
    toolcall.call("idle_seconds", {})
    assert read_events(), "no events written"
    from auth import app_dir
    assert not (app_dir() / "audit.log").exists(), \
        "legacy audit.log must not be written (event log is primary)"
