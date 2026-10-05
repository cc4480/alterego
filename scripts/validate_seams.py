"""Validate seam definitions against the Windows provider implementations.

Checks for every ToolDef in seams.definitions:
  1. platform_notes is present and non-empty (the honest-gaps documentation).
  2. arg schema matches the provider function signature: required/optional
     names, defaults, and type annotations (when the provider annotates).
  3. result_keys == union of dict-literal keys across every `return` in the
     provider function's own body (nested functions excluded), following
     browser_* delegation into browser_cdp and the CDP snapshot's JS
     snippet for its **-unpacked keys.
  4. approval_tier == tool_profiles.approval_tier(name).

Definitions describe; this script only reads. Exit 0 on success.
"""
import ast
import inspect
import os
import re
import sys

PC_AGENT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "pc-agent")
sys.path.insert(0, os.path.normpath(PC_AGENT))

from seams import definitions          # noqa: E402
from tool_profiles import approval_tier  # noqa: E402
import seams.providers.windows as _win_pkg  # noqa: E402

FAILURES = []


def fail(tool, msg):
    FAILURES.append(f"{tool}: {msg}")


# ---- provider function resolution -----------------------------------------

def _provider_fn(defn):
    """Resolve a definition to its windows provider function."""
    mod = __import__(f"seams.providers.windows.{defn.group}",
                     fromlist=["*"])
    tool_map = getattr(mod, "TOOL_MAP", None)
    if tool_map is not None:
        fn = tool_map.get(defn.name)
    else:
        fn = getattr(mod, defn.name, None)
    if fn is None:
        fail(defn.name, f"no windows provider function for group "
                        f"{defn.group!r}")
    return fn


# ---- signature check -------------------------------------------------------

def check_signature(defn, fn):
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError) as e:
        fail(defn.name, f"cannot inspect signature: {e}")
        return
    req, opt = set(), {}
    for pname, p in sig.parameters.items():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.default is inspect.Parameter.empty:
            req.add(pname)
        else:
            opt[pname] = p.default
    want_req = {a for a, s in defn.args.items() if s["required"]}
    want_opt = {a: s.get("default") for a, s in defn.args.items()
                if not s["required"]}
    if req != want_req:
        fail(defn.name, f"required args {sorted(req)} != "
                        f"definition {sorted(want_req)}")
    if set(opt) != set(want_opt):
        fail(defn.name, f"optional args {sorted(opt)} != "
                        f"definition {sorted(want_opt)}")
    for a, default in want_opt.items():
        if a in opt and opt[a] != default:
            fail(defn.name, f"default for {a}: provider {opt[a]!r} != "
                            f"definition {default!r}")
    for pname, p in sig.parameters.items():
        if p.annotation is inspect.Parameter.empty or pname not in defn.args:
            continue
        want_type = defn.args[pname]["type"]
        got = getattr(p.annotation, "__name__", str(p.annotation)).lower()
        if got != want_type:
            fail(defn.name, f"type of {pname}: provider {got} != "
                            f"definition {want_type}")


# ---- result-key check (AST, own-body returns only) --------------------------

class _Returns(ast.NodeVisitor):
    """Union of dict-literal keys from a function's own returns."""

    def __init__(self):
        self.keys = set()
        self.delegates = []   # ("browser_cdp", attr) call targets
        self.local_calls = []  # same-module Name(...) call targets
        self.starred = []     # source snippets of **-unpacks
        self._depth = 0

    def visit_FunctionDef(self, node):
        self._depth += 1
        if self._depth == 1:
            self.generic_visit(node)
        self._depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Return(self, node):
        if self._depth != 1 or node.value is None:
            return
        v = node.value
        if isinstance(v, ast.Dict):
            for k in v.keys:
                if k is None:
                    self.starred.append(ast.unparse(v))
                else:
                    self.keys.add(ast.literal_eval(k))
        elif (isinstance(v, ast.Call)
              and isinstance(v.func, ast.Attribute)
              and isinstance(v.func.value, ast.Name)
              and v.func.value.id == "browser_cdp"):
            self.delegates.append(v.func.attr)
        elif (isinstance(v, ast.Call)
              and isinstance(v.func, ast.Name)):
            # return helper(...) — same-module helper, follow it
            self.local_calls.append(v.func.id)
        # other expression returns (bare/None) carry no contract keys


def _func_node(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    return None


def _js_snapshot_keys():
    """Keys of the JS object returned by the _SNAPSHOT snippet in
    browser_cdp.py (snapshot() **-unpacks it into its result dict)."""
    import browser_cdp
    src = inspect.getsource(browser_cdp)
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) \
                and any(isinstance(t, ast.Name) and t.id == "_SNAPSHOT"
                        for t in node.targets) \
                and isinstance(node.value, ast.Constant):
            js = node.value.value
            m = re.search(r"return\s*\{(.*?)\}\s*;", js, re.DOTALL)
            if not m:
                return None
            # flat object: split top-level commas, take key before ':'
            keys, depth = set(), 0
            for chunk in re.split(r",(?![^()]*\))", m.group(1)):
                k = chunk.strip().split(":", 1)[0].strip()
                if re.fullmatch(r"[A-Za-z_]\w*", k):
                    keys.add(k)
            return keys
    return None


def _scan_source_keys(source_path, func_name):
    with open(source_path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    node = _func_node(tree, func_name)
    if node is None:
        return None, [f"function {func_name} not found in {source_path}"]
    vis = _Returns()
    vis.visit(node)
    return vis, []


def _collect_keys(source_path, func_name, seen):
    """Union of return-dict keys for func_name, following same-module
    helper calls. seen guards against cycles."""
    key = (source_path, func_name)
    if key in seen:
        return set()
    seen.add(key)
    vis, errs = _scan_source_keys(source_path, func_name)
    if errs:
        raise RuntimeError("; ".join(errs))
    keys = set(vis.keys)
    with open(source_path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for callee in vis.local_calls:
        if _func_node(tree, callee) is not None:
            _, sub = _collect_keys(source_path, callee, seen)
            keys |= sub
    return vis, keys


def check_result_keys(defn, fn):
    src = inspect.getsourcefile(fn)
    if src is None:
        fail(defn.name, "provider function has no source file")
        return
    try:
        vis, keys = _collect_keys(src, fn.__name__, set())
    except RuntimeError as e:
        fail(defn.name, str(e))
        return
    for attr in vis.delegates:
        import browser_cdp
        cdp_src = inspect.getsourcefile(browser_cdp)
        dvis, derrs = _scan_source_keys(cdp_src, attr)
        if derrs:
            for e in derrs:
                fail(defn.name, e)
            return
        keys |= dvis.keys
        for s in dvis.starred:
            if "val or {}" in s:  # snapshot(): **(val or {}) from JS
                js_keys = _js_snapshot_keys()
                if js_keys is None:
                    fail(defn.name, "could not parse _SNAPSHOT JS keys")
                else:
                    keys |= js_keys
            else:
                fail(defn.name, f"unresolved **-unpack in return: {s[:60]}")
    if vis.starred and defn.group != "browser":
        fail(defn.name, f"unresolved **-unpack(s): {vis.starred}")
    if set(defn.result_keys) != keys:
        fail(defn.name, f"result_keys {sorted(defn.result_keys)} != "
                        f"provider returns {sorted(keys)}")


# ---- main ------------------------------------------------------------------

def main():
    defs = definitions.ALL_DEFS
    print(f"definitions: {len(defs)}")
    if len(defs) != 50:
        fail("ALL", f"expected 50 definitions, got {len(defs)}")
    for d in defs:
        if not d.platform_notes or not d.platform_notes.strip():
            fail(d.name, "platform_notes missing or empty")
        fn = _provider_fn(d)
        if fn is None:
            continue
        check_signature(d, fn)
        check_result_keys(d, fn)
        want_tier = approval_tier(d.name)
        if d.approval_tier != want_tier:
            fail(d.name, f"approval_tier {d.approval_tier!r} != "
                         f"tool_profiles {want_tier!r}")
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURES:")
        for f in FAILURES:
            print("  FAIL", f)
        return 1
    print("ALL SEAM DEFINITION CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
