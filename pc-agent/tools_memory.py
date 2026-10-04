"""Read-only recall over the on-PC memory archive.

Layout under %APPDATA%\\pc-mcp-bridge\\memory\\ (override: PC_BRIDGE_MEMORY_DIR):
  transcripts/<date>-<chat>.md     full chronological transcripts
  transcripts/<date>-subjects.md   per-session subject catalogs
  subjects/INDEX.md                master subject index across sessions
"""
import os
import re
from pathlib import Path


def _memory_dir() -> Path:
    override = os.environ.get("PC_BRIDGE_MEMORY_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "pc-mcp-bridge" / "memory"


def _score(text: str, terms: list) -> tuple:
    low = text.lower()
    term_hits = sum(low.count(t) for t in terms)
    heading_hits = sum(
        1 for line in text.splitlines() if line.startswith("#")
        for t in terms if t in line.lower()
    )
    return heading_hits, term_hits


def memory_recall(query: str, limit: int = 5) -> dict:
    """Keyword search over the memory archive. No approval (read-only)."""
    if not query or not str(query).strip():
        raise ValueError("query must be a non-empty string")
    limit = max(1, min(int(limit), 20))
    terms = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 1]
    if not terms:
        raise ValueError("query has no searchable terms")
    root = _memory_dir()
    if not root.is_dir():
        return {"query": query, "results": [],
                "note": f"memory archive not found at {root}"}
    files = sorted(root.rglob("*.md"))
    scored = []
    for md in files:
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hh, th = _score(text, terms)
        if th == 0:
            continue
        low = text.lower()
        idx = min(low.find(t) for t in terms if t in low)
        snippet = text[max(0, idx - 300):idx + 400].strip()
        scored.append((hh, th, str(md.relative_to(root)), snippet))
    scored.sort(key=lambda r: (r[0], r[1]), reverse=True)
    results = [{"file": f, "subject_hits": hh, "term_hits": th, "snippet": s}
               for hh, th, f, s in scored[:limit]]
    return {"query": query, "results": results,
            "files_searched": len(files), "files_matched": len(scored)}
