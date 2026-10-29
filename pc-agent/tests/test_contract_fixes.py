"""Contract-fix tests (Wave 2 slice G, live-test record 2026-10-29).

1. arbitrate_tool: every accepted trajectory shape runs end-to-end
   through the mock pipeline, equivalent shapes normalise (and rank)
   identically, garbage fails with an error stating the expected
   shapes, and the published schema types trajectory items (no
   bare {}).
2. task_checkpoint: step is 1-based everywhere — schema doc,
   checkpoint recording, task_status reporting — in BOTH the mock
   provider (through the pipeline) and the signed Windows provider
   implementation (exercised directly; it is portable Python).
"""
import pytest
from pydantic import TypeAdapter

import arbitrate
import toolcall
from seams import definitions


# ---------------------------------------------------------------------
# arbitrate: accepted shapes, end-to-end through the pipeline (mock)
# ---------------------------------------------------------------------

# The two candidates from the live failure, in every accepted shape.
SHAPE_VARIANTS = [
    # canonical: lists of tool names (mock pipeline's original shape)
    [["web_search"], ["web_fetch"]],
    # lists of {"tool", "args"} step objects
    [[{"tool": "web_search", "args": {"query": "x"}}],
     [{"tool": "web_fetch", "args": {"url": "https://example.com"}}]],
    # live shape 1: bare step objects, one per candidate
    [{"tool": "web_search", "args": {"query": "x"}},
     {"tool": "web_fetch", "args": {"url": "https://example.com"}}],
    # live shape 2: {"tools": [...]} dicts
    [{"tools": ["web_search"]}, {"tools": ["web_fetch"]}],
    # {"tools": [...]} carrying step objects
    [{"tools": [{"tool": "web_search", "args": {}}]},
     {"tools": [{"tool": "web_fetch"}]}],
    # bare tool names, one per candidate
    ["web_search", "web_fetch"],
]


@pytest.mark.parametrize("trajectories", SHAPE_VARIANTS)
def test_arbitrate_accepted_shapes_end_to_end(trajectories):
    r = toolcall.call("arbitrate", {"trajectories": trajectories})
    assert set(r) == {"ranked", "recommended"}, r
    assert [t["tools"] for t in r["ranked"]] == [["web_search"],
                                                  ["web_fetch"]] or \
        {tuple(t["tools"]) for t in r["ranked"]} == {("web_search",),
                                                     ("web_fetch",)}
    assert r["recommended"] == r["ranked"][0]


def test_arbitrate_multi_step_shapes_end_to_end():
    variants = [
        [["screenshot", "shell_exec"]],
        [[{"tool": "screenshot"}, {"tool": "shell_exec", "args": {}}]],
        [{"tools": ["screenshot", "shell_exec"]}],
        [{"tools": [{"tool": "screenshot"}, {"tool": "shell_exec"}]}],
        # mixed names and step objects in one list trajectory
        [["screenshot", {"tool": "shell_exec"}]],
    ]
    for trajectories in variants:
        r = toolcall.call("arbitrate", {"trajectories": trajectories})
        assert set(r) == {"ranked", "recommended"}, (trajectories, r)
        assert r["ranked"][0]["tools"] == ["screenshot", "shell_exec"]


def test_arbitrate_shape_equivalence_of_results():
    """Every variant of the same candidates ranks identically."""
    results = [arbitrate.arbitrate(v) for v in SHAPE_VARIANTS]
    first = results[0]
    for other in results[1:]:
        assert other == first


def test_arbitrate_normalisation_equivalence():
    for v in SHAPE_VARIANTS:
        assert arbitrate.normalize_trajectories(v) == [["web_search"],
                                                       ["web_fetch"]]
    assert arbitrate.normalize_trajectory({"tools": ["a", "b"]}) == ["a", "b"]
    assert arbitrate.normalize_trajectory([{"tool": "a"},
                                           {"tool": "b"}]) == ["a", "b"]
    assert arbitrate.normalize_trajectory({"tool": "a"}) == ["a"]
    assert arbitrate.normalize_trajectory("a") == ["a"]


def test_arbitrate_canonical_shape_still_works():
    r = toolcall.call("arbitrate",
                      {"trajectories": [["screenshot"], ["shell_exec"]]})
    assert r["recommended"]["tools"] == ["screenshot"]


# ---------------------------------------------------------------------
# arbitrate: garbage still fails, with the expected shapes in the error
# ---------------------------------------------------------------------

GARBAGE = [
    None,
    "screenshot",                      # top-level string, not a list
    42,
    [],                                # empty candidate list
    [[]],                              # empty trajectory
    [123],                             # nonsense trajectory type
    [None],
    [{"nope": 1}],                     # dict with neither tool nor tools
    [[{"args": {}}]],                  # step object without a tool
    [[{"tool": 123}]],                 # tool name not a string
    [[{"tool": ""}]],                  # empty tool name
    [{"tools": "web_search"}],         # tools not a list
    [{"tools": []}],                   # empty tools list
    [[{"tool": "screenshot", "args": "x"}]],  # args not an object
]


@pytest.mark.parametrize("trajectories", GARBAGE)
def test_arbitrate_garbage_rejected_with_shapes(trajectories):
    with pytest.raises(ValueError) as ei:
        arbitrate.arbitrate(trajectories)
    msg = str(ei.value)
    assert "each trajectory must be one of" in msg
    assert '["tool_name", ...]' in msg
    assert '{"tools": ["tool_name", ...]}' in msg


@pytest.mark.parametrize("trajectories", GARBAGE)
def test_arbitrate_garbage_error_surfaces_through_pipeline(trajectories):
    r = toolcall.call("arbitrate", {"trajectories": trajectories})
    assert set(r) == {"error"}, r
    assert "each trajectory must be one of" in r["error"]


def test_arbitrate_unknown_tool_still_rejected():
    with pytest.raises(ValueError, match="unknown tool: no_such_tool"):
        arbitrate.arbitrate([["no_such_tool"]])
    r = toolcall.call("arbitrate", {"trajectories": [["no_such_tool"]]})
    assert "unknown tool: no_such_tool" in r["error"]


# ---------------------------------------------------------------------
# arbitrate: the published schema IS the contract
# ---------------------------------------------------------------------

def test_arbitrate_published_schema_types_trajectory_items():
    """The MCP schema is generated from arbitrate_tool's annotation;
    its trajectories items must be typed, never a bare {}."""
    hints = arbitrate.arbitrate_tool.__annotations__
    schema = TypeAdapter(hints["trajectories"]).json_schema()
    items = schema["items"]
    assert items != {}
    assert "anyOf" in items
    defs = schema.get("$defs", {})
    assert "TrajectoryStep" in defs
    assert defs["TrajectoryStep"]["required"] == ["tool"]
    assert "TrajectoryTools" in defs
    assert defs["TrajectoryTools"]["required"] == ["tools"]


def test_arbitrate_tooldef_types_items_and_documents_shapes():
    defn = definitions.by_name("arbitrate")
    spec = defn.args["trajectories"]
    assert spec["items"] != {}
    assert "anyOf" in spec["items"]
    for fragment in ('["tool_name", ...]', '{"tools"',
                     '{"tool": "tool_name"'):
        assert fragment in defn.doc
    # The MCP-facing wrapper docstring states the shapes too.
    doc = arbitrate.arbitrate_tool.__doc__
    for fragment in ('["tool_name", ...]', '{"tools"',
                     '{"tool": "tool_name"'):
        assert fragment in doc


# ---------------------------------------------------------------------
# tasks: 1-based step, through the pipeline (mock provider)
# ---------------------------------------------------------------------

PLAN = ["step one recon", "step two verify"]


def _make_task():
    created = toolcall.call("task_create", {"goal": "g", "plan": PLAN})
    return created["task_id"]


def test_checkpoint_step_1_lands_on_first_plan_step():
    tid = _make_task()
    cp = toolcall.call("task_checkpoint",
                       {"task_id": tid, "step": 1, "result": "recon ok"})
    assert cp == {"task_id": tid, "step": 1, "done": True}
    st = toolcall.call("task_status", {"task_id": tid})
    assert len(st["checkpoints"]) == 1
    chk = st["checkpoints"][0]
    assert chk["step"] == 1
    assert chk["plan_step"] == "step one recon"
    assert st["done_steps"] == [1]


def test_checkpoint_step_2_lands_on_second_plan_step():
    tid = _make_task()
    toolcall.call("task_checkpoint",
                  {"task_id": tid, "step": 2, "result": "verified"})
    st = toolcall.call("task_status", {"task_id": tid})
    assert st["checkpoints"][0]["plan_step"] == "step two verify"
    assert st["done_steps"] == [2]


@pytest.mark.parametrize("bad_step", [0, 3, -1, 100])
def test_checkpoint_out_of_range_steps_error(bad_step):
    tid = _make_task()
    r = toolcall.call("task_checkpoint",
                      {"task_id": tid, "step": bad_step, "result": "x"})
    assert set(r) == {"error"}, r
    assert "1..2" in r["error"]
    assert "1-based" in r["error"]
    # Nothing was recorded.
    st = toolcall.call("task_status", {"task_id": tid})
    assert st["checkpoints"] == []
    assert st["done_steps"] == []


def test_checkpoint_step_bool_is_not_step_1():
    tid = _make_task()
    r = toolcall.call("task_checkpoint",
                      {"task_id": tid, "step": True, "result": "x"})
    assert set(r) == {"error"}, r


# ---------------------------------------------------------------------
# tasks: 1-based step in the signed Windows provider implementation
# (the code that actually runs on the PC; portable Python + tmp APPDATA)
# ---------------------------------------------------------------------

def _win_tasks():
    from seams.providers.windows import tasks as win_tasks
    return win_tasks


def test_windows_tasks_checkpoint_is_1_based():
    wt = _win_tasks()
    created = wt.create("g", PLAN)
    tid = created["task_id"]
    out = wt.checkpoint(tid, 1, "recon ok")
    assert out == {"task_id": tid, "step": 1, "done": True}
    st = wt.status(tid)
    assert st["checkpoints"][0]["step"] == 1
    assert st["checkpoints"][0]["plan_step"] == "step one recon"
    assert st["done_steps"] == [1]
    wt.checkpoint(tid, 2, "verified")
    st = wt.status(tid)
    assert st["checkpoints"][1]["plan_step"] == "step two verify"
    assert st["done_steps"] == [1, 2]


@pytest.mark.parametrize("bad_step", [0, 3, -1])
def test_windows_tasks_out_of_range_steps_error(bad_step):
    wt = _win_tasks()
    tid = wt.create("g", PLAN)["task_id"]
    with pytest.raises(ValueError) as ei:
        wt.checkpoint(tid, bad_step, "x")
    assert "1..2" in str(ei.value)
    assert "1-based" in str(ei.value)
    assert wt.status(tid)["checkpoints"] == []


# ---------------------------------------------------------------------
# tasks: schema docs state the 1-based contract
# ---------------------------------------------------------------------

def test_task_defs_document_1_based_step():
    cp_def = definitions.by_name("task_checkpoint")
    assert "1-based" in cp_def.doc
    assert "step=1 is the first plan step" in cp_def.doc
    assert cp_def.args["step"]["description"].startswith("1-based")
    status_def = definitions.by_name("task_status")
    assert "1-based" in status_def.doc
    create_def = definitions.by_name("task_create")
    assert "numbered from 1" in create_def.doc
    # The MCP-facing wrapper docstrings agree.
    wt = _win_tasks()
    assert "1-based" in wt.task_checkpoint.__doc__
    assert "step=1 is the first plan step" in wt.task_checkpoint.__doc__
