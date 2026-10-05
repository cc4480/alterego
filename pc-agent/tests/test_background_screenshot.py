"""Tests for background command execution and enhanced screenshot.

Uses the mock provider (no real processes spawned, no real screenshots).
"""
import toolcall


def test_exec_background_returns_job():
    r = toolcall.call("exec_background", {"command": "sleep 60"},
                      write=True)
    assert r["status"] == "started"
    assert r["job_id"] == "mock-job-001"
    assert r["pid"] == 4242


def test_exec_background_custom_timeout():
    r = toolcall.call("exec_background",
                      {"command": "sleep 60", "timeout_s": 120},
                      write=True)
    assert r["status"] == "started"


def test_exec_status_returns_completed():
    r = toolcall.call("exec_status", {"job_id": "mock-job-001"})
    assert r["job_id"] == "mock-job-001"
    assert r["status"] == "completed"
    assert r["returncode"] == 0
    assert "output_tail" in r
    assert "output_full_path" in r


def test_exec_cancel_returns_cancelled():
    r = toolcall.call("exec_cancel", {"job_id": "mock-job-001"},
                      write=True)
    assert r["job_id"] == "mock-job-001"
    assert r["status"] == "cancelled"


def test_screenshot_no_args_backward_compat():
    r = toolcall.call("screenshot", {})
    assert "png_base64" in r
    assert r["width"] == 1
    assert r["height"] == 1


def test_screenshot_with_region():
    r = toolcall.call("screenshot", {
        "region": {"x": 0, "y": 0, "width": 100, "height": 100}})
    assert "png_base64" in r


def test_screenshot_with_scale():
    r = toolcall.call("screenshot", {"scale": 0.5})
    assert "png_base64" in r


def test_screenshot_with_region_and_scale():
    r = toolcall.call("screenshot", {
        "region": {"x": 10, "y": 10, "width": 200, "height": 200},
        "scale": 0.5})
    assert "png_base64" in r


def test_screenshot_bad_scale_rejected():
    r = toolcall.call("screenshot", {"scale": 2.0})
    assert "error" in r
    assert "scale" in r["error"].lower()


def test_screenshot_bad_region_rejected():
    r = toolcall.call("screenshot", {"region": "not-a-dict"})
    assert "error" in r
    assert "region" in r["error"].lower()
