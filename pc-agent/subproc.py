"""Subprocess runner immune to the pipe-inheritance hang.

capture_output=True uses pipes; any grandchild that inherits them (e.g. via
`start`) keeps them open, so communicate() blocks until the grandchild exits
and the HTTP request hangs. Redirecting to temp files instead means wait()
returns as soon as the direct child exits, no matter what grandchildren
still hold open.
"""
import os
import subprocess
import tempfile


def _job_context():
    """The jobs.JobContext running on this thread, or None. Lazy import:
    jobs never imports subproc, so there is no cycle; outside a job
    (every legacy sync call) this returns None and nothing changes."""
    try:
        import jobs
        return jobs.current_context()
    except Exception:
        return None


def _kill_tree(proc):
    """Kill proc's whole process tree, not just the direct child.

    A bare proc.kill() orphans grandchildren (detached shells, builds,
    sync tools), which accumulate as stuck processes on the PC — the
    codebase already knows this lesson (exec_cancel's tree-kill). The
    timeout paths below use this; falls back to proc.kill() when
    taskkill is unavailable or fails.
    """
    try:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=10)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def run_noinherit(cmd, timeout_s, **kwargs):
    """Run cmd; returns (returncode, stdout, stderr, timed_out)."""
    out_f = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    err_f = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f, **kwargs)
        ctx = _job_context()
        timed_out = False
        if ctx is None:
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc)
                proc.wait()
        else:
            # Inside a bridge job: register the child so the watchdog /
            # cancel can fence THIS job's process tree, and wait in
            # slices — output growth is the job's progress signal, and a
            # cancel lands within one slice instead of at timeout.
            ctx.register_process(proc)
            try:
                deadline = timeout_s
                elapsed = 0.0
                while True:
                    try:
                        proc.wait(timeout=0.5)
                        break
                    except subprocess.TimeoutExpired:
                        elapsed += 0.5
                        size = (os.fstat(out_f.fileno()).st_size
                                + os.fstat(err_f.fileno()).st_size)
                        ctx.report_output(size)
                        if ctx.cancel_event.is_set():
                            proc.kill()
                            proc.wait()
                            break
                        if deadline is not None and elapsed >= deadline:
                            timed_out = True
                            _kill_tree(proc)
                            proc.wait()
                            break
            finally:
                ctx.unregister_process(proc)
        out_f.seek(0)
        err_f.seek(0)
        return proc.returncode, out_f.read(), err_f.read(), timed_out
    finally:
        out_f.close()
        err_f.close()
