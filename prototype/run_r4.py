"""R4 间歇偏置实验（预注册补充）：观察通道以 0.5 概率瞬时错位，下一次观察恢复。

研究问题：非持续故障下 L4 是否继续隔离？（预期：瞬态隔离 + 正确观察后重新
INTENT 即解除，不发生持续锁死——隔离的持续性只在持续性故障下出现。）

场景：T1 争用（5x5，apple1 (2,3)，投放 (0,4)），B 的观察通道每次观察以 0.5
概率把 apple1 偏移 +1（瞬时，可复现种子 obs_seed）。对照：E1 C4 的持续性注入
（B 永久隔离、零算子）。

用法：python run_r4.py [--trials 30] [--prob 0.5]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from llm_agent import LLMAgent, load_clients
from run_e1_full import TASKS, check_success
from testbed import Sim, fmt_pos, init_world

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))


def build_r4_sim(trial: int, prob: float, clients: dict):
    spec = TASKS["T1"]
    sim = Sim()
    init_world(sim.arbiter, spec["items"], spec["agents"],
               width=spec["W"], height=spec["H"])

    if trial % 2 == 0:
        m_a, m_b = clients["qwen"], clients["deepseek"]
    else:
        m_a, m_b = clients["deepseek"], clients["qwen"]

    first_item = list(spec["items"])[0]
    px, py = spec["items"][first_item]
    override = {f"item:{first_item}": f"world@({min(px + 1, spec['W'] - 1)},{py})"}
    drop = fmt_pos(spec["drop"])

    a = LLMAgent(sim, "A", m_a, f"把 item:{first_item} 从它所在位置取起，送到 {drop} 并 place。",
                 [f"item:{first_item}"], spec["agents"]["A"])
    b = LLMAgent(sim, "B", m_b, f"把 item:{first_item} 从它所在位置取起，送到 {drop} 并 place。",
                 [f"item:{first_item}"], spec["agents"]["B"],
                 belief_override=override, override_prob=prob,
                 obs_seed=f"r4-{prob}-{trial}-B")
    a.start(1)
    b.start(2)
    return sim, (a, b)


def run_trial(trial: int, prob: float, clients: dict) -> dict:
    print(f"\n--- R4 trial {trial} (prob={prob}) ---")
    for c in clients.values():
        c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    sim, agents = build_r4_sim(trial, prob, clients)
    arb = sim.arbiter
    try:
        sim.run(TASKS["T1"]["horizon"])
    except RuntimeError as e:
        print(f"  仿真异常: {e}")
    ok, why = check_success(sim, "C4", "T1")

    blocked = sum(1 for e in arb.log if e[0] == "intent_suspect_blocked")
    rejected = sum(1 for e in arb.log if e[0] == "commit_rejected")
    b_ops = [e for e in arb.log if e[0] == "op" and e[3] != "-"
             and e[2] in {l.lease_id for l in arb.leases.values()}]
    lease_agent = {}
    for e in arb.log:
        if e[0] == "grant":
            lease_agent[e[2]] = e[3]
    failed_by = {}
    for e in arb.log:
        if e[0] == "op":
            aid = lease_agent.get(e[2], "?")
            if not e[5]:
                failed_by[aid] = failed_by.get(aid, 0) + 1
    rec = {
        "trial": trial, "prob": prob,
        "models": {ag.id: ag.client.model for ag in agents},
        "success": ok, "fail_reason": why,
        "n_quarantined_final": sum(1 for v in arb.agent_status.values() if v == "suspect"),
        "blocked_intents": blocked, "commits_rejected": rejected,
        "failed_ops": failed_by,
        "tokens": sum(c.usage["prompt_tokens"] + c.usage["completion_tokens"]
                      for c in clients.values()),
        "llm_calls": sum(c.usage["calls"] for c in clients.values()),
    }
    print(f"  => 成功={ok} 隔离拦截={blocked} 拒提交={rejected} "
          f"失败算子={failed_by} tokens={rec['tokens']}")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=30)
    ap.add_argument("--prob", type=float, default=0.5)
    args = ap.parse_args()
    clients = load_clients()

    out = os.path.join(BASE, f"r4_transient_p{int(args.prob * 100)}.json")
    records = []
    if os.path.exists(out):
        records = json.load(open(out, encoding="utf-8"))
    done = {r["trial"] for r in records}
    for t in range(args.trials):
        if t in done:
            continue
        records.append(run_trial(t, args.prob, clients))
        json.dump(records, open(out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    sr = sum(r["success"] for r in records) / len(records)
    blocked = sum(r["blocked_intents"] for r in records)
    print(f"\n[R4] {len(records)} 局 成功率 {sr:.0%} 隔离拦截总次数 {blocked} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
