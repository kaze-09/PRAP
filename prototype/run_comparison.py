"""E6 竞品对比批次：四臂（coagent / orch / cnp / groupchat）机制级复现对比。

设计（briefing §四 E6）：同一测试床、同任务/条件、同种子式模型交叉指派，
与 E1/E4 的 PRAP 与既有基线数字直接可比。

场景矩阵（每格 n=10）：
  T1/C2   主任务纯争用      T1/C4   主任务争用+幻觉注入
  T2/C4   换拓扑争用+幻觉

用法：
  python run_comparison.py --baseline cnp --task T1 --cond C2 --trials 10
  python run_comparison.py --all --trials 10        # 全矩阵顺序跑，逐格落盘
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

from comp_baselines import (CoAgentUndoAgent, ContractNetAgent, GroupChatAgent,
                            OrchestratorAgent)
from llm_agent import load_clients
from run_e1_full import TASKS, check_success
from testbed import Sim, fmt_pos, init_world

BASELINES = ("coagent", "orch", "cnp", "groupchat")
CELLS = [("T1", "C2"), ("T1", "C4"), ("T2", "C4")]


def build_sim(baseline: str, cond: str, clients: dict, trial: int, task: str):
    spec = TASKS[task]
    sim = Sim()
    init_world(sim.arbiter, spec["items"], spec["agents"],
               width=spec["W"], height=spec["H"])

    # 与 E1/E4 相同的模型交叉指派（trial 奇偶对调 A/B 模型，角色均衡）
    if trial % 2 == 0:
        m_a, m_b = clients["qwen"], clients["deepseek"]
    else:
        m_a, m_b = clients["deepseek"], clients["qwen"]

    drop = fmt_pos(spec["drop"])
    first_item = list(spec["items"])[0]

    def goal_for(aid: str) -> str:
        # 与 run_e1_full 一致：单物品任务两个 agent 目标相同（争用源）
        return f"把 item:{first_item} 从它所在位置取起，送到 {drop} 并 place。"

    override_b = None
    if cond == "C4":
        px, py = spec["items"][first_item]
        override_b = {f"item:{first_item}": f"world@({min(px + 1, spec['W'] - 1)},{py})"}

    cls = {"coagent": CoAgentUndoAgent, "orch": OrchestratorAgent,
           "cnp": ContractNetAgent, "groupchat": GroupChatAgent}[baseline]
    kw = {"is_orchestrator": True} if baseline == "orch" else {}

    a = cls(sim, "A", m_a, goal_for("A"), [f"item:{first_item}"],
            spec["agents"]["A"], **kw)
    b = cls(sim, "B", m_b, goal_for("B"), [f"item:{first_item}"],
            spec["agents"]["B"], belief_override=override_b)
    a.start(1)
    b.start(2)
    return sim, (a, b)


def run_trial(baseline: str, cond: str, trial: int, clients: dict,
              task: str) -> dict:
    print(f"\n--- E6[{baseline}] 任务 {task} 条件 {cond} trial {trial} ---")
    for c in clients.values():
        c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    sim, agents = build_sim(baseline, cond, clients, trial, task)
    arb = sim.arbiter
    try:
        sim.run(TASKS[task]["horizon"])
    except RuntimeError as e:
        print(f"  仿真异常: {e}")
    ok, why = check_success(sim, cond, task)

    agent_ops: dict = {}
    for e in arb.log:
        if e[0] != "op_free":
            continue
        d = agent_ops.setdefault(e[4].get("agent", "?"), {"ops": 0, "failed": 0})
        d["ops"] += 1
        if not e[5]:
            d["failed"] += 1

    extra = {}
    for ag in agents:
        d = {attr: getattr(ag, attr, None) for attr in
             ("conflicts", "undone", "undo_failed", "reassignments",
              "tenders", "chat_rounds")}
        extra[ag.id] = {k: v for k, v in d.items() if v}

    rec = {
        "baseline": baseline, "task": task, "cond": cond, "trial": trial,
        "models": {ag.id: ag.client.model for ag in agents},
        "success": ok, "fail_reason": why,
        "agent_ops": agent_ops,
        "tokens": sum(ag.client.usage["prompt_tokens"]
                      + ag.client.usage["completion_tokens"] for ag in agents),
        "llm_calls": sum(ag.client.usage["calls"] for ag in agents),
        "extra": extra,
        "op_seq": [{"agent": e[4].get("agent", "?"), "op": e[3], "ok": e[5]}
                   for e in arb.log if e[0] == "op_free"],
    }
    print(f"  => 成功={ok}{'（' + why + '）' if not ok else ''} "
          f"浪费算子={sum(d['failed'] for d in agent_ops.values())} "
          f"tokens={rec['tokens']}")
    return rec


def aggregate(records: list[dict]) -> str:
    lines = ["\n================== E6 竞品对比汇总 ==================",
             f"{'基线/任务/条件':<20}{'n':>3}{'成功率':>7}{'浪费算子':>9}"
             f"{'tokens':>8}{'LLM调用':>8}"]
    for label in sorted({f"{r['baseline']}/{r['task']}/{r['cond']}" for r in records}):
        bl, task, cond = label.split("/")
        rs = [r for r in records if r["baseline"] == bl
              and r["task"] == task and r["cond"] == cond]
        n = len(rs)
        sr = sum(r["success"] for r in rs) / n
        waste = sum(d["failed"] for r in rs for d in r["agent_ops"].values()) / n
        toks = statistics.mean([r["tokens"] for r in rs])
        calls = statistics.mean([r["llm_calls"] for r in rs])
        lines.append(f"{label:<20}{n:>3}{sr:>6.0%}{waste:>9.1f}{toks:>8.0f}{calls:>8.1f}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", choices=BASELINES, default=None)
    ap.add_argument("--task", default="T1", choices=list(TASKS))
    ap.add_argument("--cond", default="C2")
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    clients = load_clients()
    jobs = ([(bl, t, c) for bl in BASELINES for t, c in CELLS] if args.all
            else [(args.baseline, args.task, args.cond)])
    all_records = []
    for bl, task, cond in jobs:
        records = []
        out = args.out or f"comp_{bl}_{task}_{cond}.json"
        # 断点续跑：已有结果跳过对应 trial
        if os.path.exists(out):
            with open(out, encoding="utf-8") as f:
                records = json.load(f)
        done = {r["trial"] for r in records}
        for t in range(args.trials):
            if t in done:
                continue
            records.append(run_trial(bl, cond, t, clients, task))
            with open(out, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=1)
        all_records.extend(records)
        print(f"\n[{bl}/{task}/{cond}] 完成 {len(records)}/{args.trials} -> {out}")

    print(aggregate(all_records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
