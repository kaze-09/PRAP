"""P2 规模矩阵：2/4/8 agent × 故障率 0/10/30% × 三方案（PRAP/无协议/提示词修复）。

场景设计（可伸缩争用）：
  - N 个 agent，M = N/2 个 item，摆放在 y=3 的相邻行（(1,3)..(4,3)）——
    相邻摆放使幻觉者的 +1 位置偏移**撞上邻居的 item**（无协议下可偷取/
    错置邻居物品，PRAP 下幻觉者在 INTENT 阶段即被隔离）；
  - 每个 agent 的目标 = 交付自己分到的 item 到 (0,4)；每个 item 恰好 2 个
    争用者（成对争用，随 N 线性扩展）；
  - 故障注入：按故障率独立把 agent 变成**幻觉者**（belief_override：自己的
    item 位置 +1）——幻觉者在 PRAP 下被 L4 隔离（零算子、零干扰），在
    无协议/提示词修复下自由执行错误计划（浪费算子、可能错置邻居 item）；
  - 成功 = 全部 M 个 item 都在投放点。

方案臂：prap（LLMAgent 全协议）/ noproto（NoProtocolAgent）/ promptfix（协调感知提示词）。
矩阵：3 规模 × 3 故障率 × 3 臂 × 10 局 = 270 局；逐格落盘 + 断点续跑。

用法：python scale_campaign.py [--seeds-per-cell 10] [--cells subset]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys

from llm_agent import LLMAgent, NoProtocolAgent, load_clients
from run_e4 import PromptFixAgent
from testbed import Sim, fmt_pos, init_world

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))

W = H = 5
DROP = (0, 4)
AGENT_STARTS = [(0, 0), (4, 4), (0, 2), (4, 2), (2, 0), (2, 4), (0, 3), (4, 1)]
SCALES = (2, 4, 8)
FAULTS = (0.0, 0.10, 0.30)
ARMS = ("prap", "noproto", "promptfix")
ARMS_CLS = {"prap": LLMAgent, "noproto": NoProtocolAgent, "promptfix": PromptFixAgent,
            "leaseonly": LLMAgent}   # R5：租约仲裁但关闭 L4（l4_enabled=False）


def cell_name(scale: int, fault: float, arm: str) -> str:
    return f"scale{scale}_f{int(fault * 100)}_{arm}"


def build_scale_sim(scale: int, fault: float, arm: str, trial: int, clients: dict):
    """N agent 争 M=N/2 item 的场景；返回 (sim, agents, item_keys, client_of)。"""
    spec_items = {f"apple{i + 1}": (i + 1, 3) for i in range(scale // 2)}
    item_keys = [f"item:{k}" for k in spec_items]   # 状态 key（item: 前缀）
    sim = Sim()
    arb = init_world(sim.arbiter, spec_items,
                     {f"A{i}": AGENT_STARTS[i] for i in range(scale)},
                     width=W, height=H)

    rng = random.Random(f"{scale}-{fault}-{arm}-{trial}")
    cls = ARMS_CLS[arm]
    if arm == "leaseonly":
        sim.arbiter.l4_enabled = False   # R5：租约保留，L4 分类与隔离关闭
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
        ag = cls(sim, aid, client, goal, [my_item], AGENT_STARTS[i],
                 belief_override=override)
        agents.append(ag)
    for i, ag in enumerate(agents):
        ag.start(i + 1)
    return sim, agents, item_keys


def check_all_delivered(arb, item_keys) -> tuple[bool, str]:
    for k in item_keys:
        e = arb.state.get(k)
        if e is None or e.value != f"world@{fmt_pos(DROP)}":
            return False, f"{k} 未送达（{getattr(e, 'value', '?')}）"
    holds = [k for k, e in arb.state.items()
             if k.startswith("hold:") and e.value != "none"]
    if holds:
        return False, f"仍持有: {holds}"
    return True, "ok"


def run_cell(scale: int, fault: float, arm: str, trials: int, clients: dict,
             out_path: str) -> list[dict]:
    records = []
    if os.path.exists(out_path):   # 断点续跑
        records = json.load(open(out_path, encoding="utf-8"))
        if len(records) >= trials:
            return records
    for t in range(len(records), trials):
        for c in clients.values():
            c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
        sim, agents, item_keys = build_scale_sim(scale, fault, arm, t, clients)
        try:
            sim.run(SPEC_HORIZON)
        except RuntimeError as e:
            print(f"  仿真异常: {e}")
        ok, why = check_all_delivered(sim.arbiter, item_keys)
        arb = sim.arbiter
        n_quarantined = sum(1 for a in arb.agent_status.values() if a == "suspect")
        wasted = sum(1 for e in arb.log if e[0] in ("op", "op_free") and not e[5])
        rec = {
            "cell": cell_name(scale, fault, arm), "trial": t,
            "scale": scale, "fault": fault, "arm": arm,
            "success": ok, "fail_reason": why,
            "delivered": sum(1 for k in item_keys
                             if arb.state[k].value == f"world@{fmt_pos(DROP)}"),
            "n_items": len(item_keys),
            "n_quarantined": n_quarantined,
            "wasted_ops": wasted,
            "tokens": sum(c.usage["prompt_tokens"] + c.usage["completion_tokens"]
                          for c in clients.values()),
            "llm_calls": sum(c.usage["calls"] for c in clients.values()),
            "completion_tick": max((a.report.completed_at or 0) for a in agents),
        }
        records.append(rec)
        json.dump(records, open(out_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"  [{rec['cell']} t{t}] 成功={int(ok)} 送达 {rec['delivered']}/{rec['n_items']}"
              f" 隔离={n_quarantined} 浪费={wasted} tokens={rec['tokens']}")
    return records


SPEC_HORIZON = 120   # 多 agent 争用需要更长视野


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--scales", type=str, default="2,4,8")
    ap.add_argument("--faults", type=str, default="0,10,30")
    ap.add_argument("--arms", type=str, default=",".join(ARMS))
    args = ap.parse_args()

    scales = [int(s) for s in args.scales.split(",")]
    faults = [int(f) / 100 for f in args.faults.split(",")]
    arms = args.arms.split(",")
    clients = load_clients()

    total_cells = len(scales) * len(faults) * len(arms)
    done = 0
    for scale in scales:
        for fault in faults:
            for arm in arms:
                done += 1
                out = os.path.join(BASE, f"{cell_name(scale, fault, arm)}.json")
                print(f"\n== 格 {done}/{total_cells}: {cell_name(scale, fault, arm)} ==")
                recs = run_cell(scale, fault, arm, args.trials, clients, out)
                sr = sum(r["success"] for r in recs) / len(recs)
                print(f"  => 小计: {sr:.0%} ({len(recs)} 局)")
    print("\n全部格完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
