"""LLM 冒烟实验：2 个真实 LLM agent（Qwen + DeepSeek）在 PRAP 协议下争抢资源。

场景：
  1  真实争抢：A(qwen) 与 B(deepseek) 同时受命"取 apple1 送到 (0,4)"；
     验证：LLM 规划 -> INTENT -> 仲裁 -> GRANT/DENY -> COMMIT，输家等待接管。
  2  幻觉信念：给 B 一份被篡改的观察（apple1 位置错误、版本相同），
     验证 L4 通过 digest 不符判为 deviation（SDK 信念缓存路线）。
  3  挂死注入（--stuck）：A 获授后不再动作，验证 TTL 释放 + B 接管。

运行：python run_llm_smoke.py [--stuck]
前提：api_keys.json 已填 key（或设环境变量 QWEN_API_KEY / DEEPSEEK_API_KEY）。
注意：LLM 网络调用为阻塞式；虚拟时钟不反映真实延迟。
"""

from __future__ import annotations

import argparse
import sys

from llm_agent import LLMAgent, load_clients
from testbed import Sim, fmt_pos, init_world

ITEM, ITEM_POS, DROP = "apple1", (2, 3), (0, 4)
ITEM_KEY = f"item:{ITEM}"
GOAL = f"把 item:{ITEM} 从它所在位置取起，送到 (0,4) 并 place。"


def build_sim(clients, stuck_a=False, override_b=None):
    sim = Sim()
    init_world(sim.arbiter, {ITEM: ITEM_POS}, {"A": (0, 0), "B": (4, 4)})
    a = LLMAgent(sim, "A", clients["qwen"], GOAL, [ITEM_KEY], (0, 0), stuck=stuck_a)
    b = LLMAgent(sim, "B", clients["deepseek"], GOAL, [ITEM_KEY], (4, 4),
                 belief_override=override_b)
    a.start(1)
    b.start(2)
    return sim, a, b


def report(tag, sim, a, b):
    arb = sim.arbiter
    print(f"\n-- {tag} 结果 --")
    print(f"  统计: grants={arb.stats['grants']} denies={arb.stats['denies']} "
          f"expires={arb.stats['expires']} commits={arb.stats['commits']}")
    for ag in (a, b):
        r = ag.report
        print(f"  {ag.id}({ag.client.name}): granted_at={r.granted_at} "
              f"blocked_from={r.blocked_from} completed_at={r.completed_at} "
              f"verdict={r.verdict} expiry_notices={r.expiry_notifications} "
              f"LLM调用={ag.client.usage['calls']} "
              f"tokens={ag.client.usage['prompt_tokens']}+{ag.client.usage['completion_tokens']}")
    print(f"  终态 item:{ITEM} = {arb.state[ITEM_KEY].value}（目标 world@{fmt_pos(DROP)}）")
    print(f"  日志条目: {len(arb.log)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stuck", action="store_true", help="场景 3：注入 A 挂死")
    args = ap.parse_args()

    clients = load_clients()
    missing = [n for n in ("qwen", "deepseek") if n not in clients]
    if missing:
        print(f"缺少 API key：{missing}。请填写 api_keys.json 或设置环境变量后重试。")
        return 2
    print(f"客户端就绪: {[f'{c.name}({c.model})' for c in clients.values()]}")

    print("\n== 场景 1：真实争抢 ==")
    sim, a, b = build_sim(clients)
    sim.run(60)
    report("场景 1", sim, a, b)
    ok1 = a.report.completed_at is not None and b.report.granted_at is not None

    print("\n== 场景 2：幻觉信念（B 的观察被篡改：apple1 位置错误）==")
    clients2 = load_clients()
    sim2, a2, b2 = build_sim(clients2, override_b={ITEM_KEY: "world@(3,1)"})
    sim2.run(60)
    report("场景 2", sim2, a2, b2)
    ok2 = b2.last_verdict == "deviation"
    print(f"  L4 判定 B: {b2.last_verdict}（预期 deviation）")

    if args.stuck:
        print("\n== 场景 3：挂死注入（A 获授后不再动作）==")
        clients3 = load_clients()
        sim3, a3, b3 = build_sim(clients3, stuck_a=True)
        sim3.run(60)
        report("场景 3", sim3, a3, b3)
        print(f"  A 挂死，B 在 A 的 TTL 到期后接管: granted_at={b3.report.granted_at}")

    print(f"\n冒烟结果：场景1 {'通过' if ok1 else '未达标'}；场景2 L4 幻觉检测 "
          f"{'通过' if ok2 else '未达标'}")
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    sys.exit(main())
