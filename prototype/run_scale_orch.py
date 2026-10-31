"""P2 规模矩阵 orch 臂：2/4/8 agent × 故障率 0/10/30% × n=10。

复用 scale_campaign 的场景结构（成对争用、belief_override 注入、种子公式），
唯一差别 = OrchScaleAgent（A0 兼任编排者，item 在两个争用者中分派 +
失败重分配）。用法：python run_scale_orch.py [--trials 10] [--scales 2,4,8]
[--faults 0,10,30]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

from comp_baselines import OrchScaleAgent
from llm_agent import load_clients
from scale_campaign import (AGENT_STARTS, DROP, SPEC_HORIZON, W, H,
                            cell_name, check_all_delivered)
from testbed import Sim, fmt_pos, init_world

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
FAULTS = (0.0, 0.10, 0.30)


def build_scale_sim_orch(scale: int, fault: float, trial: int, clients: dict):
    spec_items = {f"apple{i + 1}": (i + 1, 3) for i in range(scale // 2)}
    item_keys = [f"item:{k}" for k in spec_items]
    sim = Sim()
    init_world(sim.arbiter, spec_items,
               {f"A{i}": AGENT_STARTS[i] for i in range(scale)},
               width=W, height=H)

    rng = random.Random(f"{scale}-{fault}-orch-{trial}")
    agents = []
    for i in range(scale):
        aid = f"A{i}"
        my_item = item_keys[i % len(item_keys)]
        client = clients["qwen"] if (i + trial) % 2 == 0 else clients["deepseek"]
        hallucinate = (fault > 0 and rng.random() < fault)
        override = None
        if hallucinate:
            base = my_item.split(":")[1]
            px, py = spec_items[base]
            override = {my_item: f"world@({min(px + 1, W - 1)},{py})"}
        goal = f"把 {my_item} 从它所在位置取起，送到 {fmt_pos(DROP)} 并 place。"
        ag = OrchScaleAgent(sim, aid, client, goal, [my_item], AGENT_STARTS[i],
                            belief_override=override, is_orchestrator=(i == 0))
        agents.append(ag)
    # 每件物品的两个争用者：agent i 的 item = item_keys[i % M]，
    # 故 item j 的争用对 = {A{j}, A{j+M}}（M = scale//2）
    m = scale // 2
    item_candidates = {k: [f"A{j}", f"A{j + m}"] for j, k in enumerate(item_keys)}
    agents[0].setup_scale([f"A{i}" for i in range(scale)], item_candidates)
    for i, ag in enumerate(agents):
        ag.start(i + 1)
    return sim, agents, item_keys


def run_cell(scale: int, fault: float, trials: int, clients: dict,
             out_path: str) -> list[dict]:
    records = []
    if os.path.exists(out_path):
        records = json.load(open(out_path, encoding="utf-8"))
        if len(records) >= trials:
            return records
    for t in range(len(records), trials):
        for c in clients.values():
            c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
        sim, agents, item_keys = build_scale_sim_orch(scale, fault, t, clients)
        try:
            sim.run(SPEC_HORIZON)
        except RuntimeError as e:
            print(f"  仿真异常: {e}")
        ok, why = check_all_delivered(sim.arbiter, item_keys)
        arb = sim.arbiter
        wasted = sum(1 for e in arb.log if e[0] == "op_free" and not e[5])
        reassign = sum(getattr(a, "reassignments", 0) for a in agents)
        rec = {
            "cell": cell_name(scale, fault, "orch"), "trial": t,
            "scale": scale, "fault": fault, "arm": "orch",
            "success": ok, "fail_reason": why,
            "delivered": sum(1 for k in item_keys
                             if arb.state[k].value == f"world@{fmt_pos(DROP)}"),
            "n_items": len(item_keys),
            "n_quarantined": 0,
            "wasted_ops": wasted, "reassignments": reassign,
            "tokens": sum(c.usage["prompt_tokens"] + c.usage["completion_tokens"]
                          for c in clients.values()),
            "llm_calls": sum(c.usage["calls"] for c in clients.values()),
            "completion_tick": max((a.report.completed_at or 0) for a in agents),
        }
        records.append(rec)
        json.dump(records, open(out_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"  [{rec['cell']} t{t}] 成功={int(ok)} 送达 {rec['delivered']}/"
              f"{rec['n_items']} 浪费={wasted} 重分配={reassign} tokens={rec['tokens']}")
    return records


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--scales", type=str, default="2,4,8")
    ap.add_argument("--faults", type=str, default="0,10,30")
    args = ap.parse_args()
    scales = [int(s) for s in args.scales.split(",")]
    faults = [int(f) / 100 for f in args.faults.split(",")]
    clients = load_clients()

    cells = [(s, f) for s in scales for f in faults]
    for i, (s, f) in enumerate(cells, 1):
        out = os.path.join(BASE, f"{cell_name(s, f, 'orch')}.json")
        print(f"\n== 格 {i}/{len(cells)}: {cell_name(s, f, 'orch')} ==")
        recs = run_cell(s, f, args.trials, clients, out)
        sr = sum(r["success"] for r in recs) / len(recs)
        print(f"  => 小计: {sr:.0%} ({len(recs)} 局)")
    print("\norch 臂全部格完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
