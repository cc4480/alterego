"""Structured elicitation: ask instead of failing.

Protocol-native elicitation (server-initiated ``elicitation/create``
plus the multi-round-trip ``InputRequiredResult`` flow) needs the
framework's ``Resolve`` markers and a client that answers them; Brio's
raw JSON-RPC client implements neither. This module is the working
equivalent: the server records a question with a pick-list, the client
reads it from the tool result, and answers with a follow-up tool call.
One round trip each way, no guessing, no error.

Pending elicitations live in memory only: a restart drops them, and
the event log keeps the history (``ElicitationCreated`` /
``ElicitationAnswered``). The journal write can never break a call.
"""

import secrets
import threading
import time

_LOCK = threading.Lock()
_STORE = {}
_SEQ = 0


def _journal(event_type, data):
    try:
        import events
        events.append(event_type, data, session_id=None)
    except Exception:
        pass


def _public(record):
    return {"elicitation_id": record["elicitation_id"],
            "question": record["question"],
            "options": list(record["options"]),
            "status": record["status"],
            "tool": record["tool"],
            "argument": record["argument"]}


def create_elicitation(question, options, tool="", argument="",
                       context=""):
    """Record a question with a pick-list. Returns the public record."""
    question = (question or "").strip()
    if not question:
        raise ValueError("question must be a non-empty string")
    if not isinstance(options, (list, tuple)) or not 2 <= len(options) <= 10:
        raise ValueError("options must be a list of 2 to 10 choices")
    options = [(o or "").strip() for o in options]
    if any(not o for o in options):
        raise ValueError("options must be non-empty strings")
    if len(set(options)) != len(options):
        raise ValueError("options must be unique")
    tool = (tool or "").strip()
    argument = (argument or "").strip()
    if argument and not tool:
        raise ValueError("argument requires a tool")
    if tool:
        from seams import definitions
        definition = definitions.by_name(tool)
        if definition is None:
            raise ValueError(f"unknown tool: {tool}")
        if argument and argument not in definition.args:
            raise ValueError(f"tool {tool} has no argument {argument!r}")
    global _SEQ
    with _LOCK:
        _SEQ += 1
        elicitation_id = f"elic_{secrets.token_hex(6)}"
        record = {"elicitation_id": elicitation_id,
                  "question": question, "options": options,
                  "tool": tool, "argument": argument,
                  "context": (context or "").strip(),
                  "status": "pending", "answer": None,
                  "created_ts": time.time(), "seq": _SEQ}
        _STORE[elicitation_id] = record
    _journal("ElicitationCreated", {
        "elicitation_id": elicitation_id, "question": question,
        "options": options, "tool": tool, "argument": argument})
    return _public(record)


def answer_elicitation(elicitation_id, choice):
    """Answer a pending elicitation. Fails loud on unknown, answered,
    or off-list choices."""
    with _LOCK:
        record = _STORE.get(elicitation_id)
        if record is None:
            raise ValueError(f"unknown elicitation: {elicitation_id}")
        if record["status"] != "pending":
            raise ValueError(
                f"elicitation {elicitation_id} already "
                f"{record['status']}")
        if choice not in record["options"]:
            raise ValueError(
                f"choice {choice!r} is not one of the options")
        record["status"] = "answered"
        record["answer"] = choice
        record["answered_ts"] = time.time()
    _journal("ElicitationAnswered", {
        "elicitation_id": elicitation_id, "choice": choice})
    return {"elicitation_id": elicitation_id,
            "question": record["question"],
            "choice": choice, "status": "answered"}


def list_elicitations(status="", limit=20):
    """Pending/answered/all elicitations, newest first."""
    limit = max(1, min(int(limit or 20), 100))
    status = (status or "").strip()
    if status and status not in ("pending", "answered"):
        raise ValueError("status must be '', 'pending', or 'answered'")
    with _LOCK:
        records = sorted(_STORE.values(),
                         key=lambda r: r["seq"], reverse=True)
    if status:
        records = [r for r in records if r["status"] == status]
    total = len(records)
    entries = []
    for record in records[:limit]:
        entry = _public(record)
        entry["answer"] = record["answer"]
        entries.append(entry)
    return {"elicitations": entries, "count": len(entries), "total": total}


def reset():
    """Test-only: drop all elicitations."""
    global _SEQ
    with _LOCK:
        _STORE.clear()
        _SEQ = 0
