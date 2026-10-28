"""P1-2 冗余剥夺反事实实验（redundancy-stripping counterfactual）。

研究问题（审稿意见 P1-2）：C4 中健康 agent 失败，是因为幻觉邻居"污染"了它，
还是因为隔离机制**剥夺了接管救援的冗余**？

受控设计——把混淆变量全部钉死：
  - A 的计划 = T2/C4 实测中逐字节复现 5/5 的 deepseek off-by-one 计划
    （thought 正确、ops 少走一格，apple1 落在 (4,1) 而非投放点 (4,0)），
    以**脚本硬编码**离线执行——A 的行为完全确定，与 LLM 采样无关；
  - 世界、A 起始、租约参数三臂完全相同；
  - 唯一干预变量 = B 的状态：
      rescue      B 健康，可被 DENY 后重规划接管（C2 的救援冗余）
      quarantined B 被注入幻觉信念 -> L4 判 deviation -> suspect -> 永不授予
      absent      无 B
  - script 组（A、B 全脚本）：三臂结果应为确定性 1/0/0——因果链的直接展示；
  - llm 组（B 用真实 LLM，A 保持脚本）：rescue vs quarantined 两臂各 N 局
    （模型偶/奇交叉），检验真实 LLM 救援路径（DENY -> 排队 -> 授权 ->
    首计划失败 -> L3 重规划 -> 送达）与隔离下的失败。

预期：若"冗余剥夺"机制成立，则 rescue 臂成功、quarantined/absent 臂失败，
且失败时 apple1 停在同一个 (4,1)——同一错误计划，有无救援决定成败。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from llm_agent import LLMAgent, load_clients
from run_e1_full import TASKS, check_success
from testbed import Sim, ScriptedAgent, fmt_pos, init_world

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))

# ---- T2 规格（与 run_e1_full.TASKS["T2"] 一致）----
SPEC = TASKS["T2"]
ITEM_KEY = "item:apple1"
APPLE_TRUE = SPEC["items"]["apple1"]      # (1, 3)
DROP = SPEC["drop"]                        # (4, 0)
B_START = SPEC["agents"]["B"]              # (2, 0)
SHIFTED = f"world@({min(APPLE_TRUE[0] + 1, SPEC['W'] - 1)},{APPLE_TRUE[1]})"

GOAL = f"把 {ITEM_KEY} 从它所在位置取起，送到 {fmt_pos(DROP)} 并 place。"


def m(dx, dy, aid):
    return ("move", {"agent": aid, "dx": dx, "dy": dy})


def grab(key, aid):
    return ("grab", {"agent": aid, "key": key})


def place(key, aid):
    return ("place", {"agent": aid, "key": key})


# A 的确定性错误计划：T2/C4 实测 off-by-one（9 ops，全合法、零报错、错一格）
A_FAULTY_OPS = [
    m(1, 0, "A"), m(0, -1, "A"),                      # (0,4)->(1,4)->(1,3)
    grab(ITEM_KEY, "A"),                              # 正确抓取
    m(0, -1, "A"), m(0, -1, "A"),                     # (1,2)->(1,1)  [少走一格]
    m(1, 0, "A"), m(1, 0, "A"), m(1, 0, "A"),         # ->(4,1)
    place(ITEM_KEY, "A"),                             # place 在 (4,1) —— 错误落点
]

# B 的正确救援计划（脚本版）：从 (2,0) 到 (4,1) 取回苹果，送到 (4,0)
B_RESCUE_OPS = [
    m(1, 0, "B"), m(0, 1, "B"), m(1, 0, "B"),         # (2,0)->(3,0)->(3,1)->(4,1)
    grab(ITEM_KEY, "B"),
    m(0, -1, "B"),                                    # ->(4,0)
    place(ITEM_KEY, "B"),
]


def build_arm(b_mode: str, b_client=None):
    """b_mode: rescue_scripted / quarantined_scripted / absent /
               rescue_llm / quarantined_llm"""
    sim = Sim()
    init_world(sim.arbiter, dict(SPEC["items"]), dict(SPEC["agents"]),
               width=SPEC["W"], height=SPEC["H"])
    a = ScriptedAgent(sim, "A", (ITEM_KEY,), A_FAULTY_OPS, ttl=15, think=2)
    a.start(1)
    b = None
    if b_mode == "rescue_scripted":
        b = ScriptedAgent(sim, "B", (ITEM_KEY,), B_RESCUE_OPS, ttl=20, think=2)
        b.start(2)
    elif b_mode == "quarantined_scripted":
        # 与 rescue 完全相同的计划/参数，唯一差别：信念被注入（值伪造）
        b = ScriptedAgent(sim, "B", (ITEM_KEY,), B_RESCUE_OPS, ttl=20, think=2,
                          belief=[(ITEM_KEY, 1, "deadbeef")])
        b.start(2)
    elif b_mode == "rescue_llm":
        b = LLMAgent(sim, "B", b_client, GOAL, [ITEM_KEY], B_START)
        b.start(2)
    elif b_mode == "quarantined_llm":
        b = LLMAgent(sim, "B", b_client, GOAL, [ITEM_KEY], B_START,
                     belief_override={ITEM_KEY: SHIFTED})
        b.start(2)
    return sim, a, b


def run_arm(b_mode: str, b_client=None) -> dict:
    sim, a, b = build_arm(b_mode, b_client)
    try:
        sim.run(SPEC["horizon"])
    except RuntimeError as e:
        print(f"  仿真异常: {e}")
    ok, why = check_success(sim, "C2", "T2")
    arb = sim.arbiter
    return {"arm": b_mode, "success": ok, "fail_reason": why,
            "final_item": arb.state[ITEM_KEY].value,
            "b_granted_at": (b.report.granted_at if b is not None else None),
            "b_blocked_from": (b.report.blocked_from if b is not None else None),
            "b_status": arb.agent_status.get("B", "-"),
            "b_verdict": getattr(b, "last_verdict", None),
            "wasted_ops": sum(1 for e in arb.log
                              if e[0] in ("op", "op_free") and not e[5]),
            "tokens": (sum(b.client.usage["prompt_tokens"]
                           + b.client.usage["completion_tokens"]
                           for b in [b] if b is not None and hasattr(b, "client")))}


def scripted_suite() -> dict:
    print("== 反事实·脚本组（A 错误计划固定，零 LLM 噪声；每组重复 5 次验证确定性）==")
    out = {}
    for arm in ("rescue_scripted", "quarantined_scripted", "absent"):
        runs = [run_arm(arm) for _ in range(5)]
        identical = len({json.dumps(r, sort_keys=True) for r in runs}) == 1
        r = runs[0]
        out[arm] = {"success_rate": sum(x["success"] for x in runs) / len(runs),
                    "deterministic": identical, **r}
        print(f"  [{arm:<22}] 成功 {sum(x['success'] for x in runs)}/5 "
              f"终态={r['final_item']} 确定性={identical} "
              f"B状态={r['b_status']} 浪费算子={r['wasted_ops']}")
    return out


def llm_suite(trials: int) -> dict:
    print(f"\n== 反事实·LLM 组（A 脚本错误计划固定，B 真实 LLM，各 {trials} 局）==")
    clients = load_clients()
    out = {}
    for arm in ("rescue_llm", "quarantined_llm"):
        runs = []
        for t in range(trials):
            client = clients["qwen"] if t % 2 == 0 else clients["deepseek"]
            client.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
            r = run_arm(arm, b_client=client)
            r["model"] = client.model
            runs.append(r)
            print(f"  [{arm} t{t}] model={client.model:<13} 成功={int(r['success'])} "
                  f"B状态={r['b_status']} 终态={r['final_item']} "
                  f"浪费算子={r['wasted_ops']}")
        out[arm] = {"success_rate": sum(r["success"] for r in runs) / len(runs),
                    "runs": runs}
        print(f"  => [{arm}] 成功率 {out[arm]['success_rate']:.0%}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm-trials", type=int, default=10)
    ap.add_argument("--skip-llm", action="store_true")
    args = ap.parse_args()

    scripted = scripted_suite()
    llm = {} if args.skip_llm else llm_suite(args.llm_trials)

    result = {"scripted": scripted, "llm": llm}
    out = os.path.join(BASE, "counterfactual_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"\n结果已存 {out}")

    s = scripted
    print("\n===== 反事实结论 =====")
    print(f"  rescueScripted {s['rescue_scripted']['success_rate']:.0%} | "
          f"quarantinedScripted {s['quarantined_scripted']['success_rate']:.0%} | "
          f"absent {s['absent']['success_rate']:.0%}（同一份 A 错误计划）")
    if llm:
        print(f"  rescueLLM      {llm['rescue_llm']['success_rate']:.0%} | "
              f"quarantinedLLM {llm['quarantined_llm']['success_rate']:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
