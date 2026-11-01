"""Stable element refs for the last CDP snapshot (browser refs, item 4).

cdp_snapshot assigns refs (e1, e2, ...) to its elements in order and
stores the ref -> selector mapping. cdp_click/cdp_type accept a ref
instead of a selector/label. The store holds ONLY the last snapshot:
a new snapshot replaces the mapping wholesale, and any ref from an
older snapshot fails loud — re-snapshot, never act on a stale element.

Refs are CDP/Chrome only. Edge browser_snapshot elements are plain
strings (tag + label, no selector), so Edge refs would need the
snapshot JS rewritten; that is deferred, not faked here.
"""

_refs = {}


def store_snapshot(elements):
    """Attach refs to snapshot elements and store the mapping.

    Returns NEW dicts (the provider's elements are not mutated) with a
    'ref' key on every element that has a selector. Refs number the
    ref-able elements in order, starting at e1. Any previous snapshot's
    mapping is replaced wholesale: only the last snapshot's refs
    resolve.
    """
    global _refs
    mapping = {}
    out = []
    n = 0
    for el in elements:
        if not isinstance(el, dict):
            out.append(el)
            continue
        selector = el.get("selector") or ""
        if not selector:
            out.append(el)
            continue
        n += 1
        ref = f"e{n}"
        mapping[ref] = selector
        out.append({**el, "ref": ref})
    _refs = mapping
    return out


def resolve(ref):
    """Selector for a ref from the LAST snapshot only.

    Unknown or stale refs fail loud: the caller must re-snapshot.
    Never silently fall back to a selector from an older page.
    """
    if not isinstance(ref, str) or not ref:
        raise ValueError("ref must be a non-empty string from cdp_snapshot")
    try:
        return _refs[ref]
    except KeyError:
        raise ValueError(
            f"unknown or stale element ref {ref!r} — re-snapshot with "
            "cdp_snapshot before acting on it (refs are valid only for "
            "the most recent snapshot)")


def clear():
    """Drop the stored mapping (tests / session reset)."""
    global _refs
    _refs = {}
