"""规划一致性：声明后置条件是否只躲开 T2 的那条路。

口径见 CCF_FOLLOWUP_PROTOCOL.md §D。单 agent，不注入，仲裁器不检查 effect。

用法：
  python run_plan_consistency.py --self-check
  python run_plan_consistency.py --trials 20
  python run_plan_consistency.py --layout T2 --arm plain --trials 20
  python run_plan_consistency.py --report
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from llm_agent import LLMAgent, load_clients, system_prompt
from run_e1_full import make_goal
from testbed import Sim, fmt_pos, init_world

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "ccf_followup")

LAYOUTS = {
    "T2": {"W": 5, "H": 5, "start": (0, 4), "item": (1, 3), "drop": (4, 0), "horizon": 80},
    "T1": {"W": 5, "H": 5, "start": (0, 0), "item": (2, 3), "drop": (0, 4), "horizon": 80},
    "T4": {"W": 7, "H": 7, "start": (0, 0), "item": (3, 5), "drop": (0, 6), "horizon": 100},
    "LONG": {"W": 7, "H": 7, "start": (0, 6), "item": (0, 0), "drop": (6, 0), "horizon": 100},
}
ARMS = ("plain", "declare")
ITEM = "item:apple1"
EFFECT_NOTE = """

另外必须在 JSON 里加一项 effect：你的 ops 全部执行完以后，keys 里每个 key 应该等于什么值，
写法与状态里完全一致，例如 {"effect": {"item:xxx": "world@(x,y)"}}。"""


def cell_path(layout: str, arm: str) -> str:
    return os.path.join(OUT_DIR, f"plan_{layout}_{arm}.json")


def simulate_ops(ops: list, start: tuple) -> dict:
    x, y = start
    grab_at = place_at = None
    after_grab: list[str] = []
    grabbed = False
    for op in ops:
        name, args = op[0], op[1]
        if name == "move":
            dx, dy = int(args.get("dx", 0)), int(args.get("dy", 0))
            x += dx
            y += dy
            if grabbed and place_at is None:
                if dx:
                    after_grab.append("H")
                if dy:
                    after_grab.append("V")
        elif name == "grab" and grab_at is None:
            grab_at = (x, y)
            grabbed = True
        elif name == "place" and place_at is None:
            place_at = (x, y)
    first_axis = after_grab[0] if after_grab else None
    return {"grab_at": grab_at, "place_at": place_at, "final": (x, y),
            "n_ops": len(ops), "after_grab_axis": first_axis}


def thought_mentions(thought: str, pos: tuple) -> bool:
    if not thought:
        return False
    pat = rf"\(\s*{pos[0]}\s*,\s*{pos[1]}\s*\)"
    return re.search(pat, thought) is not None


class PlanAgent(LLMAgent):
    def __init__(self, *args, declare: bool, width: int, height: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.SYSTEM_PROMPT_OVERRIDE = system_prompt(width, height) + (EFFECT_NOTE if declare else "")
        self.declared: list = []

    def _plan(self, obs: dict) -> dict:
        plan = super()._plan(obs)
        effect = plan.get("effect")
        if isinstance(effect, dict):
            self.declared.append({str(k): str(v).replace(" ", "") for k, v in effect.items()})
        else:
            self.declared.append(None)
        return plan


def run_trial(layout: str, arm: str, trial: int, clients: dict) -> dict:
    spec = LAYOUTS[layout]
    for c in clients.values():
        c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    client = clients["qwen"] if trial % 2 == 0 else clients["deepseek"]
    sim = Sim()
    init_world(sim.arbiter, {"apple1": spec["item"]}, {"A": spec["start"]},
               width=spec["W"], height=spec["H"])
    sim.arbiter.effect_check = "off"
    goal = make_goal(["apple1"], fmt_pos(spec["drop"]), False)
    agent = PlanAgent(sim, "A", client, goal, [ITEM], spec["start"],
                      declare=(arm == "declare"), width=spec["W"], height=spec["H"])
    agent.start(1)
    err = None
    try:
        sim.run(spec["horizon"])
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
    first = agent.plans[0] if agent.plans else {"thought": "", "ops": []}
    simu = simulate_ops(first.get("ops") or [], spec["start"])
    drop = spec["drop"]
    thought = first.get("thought") or ""
    first_ok = simu["place_at"] == drop
    gap = thought_mentions(thought, drop) and simu["place_at"] is not None and simu["place_at"] != drop
    final = sim.arbiter.state[ITEM].value
    success = final == f"world@{fmt_pos(drop)}"
    return {
        "layout": layout, "arm": arm, "trial": trial, "model": client.model,
        "error": err, "success": success, "final_item": final,
        "first_ok": first_ok,
        "first_place": list(simu["place_at"]) if simu["place_at"] else None,
        "first_grab": list(simu["grab_at"]) if simu["grab_at"] else None,
        "first_axis": simu["after_grab_axis"],
        "thought_mentions_drop": thought_mentions(thought, drop),
        "thought_ops_gap": gap,
        "thought": thought,
        "n_ops": simu["n_ops"],
        "n_plans": len(agent.plans),
        "declared_effect": agent.declared[0] if agent.declared else None,
        "tokens": client.usage["prompt_tokens"] + client.usage["completion_tokens"],
        "llm_calls": client.usage["calls"],
    }


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    from math import comb
    n = a + b + c + d
    if n == 0:
        return 1.0
    row1, col1 = a + b, a + c

    def prob(x: int) -> float:
        return comb(col1, x) * comb(n - col1, row1 - x) / comb(n, row1)

    observed = prob(a)
    lo, hi = max(0, row1 - (n - col1)), min(row1, col1)
    return min(1.0, sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= observed + 1e-12))


def holm(pairs: list[tuple[str, float]]) -> list[tuple[str, float, float, bool]]:
    ordered = sorted(pairs, key=lambda x: x[1])
    m = len(ordered)
    adj = []
    prev = 0.0
    for i, (name, p) in enumerate(ordered):
        val = min(1.0, max(prev, p * (m - i)))
        prev = val
        adj.append((name, p, val, val < 0.05))
    by_name = {n: (p, a, s) for n, p, a, s in adj}
    return [(n, *by_name[n]) for n, _ in pairs]


def load_cell(layout: str, arm: str) -> list:
    path = cell_path(layout, arm)
    if not os.path.exists(path):
        return []
    return json.load(open(path, encoding="utf-8"))


def report() -> None:
    print("== 规划一致性：首份计划的投放是否正确 ==")
    first_ok = {}
    gaps = {}
    axes = {}
    for layout in LAYOUTS:
        for arm in ARMS:
            rows = load_cell(layout, arm)
            if not rows:
                continue
            k = sum(r["first_ok"] for r in rows)
            g = sum(r["thought_ops_gap"] for r in rows)
            s = sum(r["success"] for r in rows)
            first_ok[(layout, arm)] = (k, len(rows))
            gaps[(layout, arm)] = (g, len(rows))
            by_m = {}
            for r in rows:
                by_m.setdefault(r["model"], [0, 0])
                by_m[r["model"]][0] += int(r["first_ok"])
                by_m[r["model"]][1] += 1
            axis = {}
            for r in rows:
                axis[r["first_axis"]] = axis.get(r["first_axis"], 0) + 1
            axes[(layout, arm)] = axis
            print(f"  {layout:4} {arm:7} first_ok {k}/{len(rows)}  "
                  f"gap {g}/{len(rows)}  终局 {s}/{len(rows)}  "
                  + " ".join(f"{m} {v[0]}/{v[1]}" for m, v in sorted(by_m.items()))
                  + f"  轴{axis}")
    tests = []
    for layout in LAYOUTS:
        if (layout, "declare") in first_ok and (layout, "plain") in first_ok:
            dk, dn = first_ok[(layout, "declare")]
            pk, pn = first_ok[(layout, "plain")]
            p = fisher_two_sided(dk, dn - dk, pk, pn - pk)
            tests.append((layout, p, dk, dn, pk, pn))
            print(f"  {layout}: declare {dk}/{dn} vs plain {pk}/{pn}, p={p:.4f}")
    if tests:
        holm_in = [(t[0], t[1]) for t in tests]
        print("  Holm:", {n: {"p": p, "adj": a, "sig": s} for n, p, a, s in holm(holm_in)})
    t2_plain = first_ok.get(("T2", "plain"))
    t2_dec = first_ok.get(("T2", "declare"))
    other_win = [t[0] for t in tests if t[0] != "T2" and t[2] / max(t[3], 1) > t[4] / max(t[5], 1) + 1e-9]
    t2_ok = bool(t2_plain and t2_dec and t2_dec[0] / t2_dec[1] > t2_plain[0] / t2_plain[1] + 1e-9)
    passed = t2_ok and bool(other_win)
    print(f"  门槛: T2 上 declare 更好={t2_ok}；其他布局同方向={other_win or '无'}")
    print(f"  判定: {'PASS' if passed else 'FAIL'}（只有 T2 变好则视为躲开那条路）")


def self_check() -> int:
    from run_counterfactual import A_FAULTY_OPS
    r = simulate_ops(A_FAULTY_OPS, (0, 4))
    assert r["place_at"] == (4, 1), r
    assert thought_mentions("放到(4,0)", (4, 0))
    assert not thought_mentions("放到(4,1)", (4, 0))
    print("self-check ok")
    return 0


def run_cells(layouts: list[str], arms: list[str], trials: int) -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    clients = load_clients()
    if "qwen" not in clients or "deepseek" not in clients:
        print("缺少 qwen 或 deepseek")
        return 2
    for layout in layouts:
        for arm in arms:
            path = cell_path(layout, arm)
            rows = load_cell(layout, arm)
            for t in range(len(rows), trials):
                print(f"\n== {layout} {arm} t{t} ==", flush=True)
                rec = run_trial(layout, arm, t, clients)
                rows.append(rec)
                json.dump(rows, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                print(f"  {rec['model']} first_ok={int(rec['first_ok'])} "
                      f"place={rec['first_place']} gap={int(rec['thought_ops_gap'])} "
                      f"axis={rec['first_axis']} success={int(rec['success'])}",
                      flush=True)
    report()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--layout", choices=list(LAYOUTS))
    ap.add_argument("--arm", choices=ARMS)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        return self_check()
    if args.report:
        report()
        return 0
    layouts = [args.layout] if args.layout else list(LAYOUTS)
    arms = [args.arm] if args.arm else list(ARMS)
    return run_cells(layouts, arms, args.trials)


if __name__ == "__main__":
    sys.exit(main())
