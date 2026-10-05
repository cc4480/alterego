"""Risk-shaped arbitration for tool trajectories.

Implements the minimal viable arbitration spec: score candidate tool
sequences with an interpretable weighted-linear function over blast
radius, recoverability, side effects, optionality, and human cost, then
map the score to a risk tier (auto / confirm / explicit_intent) with a
one-line rationale derived from the dominant terms.

score = w1*(1-blast) + w2*recoverability + w3*(1-side_effects)
        + w4*optionality - w5*human_cost
"""
from tool_profiles import PROFILES, get_profile, approval_tier
import toolcall

_BLAST_NORM = {"none": 0.0, "session": 0.25, "external": 0.35,
               "user_data": 0.6, "system": 1.0}
W1, W2, W3, W4, W5 = 0.25, 0.35, 0.15, 0.25, 0.10


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
    """Score one tool sequence; returns score, tier, rationale, metrics."""
    if not tools or not isinstance(tools, list):
        raise ValueError("tools must be a non-empty list of tool names")
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
    for t in tools:
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
    """Score and rank candidate trajectories, best first."""
    if not candidates or not isinstance(candidates, list):
        raise ValueError("candidates must be a non-empty list of tool lists")
    ranked = [score_trajectory(c) for c in candidates]
    ranked.sort(key=lambda r: r["score"], reverse=True)
    return {"ranked": ranked, "recommended": ranked[0]}


def arbitrate(trajectories):
    """Rank candidates; each gets score, tier, rationale."""
    return rank_trajectories(trajectories)


def arbitrate_tool(trajectories: list) -> dict:
    """Rank candidate tool sequences by risk. No approval."""
    return toolcall.call("arbitrate", {"trajectories": trajectories})
