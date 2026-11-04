"""CuisineWorld level_0 上四种合作接口的试点。口径见 PRAP_cuisine_protocol.md。

用法：
  python run_cuisine_modes.py --smoke
  python run_cuisine_modes.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

PRAP = os.path.dirname(os.path.abspath(__file__))
MIND = os.path.abspath(os.path.join(PRAP, "..", "..", "external", "mindagent"))
sys.path.insert(0, PRAP)
os.chdir(MIND)
sys.path.insert(0, MIND)

from llm_agent import load_clients  # noqa: E402
from overcooked import World  # noqa: E402

OUT = os.path.join(PRAP, "ccf_followup", "cuisine_modes.jsonl")
ARMS = ("independent", "dialogue", "dispatcher", "lock")
LOCS = ("storage0", "blender0", "servingtable0")
HORIZON = 12
ACTION_RE = re.compile(
    r"((?:noop|goto|put|activate|get)_agent[01](?:_[A-Za-z0-9]+)*)"
)

SYSTEM = (
    "You control cooks in a text kitchen. Reply with actions only, "
    "no explanation. One action looks like goto_agent0_storage0, "
    "get_agent0_salmon_storage0, put_agent0_salmon_blender0, "
    "activate_agent0_blender0, or noop_agent0. "
    "Locations: storage0, blender0, servingtable0. "
    "salmonMeatcake is salmon plus flour, blended, then placed on servingtable0."
)


def state_text(obs) -> str:
    agents = ", ".join(
        f"agent{a['id']} at {a['location']} holding {a['hold']}" for a in obs.agents
    )
    locs = ", ".join(
        f"{loc['id']}:{loc['content']}" for loc in obs.locations
    )
    orders = list(zip(obs.current_tasks_name, obs.current_tasks_lifetime))
    return (
        f"step {obs.current_step}/{HORIZON} orders {orders} done {obs.accomplished_tasks}\n"
        f"agents: {agents}\nplaces: {locs}"
    )


def parse_actions(text: str, n: int) -> list[str]:
    found = ACTION_RE.findall(text or "")
    out = []
    for i in range(n):
        hit = next((a for a in found if f"agent{i}" in a), None)
        out.append(hit or f"noop_agent{i}")
    return out


def location_of(action: str) -> str | None:
    for loc in LOCS:
        if action.endswith(loc):
            return loc
    return None


def apply_lock(actions: list[str]) -> tuple[list[str], int]:
    locs = [location_of(a) for a in actions]
    if locs[0] and locs[0] == locs[1] and not actions[0].startswith("noop") and not actions[1].startswith("noop"):
        actions = [actions[0], "noop_agent1"]
        return actions, 1
    return actions, 0


def ask(client, user: str) -> str:
    return client.chat(SYSTEM, user, max_tokens=120)


def choose(client, arm: str, obs, last: list[str]) -> tuple[list[str], int]:
    text = state_text(obs)
    if arm == "dispatcher":
        raw = ask(client, text + "\nAssign one action to agent0 and one to agent1.")
        actions = parse_actions(raw, 2)
        return actions, 0
    actions = []
    for i in range(2):
        extra = ""
        if arm == "dialogue":
            extra = f"\nOther agent's previous action: {last[1 - i]}"
        raw = ask(
            client,
            text + extra + f"\nYou are agent{i}. Output one action for agent{i}.",
        )
        actions.append(parse_actions(raw, 2)[i])
    hits = 0
    if arm == "lock":
        actions, hits = apply_lock(actions)
    return actions, hits


def run_trial(arm: str, seed: int, client) -> dict:
    env = World(
        recipe_filename="assets/recipe.json",
        task_filename="assets/tasks_level_final.json",
        level="level_0",
        use_task_lifetime_interval_oracle=True,
        alpha=1.0,
        beta=2.5,
        num_agents=2,
        override_agent=True,
        max_steps=HORIZON,
        seed=seed,
    )
    obs = env.reset()
    last = ["noop_agent0", "noop_agent1"]
    contention = 0
    for _ in range(HORIZON):
        actions, hits = choose(client, arm, obs, last)
        contention += hits
        state, _just_success, _info = env.step(actions)
        obs = state
        last = actions
        if state.game_over:
            break
    done_n = len(obs.accomplished_tasks)
    print(
        f"  done={done_n} failed_actions={env.failed_action_count} contention={contention}",
        flush=True,
    )
    return {
        "arm": arm,
        "seed": seed,
        "model": client.model,
        "accomplished": done_n,
        "failed_actions": env.failed_action_count,
        "contention": contention,
        "steps": env.time_step,
    }


def decide(rows: list[dict]) -> str:
    if len(rows) < len(ARMS) * 2:
        return "INCOMPLETE"
    totals = {arm: sum(r["accomplished"] for r in rows if r["arm"] == arm) for arm in ARMS}
    span = max(totals.values()) - min(totals.values())
    locks = sum(r["contention"] for r in rows if r["arm"] == "lock")
    if all(v == 0 for v in totals.values()):
        return "STOP_FLAT"
    if span <= 1 and locks <= 1:
        return "STOP"
    return "CANDIDATE"


def smoke() -> int:
    env = World(
        recipe_filename="assets/recipe.json",
        task_filename="assets/tasks_level_final.json",
        level="level_0",
        use_task_lifetime_interval_oracle=True,
        alpha=1.0,
        beta=2.5,
        num_agents=2,
        override_agent=True,
        max_steps=HORIZON,
        seed=0,
    )
    obs = env.reset()
    env.step(["noop_agent0", "noop_agent1"])
    assert "salmonMeatcake" in obs.current_tasks_name or env.all_state().current_tasks_name
    locked, hits = apply_lock(["goto_agent0_blender0", "get_agent1_flour_blender0"])
    assert hits == 1 and locked[1] == "noop_agent1"
    assert decide([
        {"arm": a, "accomplished": 0, "contention": 0} for a in ARMS for _ in range(2)
    ]) == "STOP_FLAT"
    print("smoke ok", state_text(env.all_state()))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()
    if args.smoke:
        return smoke()
    clients = load_clients()
    if "qwen" not in clients or "deepseek" not in clients:
        print("缺少 qwen 或 deepseek")
        return 2
    rows = []
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    done = {(r["arm"], r["seed"]) for r in rows}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "a", encoding="utf-8") as out:
        for arm in ARMS:
            for seed in (0, 1):
                if (arm, seed) in done:
                    continue
                client = clients["qwen"] if seed % 2 == 0 else clients["deepseek"]
                print(f"\n== {arm} seed {seed} {client.name} ==", flush=True)
                rec = run_trial(arm, seed, client)
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                rows.append(rec)
    totals = {arm: sum(r["accomplished"] for r in rows if r["arm"] == arm) for arm in ARMS}
    print("\n================ CuisineWorld level_0 ================")
    for arm in ARMS:
        got = [r for r in rows if r["arm"] == arm]
        print(
            f"{arm:<12} accomplished {totals[arm]}  "
            f"failed {sum(r['failed_actions'] for r in got)}  "
            f"contention {sum(r['contention'] for r in got)}"
        )
    print(f"判定: {decide(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
