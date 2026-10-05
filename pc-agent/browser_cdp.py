"""CDP browser tools: drive Edge via Chrome DevTools Protocol.

If no Edge CDP endpoint is reachable on 127.0.0.1:9222, a headless Edge
is launched automatically (--headless=new) with an isolated profile, so
browser tools never pop a visible window on the user's screen.
Attaches to the live tab and gets full DOM access: buttons, inputs, open
shadow roots, and same-origin iframes that UI Automation cannot see.

Requires: pip install websocket-client
"""
import json
import time
import urllib.request
import urllib.error

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222


def _ensure_edge_headless(timeout_s: float = 15.0) -> None:
    """Launch headless Edge with CDP if none is reachable.

    Uses --headless=new so no window ever appears on the user's screen.
    A fresh user-data-dir keeps it isolated from the user's real Edge
    profile. Called automatically before any CDP operation.
    """
    import subprocess
    import tempfile
    import os

    # Already up? Nothing to do.
    try:
        _targets()
        return
    except CDPError:
        pass

    # Find Edge.
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ]
    edge = next((c for c in candidates if os.path.isfile(c)), None)
    if not edge:
        raise CDPError("Microsoft Edge not found; cannot start headless CDP")

    profile_dir = tempfile.mkdtemp(prefix="pc-bridge-edge-cdp-")
    subprocess.Popen(
        [edge, "--headless=new",
         f"--remote-debugging-port={CDP_PORT}",
         f"--user-data-dir={profile_dir}",
         "--remote-allow-origins=*",
         "--no-first-run", "--no-default-browser-check",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )

    # Wait for the CDP endpoint.
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            _targets()
            return
        except CDPError:
            time.sleep(0.5)
    raise CDPError(f"headless Edge did not expose CDP on {CDP_PORT} in time")


class CDPError(Exception):
    pass


def _targets():
    url = f"http://{CDP_HOST}:{CDP_PORT}/json"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError) as exc:
        raise CDPError(
            "cannot reach Edge CDP at "
            f"{CDP_HOST}:{CDP_PORT} — is Edge running with "
            "--remote-debugging-port=9222?"
        ) from exc


def _ws_url(url_contains=None):
    pages = [t for t in _targets() if t.get("type") == "page"]
    if url_contains:
        pages = [p for p in pages if url_contains in p.get("url", "")]
    if not pages:
        raise CDPError("no matching Edge tab found")
    ws = pages[0].get("webSocketDebuggerUrl")
    if not ws:
        raise CDPError("target has no debugger URL")
    return ws, pages[0].get("url", "")


class Browser:
    """One CDP session against a live Edge tab."""

    def __init__(self, url_contains=None):
        try:
            import websocket  # pip install websocket-client
        except ImportError as exc:
            raise CDPError(
                "websocket-client not installed — run: "
                "pip install websocket-client"
            ) from exc

        _ensure_edge_headless()
        ws_url, self.page_url = _ws_url(url_contains)
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self._id = 0

    def cmd(self, method, params=None, timeout=30):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method,
                                 "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CDPError(str(msg["error"].get("message")))
                return msg.get("result", {})
        raise CDPError(f"CDP timeout on {method}")

    def eval(self, js):
        """Run JS in the page; return (value, type)."""
        r = self.cmd("Runtime.evaluate",
                     {"expression": js, "returnByValue": True,
                      "awaitPromise": True})
        if r.get("exceptionDetails"):
            raise CDPError(str(r["exceptionDetails"].get("text", "JS error")))
        res = r.get("result", {})
        return res.get("value"), res.get("type")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# --- high-level actions (JS snippets run in the page) ---
#
# _DEEP is prepended to every snippet below (except raw browser_eval): it
# collects interactive elements from the main document, every open shadow
# root, and every same-origin iframe (recursively). Cross-origin iframes
# cannot be pierced from the page context — they are counted as skipped.

_DEEP = """
function __cdp_roots() {
  const roots = [{node: document, ctx: 'page'}];
  const seen = new Set([document]);
  let skipped = 0;
  for (let i = 0; i < roots.length; i++) {
    const root = roots[i].node;
    root.querySelectorAll('*').forEach(e => {
      if (e.shadowRoot && !seen.has(e.shadowRoot)) {
        seen.add(e.shadowRoot);
        roots.push({node: e.shadowRoot, ctx: 'shadow'});
      }
    });
    root.querySelectorAll('iframe').forEach(f => {
      try {
        const d = f.contentDocument;
        if (d && !seen.has(d)) { seen.add(d); roots.push({node: d, ctx: 'iframe'}); }
      } catch (err) { skipped++; }
    });
  }
  return {roots: roots, skipped: skipped};
}
function __cdp_els() {
  const found = __cdp_roots();
  const els = [];
  found.roots.forEach(r => {
    r.node.querySelectorAll('button, a, input, textarea, select, [role=button]')
      .forEach(e => els.push({el: e, ctx: r.ctx}));
  });
  return {els: els, skipped: found.skipped};
}
"""

_FIND_CLICK = """(text) => {
  const t = text.toLowerCase();
  const els = __cdp_els().els;
  const hit = els.find(({el: e}) => (e.innerText || e.value || e.getAttribute('aria-label') || '').toLowerCase().includes(t));
  if (!hit) return 'NOT-FOUND';
  const e = hit.el;
  e.scrollIntoView({block: 'center'});
  e.click();
  return 'CLICKED:' + (e.innerText || e.value || '').trim().slice(0, 60) + ' [' + hit.ctx + ']';
}"""

_FIND_FILL = """(label, text) => {
  const l = label.toLowerCase();
  const els = __cdp_els().els.map(h => h.el);
  const el = els.find(e => ((e.placeholder || '') + ' ' + (e.getAttribute('aria-label') || '') + ' ' + (e.name || '')).toLowerCase().includes(l))
        || els.find(e => e.type === 'text' || e.type === 'url' || !e.type);
  if (!el) return 'NOT-FOUND';
  el.focus();
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(el.__proto__, 'value')?.set
      || Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(el, text);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return 'FILLED:' + el.value.slice(0, 60);
}"""

_SNAPSHOT = """() => {
  const found = __cdp_els();
  const out = [];
  found.els.forEach(({el: e, ctx}) => {
    const r = e.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return;
    const label = (e.innerText || e.value || e.placeholder || e.getAttribute('aria-label') || e.name || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
    if (!label) return;
    const tag = e.tagName.toLowerCase() + (ctx === 'page' ? '' : ' [' + ctx + ']');
    out.push(tag + ' "' + label + '"');
  });
  return {url: location.href, title: document.title, elements: out.slice(0, 150), cross_origin_iframes: found.skipped};
}"""


def snapshot(url_contains=None):
    b = Browser(url_contains)
    try:
        # NB: the arrow IIFE must be wrapped in parens — `() => {}()`
        # is a SyntaxError, which CDP reports as protocol error "Uncaught".
        val, _ = b.eval(_DEEP + "(" + _SNAPSHOT + ")()")
        return {"page_url": b.page_url, **(val or {})}
    finally:
        b.close()


def click_text(text, url_contains=None):
    b = Browser(url_contains)
    try:
        val, _ = b.eval(_DEEP + f"({_FIND_CLICK})({json.dumps(text)})")
        return {"page_url": b.page_url, "result": val}
    finally:
        b.close()


def fill_field(label, text, url_contains=None):
    b = Browser(url_contains)
    try:
        val, _ = b.eval(_DEEP + f"({_FIND_FILL})({json.dumps(label)}, {json.dumps(text)})")
        return {"page_url": b.page_url, "result": val}
    finally:
        b.close()


def navigate(url, url_contains=None):
    b = Browser(url_contains)
    try:
        b.cmd("Page.navigate", {"url": url})
        return {"page_url": url, "result": "NAVIGATING"}
    finally:
        b.close()


def eval_js(js, url_contains=None):
    b = Browser(url_contains)
    try:
        val, typ = b.eval(js)
        s = json.dumps(val)[:2000] if typ != "string" else str(val)[:2000]
        return {"page_url": b.page_url, "type": typ, "value": s}
    finally:
        b.close()
