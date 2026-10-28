"""P1-3 L5 订阅策略四臂消融（端到端接入主协议，真实 LLM）。

研究问题：观察经济学（L5）接入真实协议后，四种订阅策略的成本-收益如何？

场景：T1 争用。A = 脚本 agent（确定性），先把 apple1 从 (2,3) **搬走**（不送达），
B = 被测 LLM agent，目标把 apple1 送到 (0,4)。B 在 t=2 被 DENY（A 持租约），
等 A 释放后获得授权——此时 apple1 已被 A 挪走，B 的计划**必然过期**。

四臂（B 的订阅策略，唯一干预变量）：
  never     不订阅：过期计划直接提交 -> 算子失败 -> L3 被动重规划（现行默认）
  always    被拒即订阅；提交前检测到计划过期 -> 主动刷新（一次 LLM 重规划，零失败算子）
  threshold 同 always，但按 Beta 后验 (post_k+1)/(post_n+2) < p* 时退订
            （低波动 key 不值得订阅——E3 判据的端到端版）
  oracle    不订阅（全知上界）：无需消息即知过期，仍主动刷新

两组波动率：single（A 搬 1 次）/ multi（A 搬 3 次）。

用法：python run_l5_ablation.py [--trials 10]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

from llm_agent import LLMAgent, load_clients
from run_e1_full import TASKS, check_success
from testbed import Sim, ScriptedAgent, fmt_pos, init_world

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))

SPEC = TASKS["T1"]                       # 5x5, apple1@(2,3), drop (0,4)
ITEM_KEY = "item:apple1"
DROP = SPEC["drop"]
GOAL = f"把 {ITEM_KEY} 从它所在位置取起，送到 {fmt_pos(DROP)} 并 place。"


def m(dx, dy, aid):
    return ("move", {"agent": aid, "dx": dx, "dy": dy})


def grab(aid):
    return ("grab", {"agent": aid, "key": ITEM_KEY})


def place(aid):
    return ("place", {"agent": aid, "key": ITEM_KEY})


def a_plan(variant: str) -> list:
    """A（搬运工）的确定性计划：把 apple1 挪走但**不**送到投放点。"""
    to_item = [m(1, 0, "A"), m(1, 0, "A"), m(0, 1, "A"), m(0, 1, "A"), m(0, 1, "A")]
    if variant == "single":       # (2,3) -> (3,3)：1 次变化
        return to_item + [grab("A"), m(1, 0, "A"), place("A")]
    # multi：(2,3)->(3,3)->(1,1)->(3,3)：3 次变化
    return (to_item + [grab("A"), m(1, 0, "A"), place("A"),
                       grab("A"), m(-1, 0, "A"), m(-1, 0, "A"),
                       m(0, -1, "A"), m(0, -1, "A"), place("A"),
                       grab("A"), m(1, 0, "A"), m(1, 0, "A"),
                       m(0, 1, "A"), m(0, 1, "A"), place("A")])


def run_trial(policy: str, variant: str, trial: int, client) -> dict:
    sim = Sim()
    init_world(sim.arbiter, dict(SPEC["items"]), dict(SPEC["agents"]),
               width=SPEC["W"], height=SPEC["H"])
    a = ScriptedAgent(sim, "A", (ITEM_KEY,), a_plan(variant), ttl=30, think=2)
    a.start(1)
    b = LLMAgent(sim, "B", client, GOAL, [ITEM_KEY], SPEC["agents"]["B"],
                 sub_policy=policy)
    b.start(2)
    try:
        sim.run(SPEC["horizon"])
    except RuntimeError as e:
        print(f"  仿真异常: {e}")
    ok, why = check_success(sim, "C2", "T1")
    arb = sim.arbiter
    lease_agent: dict = {}
    for e in arb.log:
        if e[0] == "grant":
            lease_agent[e[2]] = e[3]
    b_wasted = sum(1 for e in arb.log if e[0] == "op"
                   and lease_agent.get(e[2]) == "B" and not e[5])
    return {"policy": policy, "variant": variant, "trial": trial,
            "model": client.model, "success": ok, "fail_reason": why,
            "b_tokens": client.usage["prompt_tokens"] + client.usage["completion_tokens"],
            "b_llm_calls": client.usage["calls"],
            "proactive_refresh": b.proactive_refresh,
            "reactive_replans": b.reactive_replans,
            "updates_recv": b.updates_recv,
            "completed_at": b.report.completed_at,
            "wasted_ops": b_wasted}


def summarize(runs: list[dict]) -> dict:
    def mean(key):
        vals = [r[key] for r in runs if r[key] is not None]
        return round(statistics.mean(vals), 2) if vals else None
    return {"n": len(runs),
            "success_rate": round(sum(r["success"] for r in runs) / len(runs), 2),
            "b_tokens": mean("b_tokens"),
            "b_llm_calls": mean("b_llm_calls"),
            "proactive_refresh": mean("proactive_refresh"),
            "reactive_replans": mean("reactive_replans"),
            "updates_recv": mean("updates_recv"),
            "wasted_ops": mean("wasted_ops"),
            "completion_tick": mean("completed_at")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=10)
    args = ap.parse_args()

    clients = load_clients()
    policies = ("never", "always", "threshold", "oracle")
    variants = ("single", "multi")
    result: dict = {}

    for variant in variants:
        result[variant] = {}
        for policy in policies:
            runs = []
            for t in range(args.trials):
                client = clients["qwen"] if t % 2 == 0 else clients["deepseek"]
                client.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
                runs.append(run_trial(policy, variant, t, client))
            result[variant][policy] = summarize(runs)
            result[variant][policy]["runs"] = runs   # 逐局记录（诊断用）
            s = result[variant][policy]
            print(f"[{variant}/{policy:<9}] 成功 {s['success_rate']:.0%} | "
                  f"B tokens {s['b_tokens']} | LLM调用 {s['b_llm_calls']} | "
                  f"主动刷新 {s['proactive_refresh']} | 被动重规划 {s['reactive_replans']} | "
                  f"消息 {s['updates_recv']} | 浪费算子 {s['wasted_ops']} | "
                  f"完成 tick {s['completion_tick']}")

    out = os.path.join(BASE, "l5_ablation_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"\n结果已存 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
