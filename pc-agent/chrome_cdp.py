"""Chrome CDP session hand: drive a presentation Chrome via DevTools.

Why this exists (proof: browser-execution-proof-2026-10-28.md): the
bridge's screenshot/type hand is open-loop — 147 s best case to an
unverified screenshot of the wrong page. Raw CDP closed the same route
in 3.3–5.3 s with every action verified by readback in the same call.
browser-use was rejected on the same proof (deadlocked navigation,
ignored its profile dir). This module is the deterministic layer only:
one op at a time, addressed by DOM identity, never coordinates, no
LLM loop anywhere.

Protocol shape — the two hard-won lessons from the proof:
  1. Page operations go over the PER-TARGET WebSocket from /json/list,
     never a flatten-mode browser session (Target.attachToTarget):
     flatten sessions intermittently never delivered command responses
     on the proof machine. This module never sends Target.* commands.
  2. A background reader thread owns the socket's receive side: it
     routes responses to waiters by id and absorbs protocol events.
     Commands therefore cannot be starved or misrouted by interleaved
     events (Page.loadEventFired, console logs, ...), and every command
     carries a timeout that FAILS LOUDLY — a silent hang is the failure
     mode this hand exists to replace.

Profile semantics (presentation profile):
  - The profile lives under the agent's own data area
    (%LocalAppData%\\AlterEgo\\cdp-profiles\\presentation by default),
    never Carlos's real Chrome profile — passing the real profile dir
    (or anything inside it) is refused outright.
  - The dir is created if missing and REUSED across launches, so a
    prepared presentation login survives rehearsals. cdp_close with
    delete_profile=True removes it (owned, launched sessions only).
  - The "Restore pages?" bubble is suppressed two ways: Chrome is
    launched with --hide-crash-restore-bubble, and an existing
    profile's Preferences are seeded with a clean exit record before
    launch (the bubble appears when Chrome believes the last exit was
    a crash — an earlier forced kill leaves exactly that mark).
  - Close is GRACEFUL by requirement: Browser.close lets Chrome shut
    itself down cleanly. A forced process kill is only a last-resort
    fallback if Chrome ignores Browser.close for 10 s, and the result
    says so (forced=True) when it happens.

Chrome discovery: installed Chrome only (Program Files, Program
Files (x86), LocalAppData). Nothing is ever downloaded.

Requires: pip install websocket-client (same dependency as browser_cdp).
"""
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

# Reuse the Edge seat's deep-DOM walker (open shadow roots + same-origin
# iframes) rather than growing a parallel copy: one walker, two hands.
from browser_cdp import _DEEP

CDP_HOST = "127.0.0.1"
DEFAULT_DEBUG_PORT = 9333


class CDPError(Exception):
    pass


# --- Chrome + profile locations -------------------------------------------

def find_chrome():
    """Path of the installed Chrome, or None. Never downloads anything."""
    candidates = [
        os.path.expandvars(
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(
            r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    return next((c for c in candidates if os.path.isfile(c)), None)


def real_chrome_profile_dir():
    """Carlos's real Chrome user-data dir. Driving it is refused: the
    presentation hand gets its own profile, always."""
    return os.path.expandvars(r"%LocalAppData%\Google\Chrome\User Data")


def default_profile_dir():
    """The prepared presentation profile, in the agent's data area."""
    return os.path.expandvars(
        r"%LocalAppData%\AlterEgo\cdp-profiles\presentation")


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def _resolve_profile(profile_dir):
    """Validate + prepare the profile dir for a launch.

    Refuses the real Chrome profile (or any dir inside it / containing
    it): attaching the presentation hand to the user's daily browser
    would fight his running Chrome for the profile lock and put his
    sessions in the blast radius of a demo tool.
    """
    prof = profile_dir or default_profile_dir()
    real = real_chrome_profile_dir()
    if _norm(prof) == _norm(real) \
            or _norm(prof).startswith(_norm(real) + os.sep) \
            or _norm(real).startswith(_norm(prof) + os.sep):
        raise CDPError(
            "refusing to use Chrome's real profile dir as the "
            "presentation profile — pass a dedicated profile under the "
            "agent data area (default: "
            r"%LocalAppData%\AlterEgo\cdp-profiles\presentation)")
    os.makedirs(prof, exist_ok=True)
    _seed_clean_exit(prof)
    return prof


def _seed_clean_exit(profile_dir):
    """Mark the profile's last exit as clean so Chrome does not offer
    to restore pages on the next launch. Best-effort: the
    --hide-crash-restore-bubble launch flag is the second layer."""
    prefs_path = os.path.join(profile_dir, "Default", "Preferences")
    try:
        prefs = {}
        if os.path.isfile(prefs_path):
            with open(prefs_path, encoding="utf-8") as f:
                prefs = json.load(f)
        prof = prefs.setdefault("profile", {})
        prof["exit_type"] = "Normal"
        prof["exited_cleanly"] = True
        os.makedirs(os.path.dirname(prefs_path), exist_ok=True)
        with open(prefs_path, "w", encoding="utf-8") as f:
            json.dump(prefs, f)
    except (OSError, ValueError):
        pass  # a locked/corrupt Preferences must not block the launch


# --- CDP HTTP discovery -----------------------------------------------------

def _http_json(port, path, method="GET"):
    url = f"http://{CDP_HOST}:{port}{path}"
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError) as exc:
        raise CDPError(
            f"cannot reach Chrome CDP at {CDP_HOST}:{port} — "
            "no browser is serving that debug port") from exc


def _endpoint_alive(port):
    try:
        _http_json(port, "/json/version")
        return True
    except CDPError:
        return False


def _page_targets(port):
    return [t for t in _http_json(port, "/json/list")
            if t.get("type") == "page"]


# --- in-page snippets ---------------------------------------------------------
# _DEEP (from browser_cdp) is prepended: it defines __cdp_roots/__cdp_els
# covering the page, open shadow roots, and same-origin iframes.

_SELECTOR_FN = """
function __cdp_selector(e) {
  if (e.id) return '#' + CSS.escape(e.id);
  const parts = [];
  let n = e;
  while (n && n.nodeType === 1 && parts.length < 8) {
    let part = n.tagName.toLowerCase();
    const p = n.parentElement;
    if (p) {
      const same = [...p.children].filter(c => c.tagName === n.tagName);
      if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(n) + 1) + ')';
    }
    parts.unshift(part);
    if (p && p.id) { parts.unshift('#' + CSS.escape(p.id)); break; }
    n = p;
  }
  return parts.join(' > ');
}
"""

_SNAPSHOT2 = """() => {
  const found = __cdp_els();
  const out = [];
  found.els.forEach(({el: e, ctx}) => {
    const r = e.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return;
    const label = (e.innerText || e.value || e.placeholder || e.getAttribute('aria-label') || e.name || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
    out.push({selector: __cdp_selector(e),
              tag: e.tagName.toLowerCase(),
              kind: e.getAttribute('type') || e.getAttribute('role') || '',
              label: label,
              context: ctx});
  });
  return {url: location.href, title: document.title,
          elements: out.slice(0, 150),
          cross_origin_iframes: found.skipped};
}"""

_CLICK_SELECTOR = """(sel) => {
  const roots = __cdp_roots().roots;
  for (const r of roots) {
    const e = r.node.querySelector(sel);
    if (e) {
      e.scrollIntoView({block: 'center'});
      if (e.focus) e.focus();
      e.click();
      return 'CLICKED:' + (e.innerText || e.value || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 60);
    }
  }
  return 'NOT-FOUND';
}"""

_FOCUS_FIELD = """(sel, label) => {
  let e = null;
  if (sel) {
    for (const r of __cdp_roots().roots) {
      e = r.node.querySelector(sel);
      if (e) break;
    }
  }
  if (!e && label) {
    const l = label.toLowerCase();
    const els = __cdp_els().els.map(h => h.el);
    e = els.find(x => ((x.placeholder || '') + ' ' + (x.getAttribute('aria-label') || '') + ' ' + (x.name || '')).toLowerCase().includes(l)) || null;
  }
  if (!e) return {found: false};
  e.focus();
  window.__cdp_field = e;
  return {found: true,
          field: (e.tagName.toLowerCase()) + (e.name ? '[name=' + e.name + ']' : '') + (e.placeholder ? ' "' + e.placeholder.slice(0, 40) + '"' : ''),
          value: ('value' in e ? e.value : (e.innerText || '')).slice(0, 500)};
}"""

_CLEAR_FIELD = """() => {
  const e = window.__cdp_field;
  if (!e) return false;
  const proto = e instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(e.__proto__, 'value')?.set
      || Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(e, '');
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
}"""

_FIELD_VALUE = """() => {
  const e = window.__cdp_field;
  if (!e) return null;
  return ('value' in e ? e.value : (e.innerText || ''));
}"""

_READBACK = """() => {
  const a = document.activeElement;
  return {url: location.href, title: document.title,
          ready_state: document.readyState,
          active_element: a ? (a.tagName.toLowerCase() +
            (a.id ? '#' + a.id : '') +
            (a.getAttribute('aria-label') ? ' "' + a.getAttribute('aria-label').slice(0, 40) + '"' : '')) : ''};
}"""


# --- the session ---------------------------------------------------------------

class ChromeSession:
    """One CDP session against one page target of one Chrome.

    A daemon reader thread owns recv(); commands wait on per-id slots.
    """

    def __init__(self):
        self.ws = None
        self.port = None
        self.target = None        # active target dict from /json/list
        self.page_url = ""
        self.owned = False        # True only if launch() started Chrome
        self.pid = None
        self.profile_dir = None
        self._proc = None
        self._id = 0
        self._send_lock = threading.Lock()
        self._pending = {}        # id -> {"event": Event, "msg": dict}
        self._pending_lock = threading.Lock()
        self._events = []         # [(method, params)] ring buffer
        self._events_cond = threading.Condition()
        self._reader = None
        self._alive = False

    # -- transport --

    def _open_ws(self, ws_url):
        """Isolated so tests can substitute a fake socket."""
        try:
            import websocket  # pip install websocket-client
        except ImportError as exc:
            raise CDPError(
                "websocket-client not installed — run: "
                "pip install websocket-client") from exc
        return websocket.create_connection(ws_url)

    def attach(self, port, target_id=None):
        """Connect to a page target's own WebSocket and start the
        reader. Never a flatten-mode browser session (see module
        docstring): the socket is the target's webSocketDebuggerUrl."""
        targets = _page_targets(port)
        if target_id:
            targets = [t for t in targets if t.get("id") == target_id]
            if not targets:
                raise CDPError(f"no page target with id {target_id}")
        if not targets:
            raise CDPError("no page target found — is a tab open?")
        target = targets[0]
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise CDPError("target has no debugger URL")
        self.port = port
        self.target = target
        self.page_url = target.get("url", "")
        self.ws = self._open_ws(ws_url)
        self._alive = True
        self._reader = threading.Thread(target=self._reader_loop,
                                        name="cdp-reader", daemon=True)
        self._reader.start()
        self.cmd("Page.enable")
        self.cmd("Runtime.enable")

    def _reader_loop(self):
        while self._alive:
            try:
                raw = self.ws.recv()
            except Exception:
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            mid = msg.get("id")
            if mid is not None:
                with self._pending_lock:
                    slot = self._pending.pop(mid, None)
                if slot is not None:
                    slot["msg"] = msg
                    slot["event"].set()
                # A response with no waiter is absorbed, never fatal.
            elif "method" in msg:
                with self._events_cond:
                    self._events.append(
                        (msg["method"], msg.get("params") or {}))
                    if len(self._events) > 200:
                        del self._events[:100]
                    self._events_cond.notify_all()
        # Socket died or detach(): fail every waiter loudly.
        with self._pending_lock:
            pending, self._pending = self._pending, {}
        for slot in pending.values():
            slot["msg"] = {"error": {"message": "CDP connection closed"}}
            slot["event"].set()

    def cmd(self, method, params=None, timeout=30):
        """Send one command; wait for ITS response (matched by id).

        Interleaved events and other commands' responses are routed by
        the reader, so they cannot starve or corrupt this call. A
        timeout raises — loudly — instead of hanging.
        """
        if not self._alive or self.ws is None:
            raise CDPError("CDP session is not connected")
        with self._send_lock:
            self._id += 1
            mid = self._id
            slot = {"event": threading.Event(), "msg": None}
            with self._pending_lock:
                self._pending[mid] = slot
            try:
                self.ws.send(json.dumps(
                    {"id": mid, "method": method, "params": params or {}}))
            except Exception as exc:
                with self._pending_lock:
                    self._pending.pop(mid, None)
                raise CDPError(
                    f"CDP send failed on {method}: {exc}") from exc
        if not slot["event"].wait(timeout):
            with self._pending_lock:
                self._pending.pop(mid, None)
            raise CDPError(
                f"CDP timeout after {timeout}s on {method} — no "
                "response arrived; failing loudly instead of hanging "
                "(the hang is the failure mode this hand replaces)")
        msg = slot["msg"] or {}
        if "error" in msg:
            raise CDPError(str(msg["error"].get("message")))
        return msg.get("result", {})

    def _event_mark(self):
        with self._events_cond:
            return len(self._events)

    def wait_event(self, method, timeout, after=0):
        """Params of the next `method` event after index `after`,
        or None on timeout."""
        deadline = time.time() + timeout
        with self._events_cond:
            while True:
                for m, p in self._events[after:]:
                    if m == method:
                        return p
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._events_cond.wait(remaining)

    def detach(self):
        self._alive = False
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        self.ws = None

    # -- page actions --

    def evaluate(self, js):
        """Run JS in the page; return (value, type)."""
        r = self.cmd("Runtime.evaluate",
                     {"expression": js, "returnByValue": True,
                      "awaitPromise": True})
        if r.get("exceptionDetails"):
            raise CDPError(
                str(r["exceptionDetails"].get("text", "JS error")))
        res = r.get("result", {})
        return res.get("value"), res.get("type")

    def navigate(self, url, timeout=30):
        """Navigate and PROVE the landing: waits for the load event
        (readyState as a second witness) with a hard timeout that
        raises instead of hanging. Returns the readback."""
        mark = self._event_mark()
        r = self.cmd("Page.navigate", {"url": url},
                     timeout=min(timeout, 30))
        if r.get("errorText"):
            raise CDPError(f"navigate failed: {r['errorText']}")
        deadline = time.time() + timeout
        fired = self.wait_event("Page.loadEventFired",
                                max(0.1, deadline - time.time()),
                                after=mark)
        if fired is None:
            state, _ = self.evaluate("document.readyState")
            if state != "complete":
                raise CDPError(
                    f"navigation to {url} did not reach load within "
                    f"{timeout}s (readyState={state!r}) — failing "
                    "loudly instead of hanging")
        rb = self.readback()
        self.page_url = rb["url"]
        return rb

    def readback(self):
        val, _ = self.evaluate("(" + _READBACK + ")()")
        return val or {}

    def close_browser(self):
        """Graceful Browser.close. Returns True if a fallback process
        termination was needed (Chrome ignored the graceful close)."""
        try:
            self.cmd("Browser.close", timeout=5)
        except CDPError:
            pass  # the socket dying mid-close IS the browser closing
        forced = False
        if self._proc is not None:
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Last resort only — a forced kill is what marks the
                # profile's exit unclean and summons the restore
                # bubble next launch. The result reports it honestly.
                self._proc.terminate()
                forced = True
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        self.detach()
        return forced


# --- module-level session (one presentation hand per agent process) ----------

_session = None


def _require_session():
    if _session is None:
        raise CDPError(
            "no CDP session — call browser2/cdp launch or connect first")
    return _session


def launch(profile_dir="", debug_port=0, start_url="", timeout_s=20):
    """Start Chrome on the prepared presentation profile and attach.

    Refuses to hijack a debug port that already answers (that browser
    belongs to someone — attach with connect() instead) and refuses
    the real Chrome profile (see _resolve_profile).
    """
    global _session
    if _session is not None:
        raise CDPError(
            "a CDP session is already active — close it before "
            "launching another")
    port = debug_port or DEFAULT_DEBUG_PORT
    # Profile validation first: a refused profile fails fast, before
    # any network or process work happens.
    prof = _resolve_profile(profile_dir)
    if _endpoint_alive(port):
        raise CDPError(
            f"port {port} already serves CDP — use cdp_connect to "
            "attach to that browser, or close it first")
    chrome = find_chrome()
    if not chrome:
        raise CDPError(
            "Google Chrome not found in Program Files or LocalAppData "
            "— nothing is downloaded; install Chrome first")
    proc = subprocess.Popen(
        [chrome,
         f"--remote-debugging-port={port}",
         f"--user-data-dir={prof}",
         "--remote-allow-origins=*",
         "--no-first-run", "--no-default-browser-check",
         "--hide-crash-restore-bubble",
         start_url or "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _endpoint_alive(port):
            break
        if proc.poll() is not None:
            raise CDPError(
                "Chrome exited during launch — its profile may be "
                "locked by a running Chrome using the same profile dir")
        time.sleep(0.25)
    else:
        proc.terminate()
        raise CDPError(
            f"Chrome did not expose CDP on {port} within {timeout_s}s "
            "— the profile may be locked by a running Chrome")
    s = ChromeSession()
    s.owned = True
    s.pid = proc.pid
    s._proc = proc
    s.profile_dir = prof
    try:
        s.attach(port)
    except Exception:
        proc.terminate()
        raise
    _session = s
    rb = s.readback()
    return {"cdp_port": port, "pid": proc.pid, "profile_dir": prof,
            "url": rb.get("url", ""), "title": rb.get("title", "")}


def connect(debug_port=0):
    """Attach to a Chrome already serving a debug port (no process
    ownership: close() on this session detaches, it does not close
    someone else's browser)."""
    global _session
    if _session is not None:
        raise CDPError(
            "a CDP session is already active — close it first")
    port = debug_port or DEFAULT_DEBUG_PORT
    version = _http_json(port, "/json/version")
    s = ChromeSession()
    s.profile_dir = None
    s.attach(port)
    _session = s
    rb = s.readback()
    return {"cdp_port": port, "browser": version.get("Browser", ""),
            "url": rb.get("url", ""), "title": rb.get("title", "")}


def targets(target_id=""):
    """List page targets; switch the session's active target when
    target_id is given."""
    s = _require_session()
    pages = _page_targets(s.port)
    if target_id:
        if not any(t.get("id") == target_id for t in pages):
            raise CDPError(f"no page target with id {target_id}")
        s.detach()
        s.attach(s.port, target_id)
    out = [{"id": t.get("id", ""), "url": t.get("url", ""),
            "title": t.get("title", ""),
            "active": bool(s.target and t.get("id") == s.target.get("id"))}
           for t in pages]
    return {"cdp_port": s.port, "targets": out,
            "active_target": s.target.get("id", "") if s.target else ""}


def navigate(url, timeout_s=30):
    s = _require_session()
    return s.navigate(url, timeout=timeout_s)


def snapshot():
    s = _require_session()
    val, _ = s.evaluate(_DEEP + _SELECTOR_FN + "(" + _SNAPSHOT2 + ")()")
    val = val or {}
    s.page_url = val.get("url", s.page_url)
    return {"page_url": s.page_url, **val}


def click(selector="", text=""):
    """Click by DOM identity: a CSS selector, or visible text via the
    shared deep walker. Verified by post-click readback; `changed`
    reports whether URL/title observably moved (navigation or DOM
    change is the caller's to confirm via snapshot)."""
    s = _require_session()
    before = s.readback()
    mark = s._event_mark()
    if selector:
        val, _ = s.evaluate(
            _DEEP + f"({_CLICK_SELECTOR})({json.dumps(selector)})")
    elif text:
        from browser_cdp import _FIND_CLICK
        val, _ = s.evaluate(
            _DEEP + f"({_FIND_CLICK})({json.dumps(text)})")
    else:
        raise CDPError("click requires a selector or text")
    if not val or val == "NOT-FOUND":
        raise CDPError(
            f"click target not found: {selector or text!r}")
    # A click may navigate: give the load event a brief window, then
    # read back where we actually landed.
    s.wait_event("Page.loadEventFired", 2, after=mark)
    after = s.readback()
    s.page_url = after.get("url", s.page_url)
    changed = (after.get("url") != before.get("url")
               or after.get("title") != before.get("title"))
    return {"clicked": val, "url": after.get("url", ""),
            "title": after.get("title", ""), "changed": changed}


def fill(text, selector="", label="", clear_first=True):
    """Type into a field by identity: focus it, then CDP
    Input.insertText (the proof's precise-typing primitive).
    Success REQUIRES value readback equality — anything less raises."""
    s = _require_session()
    focus, _ = s.evaluate(
        _DEEP + f"({_FOCUS_FIELD})({json.dumps(selector)}, "
        f"{json.dumps(label)})")
    if not focus or not focus.get("found"):
        raise CDPError(
            f"field not found: selector={selector!r} label={label!r}")
    before = focus.get("value", "")
    if clear_first:
        s.evaluate("(" + _CLEAR_FIELD + ")()")
    s.cmd("Input.insertText", {"text": text})
    after, _ = s.evaluate("(" + _FIELD_VALUE + ")()")
    after = after if after is not None else ""
    expected = text if clear_first else before + text
    if after != expected:
        raise CDPError(
            f"fill readback mismatch: expected {expected!r}, "
            f"field holds {after!r}")
    return {"value": after, "field": focus.get("field", ""),
            "verified": True}


def eval_js(js):
    s = _require_session()
    val, typ = s.evaluate(js)
    s_out = json.dumps(val)[:2000] if typ != "string" else str(val)[:2000]
    return {"page_url": s.page_url, "type": typ, "value": s_out}


def readback():
    s = _require_session()
    rb = s.readback()
    s.page_url = rb.get("url", s.page_url)
    return {"page_url": s.page_url, **rb}


def close(delete_profile=False):
    """End the session. A launched browser is closed GRACEFULLY via
    Browser.close (hard requirement — forced kills summon the
    restore-pages bubble); an attached browser is only detached."""
    global _session
    s = _require_session()
    _session = None
    profile_dir = s.profile_dir or ""
    if s.owned:
        forced = s.close_browser()
        closed = "browser"
    else:
        s.detach()
        forced = False
        closed = "detached"
    deleted = False
    if delete_profile and s.owned and profile_dir:
        for _ in range(3):
            shutil.rmtree(profile_dir, ignore_errors=True)
            if not os.path.exists(profile_dir):
                deleted = True
                break
            time.sleep(0.5)
    return {"closed": closed, "forced": forced,
            "profile_deleted": deleted, "profile_dir": profile_dir}
