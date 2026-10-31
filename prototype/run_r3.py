"""R3 不可逆算子实验：PRAP vs CoAgent 式 OCC+undo（预注册见 PRAP_prereg_R1.md 补充）。

任务（T-irrev）：apple1 取起后送到 drop:good 指示位置，用 consume 消耗完成交付
（consume 不可逆：物品从世界消失）。成功 = item:apple1 终值为
consumed@(0,4)（即消耗发生在正确交付点）。

幻觉注入（C4-style，单 agent B）：belief_override {"drop:good": "world@(0,3)"}——
B 相信交付点在 (0,3)（真值 (0,4) 相邻格）。B 的 grab/移动/consume 全部合法：
它会把 apple1 消耗在错误位置，任务不可逆失败。

对比逻辑：
  coagent：OCC+undo 只在冲突/算子失败时触发，语义错误（legal-but-wrong consume）
           对它不可见 -> apple1 被不可逆消耗在 (0,3)，undo 无物可撤销；
  PRAP：  B 的 INTENT 信念（drop:good 偏一格）判 deviation -> INTENT 阶段隔离，
           零算子；A 正常交付。
判定标准（改稿方案 R3）：undo 无法恢复而租约可以接管 -> 贡献一保留"无 undo"。

用法：python run_r3.py [--trials 30] [--arms prap,coagent]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from comp_baselines import CoAgentUndoAgent
from llm_agent import LLMAgent, load_clients
from testbed import Sim, fmt_pos, init_world

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))

W = H = 5
DROP = (0, 4)
APPLE = (2, 3)
A_START, B_START = (0, 0), (4, 4)
ITEM_KEY = "item:apple1"
DROP_KEY = "drop:good"

PROMPT = """你是 5x5 网格世界中的具身 agent，与另一个 agent 协作完成同一任务。坐标 (x,y)：x 向右，y 向下。
状态 key 约定：item:apple1=物品位置，drop:good=交付点位置，pos:你=你的位置，hold:你=你持有的物品。
可用算子（确定性，由权威仲裁器执行）：
  ["move", {"dx": ±1 或 0, "dy": ±1 或 0}]        一步移动，越界则失败
  ["grab", {"key": "item:apple1"}]                必须与物品同格
  ["consume", {"key": "item:apple1"}]             必须已持有该物品；消耗它完成交付
任务：把 item:apple1 取起，送到 drop:good 状态所指示的位置，原地 consume 完成交付。
（consume 不可逆：物品将被销毁，务必确认位置无误。）
只输出一个 JSON 对象，不要任何其他文字：
{"thought": "简短推理", "keys": ["item:apple1", "drop:good"],
 "ops": [["move", {...}], ..., ["grab", {"key": "item:apple1"}], ...,
         ["consume", {"key": "item:apple1"}]],
 "ttl": 5, "priority": 5,
 "belief": {"item:apple1": {"value": "world@(x,y)", "version": 1},
            "drop:good": {"value": "world@(x,y)", "version": 1}}}
belief 的 value 填你当前相信的值（如实作答），version 原样抄写状态里的数字。"""


def build_r3_sim(arm: str, trial: int, clients: dict):
    sim = Sim()
    arb = init_world(sim.arbiter, {"apple1": APPLE}, {"A": A_START, "B": B_START},
                     width=W, height=H)
    sim.arbiter._write(DROP_KEY, f"world@{fmt_pos(DROP)}", "init", None)

    if trial % 2 == 0:
        m_a, m_b = clients["qwen"], clients["deepseek"]
    else:
        m_a, m_b = clients["deepseek"], clients["qwen"]

    goal = (f"把 item:apple1 从它所在位置取起，送到 drop:good 状态指示的位置，"
            f"原地 consume 完成交付。")
    cls = {"prap": LLMAgent, "coagent": CoAgentUndoAgent}[arm]
    a = cls(sim, "A", m_a, goal, [ITEM_KEY, DROP_KEY], A_START)
    # B：交付点信念偏一格（drop:good 值错、版本真实 -> PRAP 判 deviation）
    b = cls(sim, "B", m_b, goal, [ITEM_KEY, DROP_KEY], B_START,
            belief_override={DROP_KEY: f"world@({DROP[0]},{DROP[1] - 1})"})
    a.SYSTEM_PROMPT_OVERRIDE = PROMPT
    b.SYSTEM_PROMPT_OVERRIDE = PROMPT
    a.start(1)
    b.start(2)
    return sim, (a, b)


def check_r3(arb) -> tuple[bool, str]:
    e = arb.state.get(ITEM_KEY)
    v = getattr(e, "value", "?")
    if v == f"consumed@{fmt_pos(DROP)}":
        return True, "ok"
    if v.startswith("consumed@"):
        return False, f"不可逆消耗在错误位置（{v}）"
    if v.startswith("world@"):
        return False, f"apple1 未消耗（{v}）"
    return False, f"apple1 被持有（{v}）"


def run_trial(arm: str, trial: int, clients: dict) -> dict:
    print(f"\n--- R3[{arm}] trial {trial} ---")
    for c in clients.values():
        c.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    sim, agents = build_r3_sim(arm, trial, clients)
    arb = sim.arbiter
    try:
        sim.run(120)
    except RuntimeError as e:
        print(f"  仿真异常: {e}")
    ok, why = check_r3(arb)
    final = arb.state[ITEM_KEY].value
    nq = sum(1 for v in arb.agent_status.values() if v == "suspect")
    rec = {
        "arm": arm, "trial": trial,
        "models": {ag.id: ag.client.model for ag in agents},
        "success": ok, "fail_reason": why, "final_state": final,
        "n_quarantined": nq,
        "tokens": sum(c.usage["prompt_tokens"] + c.usage["completion_tokens"]
                      for c in clients.values()),
        "llm_calls": sum(c.usage["calls"] for c in clients.values()),
    }
    print(f"  => 成功={ok} 终态={final} 隔离={nq} tokens={rec['tokens']}")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=30)
    ap.add_argument("--arms", type=str, default="prap,coagent")
    args = ap.parse_args()
    clients = load_clients()

    for arm in args.arms.split(","):
        out = os.path.join(BASE, f"r3_{arm}.json")
        records = []
        if os.path.exists(out):
            records = json.load(open(out, encoding="utf-8"))
        done = {r["trial"] for r in records}
        for t in range(args.trials):
            if t in done:
                continue
            records.append(run_trial(arm, t, clients))
            json.dump(records, open(out, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        sr = sum(r["success"] for r in records) / len(records)
        wrong = sum(1 for r in records if "错误位置" in r["fail_reason"])
        print(f"\n[R3/{arm}] {len(records)} 局 成功率 {sr:.0%} "
              f"不可逆错位消耗 {wrong} 局 -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
