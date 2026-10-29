"""Risk-shaped arbitration for tool trajectories.

Implements the minimal viable arbitration spec: score candidate tool
sequences with an interpretable weighted-linear function over blast
radius, recoverability, side effects, optionality, and human cost, then
map the score to a risk tier (auto / confirm / explicit_intent) with a
one-line rationale derived from the dominant terms.

score = w1*(1-blast) + w2*recoverability + w3*(1-side_effects)
        + w4*optionality - w5*human_cost

A *trajectory* is one candidate sequence of tools, in execution order;
`trajectories` is the list of candidates to rank against each other.
Only tool names affect scoring (profiles are per-tool), so step args
are accepted but ignored. Callers may write each trajectory in any of
the shapes listed in TRAJECTORY_SHAPES; every shape is normalised to
the internal form — a list of tool-name strings — before scoring.
"""
from typing import TypedDict, Union

try:
    from typing import NotRequired
except ImportError:  # Python < 3.11: typing_extensions ships with pydantic
    from typing_extensions import NotRequired

from tool_profiles import PROFILES, get_profile, approval_tier
import toolcall

_BLAST_NORM = {"none": 0.0, "session": 0.25, "external": 0.35,
               "user_data": 0.6, "system": 1.0}
W1, W2, W3, W4, W5 = 0.25, 0.35, 0.15, 0.25, 0.10

# The accepted trajectory shapes, stated in every contract surface
# (this module, the MCP schema/docstring, the ToolDef) and in every
# shape error, so a rejected caller can fix their payload from the
# error alone.
TRAJECTORY_SHAPES = (
    'each trajectory must be one of: '
    '["tool_name", ...] (a list of tool names), '
    '[{"tool": "tool_name", "args": {...}}, ...] (a list of step '
    'objects; "args" is optional and ignored), '
    '{"tools": ["tool_name", ...]} (a dict naming its tools), '
    '{"tool": "tool_name", "args": {...}} (a single step object), or '
    '"tool_name" (a single tool name); '
    'trajectories must be a non-empty list of trajectories'
)


class TrajectoryStep(TypedDict):
    """One step of a trajectory: a tool name plus optional call args."""
    tool: str
    args: NotRequired[dict]


class TrajectoryTools(TypedDict):
    """A trajectory written as a dict naming its tools in order."""
    tools: list[Union[str, TrajectoryStep]]


# One candidate trajectory, in any accepted shape. This alias is what
# the MCP schema for arbitrate_tool is generated from — items are
# typed, never a bare {}.
Trajectory = Union[
    list[Union[str, TrajectoryStep]],
    TrajectoryTools,
    TrajectoryStep,
    str,
]


def _fail(what, value):
    raise ValueError(f"invalid {what} {value!r}: {TRAJECTORY_SHAPES}")


def _step_name(step, what="step"):
    """Tool name from one step: a bare name or a step object."""
    if isinstance(step, str):
        if not step:
            _fail(what, step)
        return step
    if isinstance(step, dict):
        if "tools" in step:
            _fail(what, step)  # a tools-dict is a trajectory, not a step
        name = step.get("tool")
        if not isinstance(name, str) or not name:
            _fail(what, step)
        args = step.get("args")
        if args is not None and not isinstance(args, dict):
            _fail(what, step)
        return name
    _fail(what, step)


def _names_from_list(items, what):
    """Tool names from a list of names / step objects / tools-dicts."""
    names = []
    for item in items:
        if isinstance(item, dict) and "tools" in item:
            names.extend(_names_from_tools(item["tools"], what))
        else:
            names.append(_step_name(item, what))
    return names


def _names_from_tools(tools, what):
    if not isinstance(tools, (list, tuple)) or not tools:
        _fail(what, tools)
    return _names_from_list(list(tools), what)


def normalize_trajectory(candidate):
    """Normalise one trajectory (any accepted shape) to a list of
    tool-name strings. Raises ValueError stating the expected shapes
    for anything else."""
    if isinstance(candidate, str):
        if not candidate:
            _fail("trajectory", candidate)
        return [candidate]
    if isinstance(candidate, dict):
        if "tools" in candidate:
            return _names_from_tools(candidate["tools"], "trajectory")
        return [_step_name(candidate, "trajectory")]
    if isinstance(candidate, (list, tuple)):
        if not candidate:
            _fail("trajectory", candidate)
        return _names_from_list(list(candidate), "trajectory")
    _fail("trajectory", candidate)


def normalize_trajectories(trajectories):
    """Normalise the arbitrate argument to a list of tool-name lists.

    `trajectories` is a list of candidate trajectories (each in any
    accepted shape); a single trajectory written as a bare dict
    ({"tools": [...]} or {"tool": ...}) is treated as one candidate.
    """
    if isinstance(trajectories, dict):
        return [normalize_trajectory(trajectories)]
    if not isinstance(trajectories, (list, tuple)) or not trajectories:
        _fail("trajectories", trajectories)
    return [normalize_trajectory(c) for c in trajectories]


def _metrics(tools):
    profs = [get_profile(t) for t in tools]
    blast = max(_BLAST_NORM.get(p["blast_radius"], 1.0) for p in profs)
    rec = min(p["recoverability"] / 10 for p in profs)
    side = min(1.0, sum(len(p["side_effects"]) for p in profs) / 8)
    opt = sum(1 for p in profs if p["recoverability"] >= 7) / len(profs)
    cost = min(1.0, sum(1 for t in tools
                        if approval_tier(t) != "silent") / 8)
    return blast, rec, side, opt, cost


def score_trajectory(tools):
    """Score one tool sequence; returns score, tier, rationale, metrics.

    `tools` is one trajectory in any accepted shape (see
    TRAJECTORY_SHAPES); it is normalised to tool names first.
    """
    tools = normalize_trajectory(tools)
    for t in tools:
        if t not in PROFILES:
            raise ValueError(f"unknown tool: {t}")
    blast, rec, side, opt, cost = _metrics(tools)
    score = (W1 * (1 - blast) + W2 * rec + W3 * (1 - side)
             + W4 * opt - W5 * cost)
    score = round(max(0.0, min(1.0, score)), 3)
    tier = "auto" if score > 0.8 else "confirm" if score >= 0.5 else "explicit_intent"
    approvals = sum(1 for t in tools if approval_tier(t) != "silent")
    bits = []
    if blast >= 0.6:
        bits.append(f"high blast radius ({max(get_profile(t)['blast_radius'] for t in tools)})")
    elif blast <= 0.25:
        bits.append("low blast radius")
    if rec >= 0.8:
        bits.append("high recoverability")
    elif rec < 0.5:
        bits.append("low recoverability")
    bits.append(f"{approvals} approval{'s' if approvals != 1 else ''} needed")
    rationale = f"score {score}: " + "; ".join(bits) + f" -> {tier}"
    return {"tools": tools, "score": score, "tier": tier,
            "rationale": rationale,
            "metrics": {"blast_radius": round(blast, 3),
                        "recoverability": round(rec, 3),
                        "side_effects": round(side, 3),
                        "optionality": round(opt, 3),
                        "human_cost": round(cost, 3)}}


def explain_trajectory(tools):
    """Plan presentation: steps with risk, side effects, rollback, blast."""
    s = score_trajectory(tools)
    steps = []
    for t in s["tools"]:
        p = get_profile(t)
        steps.append({"tool": t, "tier": approval_tier(t),
                      "blast_radius": p["blast_radius"],
                      "recoverability": p["recoverability"],
                      "side_effects": p["side_effects"],
                      "compensating_action": p["compensating_action"]})
    rollback = [f"{st['tool']}: {st['compensating_action'] or 'no rollback'}"
                for st in steps if st["compensating_action"]]
    return {"score": s["score"], "tier": s["tier"],
            "rationale": s["rationale"], "steps": steps,
            "rollback_plan": rollback,
            "max_blast_radius": max(st["blast_radius"] for st in steps)}


def rank_trajectories(candidates):
    """Score and rank candidate trajectories, best first.

    Each candidate may use any accepted trajectory shape; all are
    normalised before scoring, so equivalent shapes rank identically.
    """
    ranked = [score_trajectory(c) for c in normalize_trajectories(candidates)]
    ranked.sort(key=lambda r: r["score"], reverse=True)
    return {"ranked": ranked, "recommended": ranked[0]}


def arbitrate(trajectories):
    """Rank candidates; each gets score, tier, rationale."""
    return rank_trajectories(trajectories)


def arbitrate_tool(trajectories: list[Trajectory]) -> dict:
    """Rank candidate tool sequences by risk. No approval.

    trajectories is a non-empty list of trajectories (candidate tool
    sequences to compare). Each trajectory may be written as:
    ["tool_name", ...] — a list of tool names;
    [{"tool": "tool_name", "args": {...}}, ...] — a list of step
    objects ("args" optional, accepted but ignored: only tool names
    affect scoring); {"tools": ["tool_name", ...]} — a dict naming
    its tools; {"tool": "tool_name", "args": {...}} — a single step
    object; or "tool_name" — a single tool name. All shapes are
    normalised to tool-name lists before scoring.
    """
    return toolcall.call("arbitrate", {"trajectories": trajectories})
