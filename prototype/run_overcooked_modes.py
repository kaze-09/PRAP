"""Official Overcooked-AI cramped_room, four cooperation interfaces.

Protocol: PRAP_overcooked_protocol.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import types
from collections import deque

sys.modules.setdefault("cv2", types.ModuleType("cv2"))
OFFICIAL = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "external", "overcooked_ai_official", "src")
)
sys.path.insert(0, OFFICIAL)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm_agent import load_clients  # noqa: E402
from overcooked_ai_py.mdp.actions import Action, Direction  # noqa: E402
from overcooked_ai_py.mdp.overcooked_mdp import OvercookedGridworld  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "ccf_followup", "overcooked_modes.jsonl")
ARMS = ("independent", "dialogue", "dispatcher", "lock")
MACROS = ("onion", "pot", "dish", "serve", "wait")
HORIZON = 400
MAX_DECISIONS = 40
LAYOUT = "cramped_room"

SYSTEM = (
    "You are a cook in the Overcooked cramped_room layout. "
    "A soup needs three onions in the pot, then a dish to pick it up, then delivery at the serve counter. "
    "Reply with one macro per requested agent, nothing else. "
    "Macros: onion, pot, dish, serve, wait."
)


def walkable(mdp, pos):
    x, y = pos
    if not (0 <= x < mdp.width and 0 <= y < mdp.height):
        return False
    return mdp.terrain_mtx[y][x] == " "


def adjacent_stands(mdp, target):
    stands = []
    tx, ty = target
    for d in Direction.ALL_DIRECTIONS:
        stand = (tx - d[0], ty - d[1])
        if walkable(mdp, stand):
            stands.append((stand, d))
    return stands


def bfs_actions(mdp, start, facing, target, blocked):
    """Primitive actions to stand next to target, face it, and interact."""
    stands = adjacent_stands(mdp, target)
    if not stands:
        return None
    goal = {s for s, _ in stands}
    face = {s: d for s, d in stands}
    q = deque([(start, [])])
    seen = {start}
    while q:
        pos, path = q.popleft()
        if pos in goal:
            actions = []
            for step in path:
                actions.append(step)
            if facing != face[pos] and path:
                pass
            # last orientation is the last move, or the original facing if no move
            orient = path[-1] if path else facing
            if orient != face[pos]:
                actions.append(face[pos])
            actions.append(Action.INTERACT)
            return actions
        x, y = pos
        for d in Direction.ALL_DIRECTIONS:
            nxt = (x + d[0], y + d[1])
            if nxt in seen or nxt in blocked or not walkable(mdp, nxt):
                continue
            seen.add(nxt)
            q.append((nxt, path + [d]))
    return None


def nearest(mdp, start, facing, targets, blocked):
    best = None
    for target in targets:
        actions = bfs_actions(mdp, start, facing, target, blocked)
        if actions is not None and (best is None or len(actions) < len(best)):
            best = actions
    return best


def pot_summary(mdp, state):
    bits = []
    for pos in mdp.get_pot_locations():
        obj = state.get_object(pos) if state.has_object(pos) else None
        if obj is None:
            bits.append(f"{pos}:empty")
        else:
            n = len(obj.ingredients)
            if obj.is_ready:
                status = "ready"
            elif obj.is_cooking:
                status = "cooking"
            else:
                status = "idle"
            bits.append(f"{pos}:{n} onions {status}")
    return "; ".join(bits)


def describe(mdp, state, t, soups):
    lines = [f"t={t}/{HORIZON} soups={soups}", "pot " + pot_summary(mdp, state)]
    for i, p in enumerate(state.players):
        held = p.held_object.name if p.held_object is not None else "nothing"
        lines.append(f"agent{i} at {p.position} facing {Direction.DIRECTION_TO_NAME[p.orientation]} holding {held}")
    return "\n".join(lines)


def targets_for(mdp, state, player, macro):
    held = player.held_object.name if player.held_object is not None else None
    if macro == "onion":
        return mdp.get_onion_dispenser_locations()
    if macro == "dish":
        return mdp.get_dish_dispenser_locations()
    if macro == "serve":
        if held == "soup":
            return mdp.get_serving_locations()
        return mdp.get_pot_locations()
    if macro == "pot":
        return mdp.get_pot_locations()
    return []


def parse_macro(text, agent):
    low = (text or "").lower()
    for name in MACROS:
        if name in low:
            return name
    return "wait"


def parse_both(text):
    lines = (text or "").lower().splitlines() or [text or ""]
    found = []
    for line in lines:
        hit = "wait"
        for name in MACROS:
            if name in line:
                hit = name
                break
        found.append(hit)
        if len(found) == 2:
            break
    while len(found) < 2:
        found.append("wait")
    return found[:2]


def apply_lock(macros):
    scarce = {"onion", "pot"}
    if macros[0] == macros[1] and macros[0] in scarce:
        return [macros[0], "wait"], 1
    return macros, 0


def ask(client, user):
    return client.chat(SYSTEM, user, max_tokens=40)


def decide_macros(client, arm, text, last):
    if arm == "dispatcher":
        return parse_both(ask(client, text + "\nAssign a macro to agent0 and agent1, one per line.")), 0
    macros = []
    for i in range(2):
        extra = f"\nOther agent's last macro: {last[1 - i]}" if arm == "dialogue" else ""
        macros.append(parse_macro(ask(client, text + extra + f"\nYou are agent{i}. One macro."), i))
    hits = 0
    if arm == "lock":
        macros, hits = apply_lock(macros)
    return macros, hits


def step_pair(mdp, state, actions):
    # get_actions may reject illegal motion; replace with stay
    legal = mdp.get_actions(state)
    safe = []
    for i, action in enumerate(actions):
        safe.append(action if action in legal[i] else Action.STAY)
    return mdp.get_state_transition(state, safe)


def run_episode(mdp, arm, seed, client, scripted=False, max_decisions=None, stop_at_horizon=True):
    limit = MAX_DECISIONS if max_decisions is None else max_decisions
    state = mdp.get_standard_start_state()
    soups = 0
    failed = 0
    contention = 0
    last = ["wait", "wait"]
    decisions = 0
    t = 0
    while decisions < limit and (not stop_at_horizon or t < HORIZON):
        if scripted:
            macros, hits = scripted_macros(mdp, state), 0
        else:
            macros, hits = decide_macros(client, arm, describe(mdp, state, t, soups), last)
        contention += hits
        decisions += 1
        last = macros
        if all(m == "wait" for m in macros):
            for _ in range(5):
                if stop_at_horizon and t >= HORIZON:
                    break
                state, info = step_pair(mdp, state, [Action.STAY, Action.STAY])
                t += 1
                soups += sum(info["sparse_reward_by_agent"]) / 20
            continue
        progressed = [False, False]
        interacted = [False, False]
        for _k in range(12):
            if (stop_at_horizon and t >= HORIZON) or all(
                interacted[i] or macros[i] == "wait" for i in range(2)
            ):
                break
            pair = []
            for i, macro in enumerate(macros):
                if macro == "wait" or interacted[i]:
                    pair.append(Action.STAY)
                    continue
                other = state.players[1 - i].position
                actions = nearest(
                    mdp, state.players[i].position, state.players[i].orientation,
                    targets_for(mdp, state, state.players[i], macro), {other},
                )
                if not actions:
                    pair.append(Action.STAY)
                else:
                    pair.append(actions[0])
                    progressed[i] = True
                    if actions[0] == Action.INTERACT:
                        interacted[i] = True
            state, info = step_pair(mdp, state, pair)
            t += 1
            soups += sum(info["sparse_reward_by_agent"]) / 20
        for i, macro in enumerate(macros):
            if macro != "wait" and not progressed[i]:
                failed += 1
        if stop_at_horizon and t >= HORIZON:
            break
    if stop_at_horizon:
        while t < HORIZON:
            state, info = step_pair(mdp, state, [Action.STAY, Action.STAY])
            t += 1
            soups += sum(info["sparse_reward_by_agent"]) / 20
    return {
        "arm": arm, "seed": seed,
        "model": "script" if scripted else client.model,
        "soups": soups, "failed_macros": failed, "contention": contention,
        "decisions": decisions, "steps": t,
    }


def scripted_macros(mdp, state):
    pot_full = False
    cooking = False
    ready = False
    for pos in mdp.get_pot_locations():
        if not state.has_object(pos):
            continue
        soup = state.get_object(pos)
        n = len(soup.ingredients)
        if soup.is_ready:
            ready = True
        elif not soup.is_idle:
            cooking = True
        elif n >= 3:
            pot_full = True
    macros = []
    for i, p in enumerate(state.players):
        held = p.held_object.name if p.held_object is not None else None
        if held in ("soup", "dish"):
            macros.append("serve")
        elif held == "onion":
            macros.append("pot")
        elif ready and held is None:
            macros.append("dish")
        elif pot_full and held is None:
            macros.append("pot")
        elif cooking:
            macros.append("wait")
        elif i == 0:
            macros.append("onion")
        else:
            macros.append("wait")
    return macros


def decide(rows):
    got = [r for r in rows if r["model"] != "script"]
    if len(got) < len(ARMS) * 2:
        return "INCOMPLETE"
    totals = {arm: sum(r["soups"] for r in got if r["arm"] == arm) for arm in ARMS}
    if all(v == 0 for v in totals.values()):
        return "STOP_FLAT"
    span = max(totals.values()) - min(totals.values())
    locks = sum(r["contention"] for r in got if r["arm"] == "lock")
    if span <= 1 and locks <= 1:
        return "STOP"
    return "CANDIDATE"


def smoke():
    mdp = OvercookedGridworld.from_layout_name(LAYOUT)
    rec = run_episode(mdp, "independent", 0, None, scripted=True)
    print("scripted", rec)
    assert rec["soups"] >= 1, rec
    print("smoke ok")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--decisions", type=int, default=MAX_DECISIONS)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    if args.smoke:
        return smoke()
    clients = load_clients()
    if "qwen" not in clients or "deepseek" not in clients:
        print("missing keys")
        return 2
    mdp = OvercookedGridworld.from_layout_name(LAYOUT)
    out_path = args.out
    rows = []
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    done = {(r["arm"], r["seed"]) for r in rows}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as out:
        for arm in ARMS:
            for seed in (0, 1):
                if (arm, seed) in done:
                    continue
                client = clients["qwen"] if seed % 2 == 0 else clients["deepseek"]
                print(f"\n== {arm} seed {seed} {client.name} decisions={args.decisions} ==", flush=True)
                rec = run_episode(
                    mdp, arm, seed, client,
                    max_decisions=args.decisions,
                    stop_at_horizon=args.decisions <= MAX_DECISIONS,
                )
                print(f"  soups={rec['soups']} decisions={rec['decisions']} steps={rec['steps']} failed_macros={rec['failed_macros']} contention={rec['contention']}", flush=True)
                out.write(json.dumps(rec) + "\n")
                out.flush()
                rows.append(rec)
    print("\n================ cramped_room ================")
    for arm in ARMS:
        got = [r for r in rows if r["arm"] == arm]
        print(f"{arm:<12} soups {sum(r['soups'] for r in got)} failed {sum(r['failed_macros'] for r in got)} contention {sum(r['contention'] for r in got)}")
    print("判定:", decide(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
