"""Subprocess runner immune to the pipe-inheritance hang.

capture_output=True uses pipes; any grandchild that inherits them (e.g. via
`start`) keeps them open, so communicate() blocks until the grandchild exits
and the HTTP request hangs. Redirecting to temp files instead means wait()
returns as soon as the direct child exits, no matter what grandchildren
still hold open.
"""
import subprocess
import tempfile


def run_noinherit(cmd, timeout_s, **kwargs):
    """Run cmd; returns (returncode, stdout, stderr, timed_out)."""
    out_f = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    err_f = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f, **kwargs)
        timed_out = False
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            proc.wait()
        out_f.seek(0)
        err_f.seek(0)
        return proc.returncode, out_f.read(), err_f.read(), timed_out
    finally:
        out_f.close()
        err_f.close()
